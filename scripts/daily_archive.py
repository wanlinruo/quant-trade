"""每日归档：竞价 tick -> 分钟线补齐 -> 导入校验。

**本脚本只做 QMT 链路**（竞价 tick、分钟线）。日线、股本、指数与派生表
（adj_factors / daily_flags /
market_regime / valuation_snapshot）自 2026-09-14 起全部归 `tushare_sync`，
由 `qsys-tushare-sync.timer` 每天 17:00 与 19:00 驱动。

2026-09-15 删掉了这里的 mootdx 日线分支：mootdx 的 K 线接口 2026-09-10 起
对所有服务器返回空，探针每天如实报告「源失效」，脚本据此 `return 1`——
于是这个服务从换源那天起天天亮红灯，而它的活其实干得好好的。红灯天天亮
就不再有信息量：`issues` 真的不为 0 的那天，systemd 报的还是同一个
exit-code，分辨不出来。现在退出码只反映 `issues`。

**为什么必须每天跑**：两份数据都是滚动窗口，漏跑的日子事后补不回来。
  - 分钟线：约一年，实测最早 2025-09-09
  - **竞价 tick：只有约 20 个交易日**（实测边界 2026-08-10 / 锚点 2026-09-08）
    ——这条比分钟线急一个数量级，漏三周就全丢。

在 Linux 上跑，通过 SSH 驱动 Windows 下载。**一个编排者、一份日志、
一处看失败**——两边各自定时是把「多系统拼接脆弱」这个老毛病请回来。

用法：
  uv run python scripts/daily_archive.py                    # 正常每日跑
  uv run python scripts/daily_archive.py --only-auction     # 只做竞价 tick
  uv run python scripts/daily_archive.py --lookback-days 30 # 补几天的缺口
"""

from __future__ import annotations

import argparse
import json
import selectors
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl
import structlog

from qsys.core.calendar import last_closed_session
from qsys.data.pipeline.auction import (
    import_auction_ticks,
    missing_auction_days,
)
from qsys.data.pipeline.candidates import (
    candidate_stock_days,
    drop_suspended,
    held_stock_days,
    write_manifest,
)
from qsys.data.pipeline.instruments import alive_on
from qsys.data.pipeline.canonical_publish import publish_minutes
from qsys.data.pipeline.minutes import ImportResult, import_minute_bars, missing_stock_days
from qsys.data.pipeline.premarket_suspensions import sync_premarket_suspensions
from qsys.data.providers.tushare import TushareProvider
from qsys.data.providers.tushare_client import TushareClient
from qsys.data.quality.minute_rules import MinuteIssue, check_minute_bars
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.live.config import load_tushare_config
from qsys.ops.services import FAILED, SKIPPED, SUCCEEDED, ServiceRun
from qsys.paper.calendar import load_trading_calendar

log = structlog.get_logger("daily_archive")
PROGRESS_PREFIX = "QSYS_PROGRESS "


@dataclass(frozen=True, slots=True)
class StreamResult:
    returncode: int
    output: str
    error: str = ""


def run_streaming(
    cmd: list[str], *, what: str, timeout: int, stall_timeout: int,
    on_line=None,
) -> StreamResult:
    """逐行转发子进程输出，并区分总超时与“进程活着但没有业务进展”。"""
    started = last_progress = time.monotonic()
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        bufsize=0,
    )
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    lines: list[str] = []
    buffered = b""
    failure = ""

    def accept(raw: bytes) -> None:
        nonlocal last_progress
        line = raw.rstrip(b"\r").decode("utf-8", errors="replace")
        lines.append(line)
        progressed = bool(on_line(line)) if on_line is not None else True
        if progressed:
            last_progress = time.monotonic()

    try:
        while True:
            now = time.monotonic()
            if now - started > timeout:
                failure = f"{what} 总超时（{timeout} 秒）"
                break
            if now - last_progress > stall_timeout:
                failure = f"{what} 超过 {stall_timeout} 秒没有进度"
                break
            events = selector.select(timeout=1.0)
            for key, _ in events:
                chunk = key.fileobj.read(65_536)
                if not chunk:
                    continue
                buffered += chunk
                while b"\n" in buffered:
                    raw, buffered = buffered.split(b"\n", 1)
                    accept(raw)
            if process.poll() is not None:
                remainder = process.stdout.read()
                buffered += remainder
                for raw in buffered.splitlines():
                    accept(raw)
                break
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    finally:
        selector.close()
    if failure:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.error("step.failed", step=what, rc=process.returncode, error=failure)
        return StreamResult(124 if "总超时" in failure else 125, "\n".join(lines), failure)
    elapsed = time.monotonic() - started
    log.info("step.finished", step=what, rc=process.returncode, seconds=round(elapsed, 1))
    return StreamResult(process.returncode, "\n".join(lines))


def parse_minute_progress(line: str) -> dict | None:
    """解析 Windows 下载器协议；普通日志和畸形行都不能污染运行记录。"""
    if not line.startswith(PROGRESS_PREFIX):
        return None
    try:
        payload = json.loads(line[len(PROGRESS_PREFIX):])
    except (TypeError, ValueError):
        return None
    required = {
        "total_stock_days", "completed_stock_days", "empty_stock_days",
        "failed_symbols", "parquet_files", "last_progress_at",
        "estimated_remaining_seconds",
    }
    return payload if isinstance(payload, dict) and required <= payload.keys() else None


def minute_progress_detail(progress: dict) -> str:
    remaining = progress.get("estimated_remaining_seconds")
    eta = "未知" if remaining is None else f"{max(int(remaining), 0) // 60} 分钟"
    return (
        f"已完成 {int(progress['completed_stock_days']):,}/"
        f"{int(progress['total_stock_days']):,} 股日；"
        f"有数据 {int(progress.get('downloaded_stock_days', 0)):,}，"
        f"空 {int(progress['empty_stock_days']):,}，"
        f"失败 {int(progress['failed_symbols']):,}，"
        f"Parquet {int(progress['parquet_files']):,}；"
        f"最近推进 {progress['last_progress_at']}，预计剩余 {eta}"
    )


def _read_minute_result(path: Path, *, expected_run_id: str) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Windows 分钟结果协议版本不受支持")
    if payload.get("run_id") != expected_run_id:
        raise ValueError("Windows 分钟结果不属于本次运行")
    if not isinstance(payload.get("empty"), list) or not isinstance(payload.get("failed"), list):
        raise ValueError("Windows 分钟结果缺少 empty/failed 清单")
    return payload


def classify_minute_empties(
    warehouse: Warehouse, items: list[dict],
) -> tuple[list[tuple[str, date]], list[tuple[str, date]]]:
    """用与候选清单相同的全天停牌契约拆分权威空结果与未知空结果。"""
    keys = sorted({
        (str(item["symbol"]), datetime.strptime(str(item["dt"]), "%Y%m%d").date())
        for item in items
    })
    if not keys:
        return [], []
    frame = pl.DataFrame(keys, schema={"symbol": pl.Utf8, "dt": pl.Date}, orient="row")
    unknown_frame = drop_suspended(WarehouseQuery(warehouse), frame)
    unknown = set(unknown_frame.iter_rows())
    return [key for key in keys if key not in unknown], [key for key in keys if key in unknown]


def run(cmd: list[str], *, what: str, timeout: int = 3600) -> str:
    """跑一条外部命令，失败就抛。"""
    started = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    elapsed = time.time() - started
    if result.returncode != 0:
        log.error(
            "step.failed", step=what, rc=result.returncode,
            stderr=result.stderr[-2000:], seconds=round(elapsed, 1),
        )
        raise RuntimeError(f"{what} 失败 (rc={result.returncode})")
    log.info("step.ok", step=what, seconds=round(elapsed, 1))
    return result.stdout


def _exit_code(
    rc: int, *, auction_ok: bool, suspension_refresh_ok: bool = True,
) -> int:
    """非阻断步骤失败时继续归档，但最终必须让 systemd 看到失败。"""
    return rc or (0 if auction_ok and suspension_refresh_ok else 1)


def _paper_fills(store: Path) -> pl.DataFrame:
    """只读取模拟盘成交；库不存在时返回空表（测试环境、新节点）。"""
    columns = {"account_id": pl.Utf8, "symbol": pl.Utf8, "side": pl.Utf8,
               "qty": pl.Int64, "filled_at": pl.Utf8}
    if not store.exists():
        log.warning("archive.paper_store_missing", path=str(store))
        return pl.DataFrame(schema=columns)
    with sqlite3.connect(f"file:{store}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT account_id, symbol, side, qty, filled_at FROM paper_fills"
        ).fetchall()
    return pl.DataFrame(rows, schema=columns, orient="row")


def _full_market_stock_days(
    instruments: pl.DataFrame,
    sessions: list[date],
    boards: set[str],
) -> pl.DataFrame:
    """按上市/退市边界生成全市场应采的股票日。"""
    frames = [
        alive_on(instruments, day)
        .filter(pl.col("board").is_in(list(boards)))
        .select("symbol")
        .with_columns(pl.lit(day, dtype=pl.Date).alias("dt"))
        for day in sessions
    ]
    if not frames:
        return pl.DataFrame(schema={"symbol": pl.Utf8, "dt": pl.Date})
    return pl.concat(frames).unique().sort(["dt", "symbol"])


def _minute_wanted(
    full_market: pl.DataFrame,
    candidates: pl.DataFrame,
    held: pl.DataFrame,
) -> pl.DataFrame:
    """分钟线归档集合 = 全市场股票日 ∪ 首板次日候选 ∪ 模拟盘持仓日。"""
    return pl.concat([
        full_market.select("symbol", "dt"),
        candidates.select("symbol", "dt"),
        held.select("symbol", "dt"),
    ]) \
        .unique().sort(["dt", "symbol"])


def _restrict_minute_symbols(
    wanted: pl.DataFrame, configured: str,
) -> tuple[pl.DataFrame, set[str]]:
    """把收敛任务限制到显式证券集合；正常每日归档传空串，不改变清单。"""
    requested = {symbol.strip() for symbol in configured.split(",") if symbol.strip()}
    if not requested:
        return wanted, requested
    return wanted.filter(pl.col("symbol").is_in(sorted(requested))), requested


def _publish_canonical_minutes(
    warehouse: Warehouse,
    start: date,
    end: date,
    service_run: ServiceRun,
) -> int:
    """发布归档涉及的 canonical 分钟分区；失败向上抛出。"""
    with service_run.step(
        "canonical 分钟线", interface="分钟来源 → bar_minute_1m",
        table="bar_minute_1m",
    ) as step:
        result = publish_minutes(warehouse, start, end)
        rows = result.counts["bar_minute_1m"]
        step.rows = rows
        step.detail = f"{start}..{end} 写后逐键逐字段对账通过"
        return rows


def _wanted_date_range(wanted: pl.DataFrame, fallback: date) -> tuple[date, date]:
    """返回本轮应发布的完整日期范围；重跑也不能只发布最后一天。"""
    days = wanted["dt"].to_list()
    return (min(days), max(days)) if days else (fallback, fallback)


def _split_minute_issues(
    found: list[MinuteIssue],
) -> tuple[list[MinuteIssue], list[MinuteIssue]]:
    """精确登记的源差异保留可见性，但不阻断整批 canonical 发布。"""
    blocking = [issue for issue in found if issue.known_issue is None]
    known = [issue for issue in found if issue.known_issue is not None]
    return blocking, known


def _check_minute_days(
    warehouse: Warehouse, days: list[date], service_run: ServiceRun,
) -> int:
    """校验整段分钟分区；首次下载和无缺口重跑必须走同一道门禁。"""
    issues = 0
    known_issues = 0
    for day in days:
        blocking, known = _split_minute_issues(check_minute_bars(warehouse, day))
        issues += len(blocking)
        known_issues += len(known)
        for issue in blocking[:3]:
            print(f"  [质量] {day} {issue.symbol} {issue.rule}: {issue.detail}")
        for issue in known[:3]:
            print(f"  [已知差异 {issue.known_issue}] {day} {issue.symbol} "
                  f"{issue.rule}: {issue.detail}")
    print(f"跨源校验 {len(days)} 天，阻断问题 {issues} 个，已知差异 {known_issues} 个")
    service_run.add_step(
        "分钟线校验", status=FAILED if issues else SUCCEEDED,
        interface="仓库内规则", table="minute_bars",
        detail=f"校验 {len(days)} 天，阻断问题 {issues} 个，"
               f"已知差异 {known_issues} 个",
    )
    return issues


def _archive_auction(args, warehouse, wanted, ssh, key, stamp) -> bool:
    """竞价 tick 归档，返回是否成功。

    失败**不中断**整个归档——分钟线照做；但结果要带回 main 决定退出码。
    QMT 只留约 20 个交易日，吞掉失败等于让数据悄悄过期。
    """
    missing = missing_auction_days(warehouse, wanted)
    print(f"竞价 tick：候选 {wanted.height} 组，其中缺 {missing.height} 组")
    if missing.height == 0:
        print("竞价 tick 已齐")
        return True

    local = Path("var/auction-incoming")
    local.mkdir(parents=True, exist_ok=True)
    manifest = Path("var") / f"auction_manifest_{stamp}.csv"
    write_manifest(missing, manifest)

    win_dir = f"{args.win_root}/auction_{stamp}"
    try:
        run(ssh + [f"New-Item -ItemType Directory -Force -Path {win_dir} | Out-Null"],
            what="建竞价目录")
        run(["scp", "-i", key, "-o", "BatchMode=yes", str(manifest),
             f"{args.win_host}:{win_dir}/manifest.csv"], what="上传竞价清单")
        output = run(ssh + [
            f'$env:PYTHONIOENCODING="utf-8"; {args.win_python} -u '
            f'{args.win_auction_script} --manifest {win_dir}\\manifest.csv '
            f'--out {win_dir} --sleep 0.1'
        ], what="Windows 下载竞价 tick", timeout=7200)
        for line in output.strip().splitlines()[-3:]:
            print("  " + line)
        run(["scp", "-i", key, "-o", "BatchMode=yes",
             f"{args.win_host}:{win_dir}/*.parquet", str(local)],
            what="回传竞价 parquet")
    except Exception as exc:
        log.error("archive.auction_failed", error=str(exc))
        print(f"  [竞价 tick 失败] {exc}——继续做分钟线，最终以失败退出")
        return False

    result = import_auction_ticks(
        warehouse, local, archive_dir=Path("var/auction-done")
    )
    print(f"竞价 tick 导入 {result.files} 文件 / {result.rows:,} 笔 / "
          f"{result.errors} 错误")
    if result.errors:
        log.error("archive.auction_import_errors", errors=result.errors)
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="每日数据归档")
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--lookback-days", type=int, default=10,
                        help="全市场分钟、候选与持仓各回看几个日历日，用于补缺口")
    parser.add_argument("--boards", default="main,gem,star,bse",
                        help="每日全市场分钟归档包含的股票板块")
    parser.add_argument("--skip-minutes", action="store_true")
    parser.add_argument("--skip-auction", action="store_true")
    parser.add_argument("--only-auction", action="store_true",
                        help="只做竞价 tick。窗口只有 20 天，出问题要能单独重试")
    parser.add_argument("--win-auction-script",
                        default=r"C:\qsys\win_download_auction_ticks.py")
    parser.add_argument("--win-host", default="Administrator@118.25.42.149")
    parser.add_argument("--win-key", default="~/.ssh/id_ed25519_winnode")
    parser.add_argument("--win-python", default=r"C:\qsys\.venv\Scripts\python.exe")
    parser.add_argument("--win-script-source", default="scripts/win_download_minutes.py",
                        help="随本次任务上传到 Windows 的分钟下载器；必须来自当前版本")
    parser.add_argument("--win-root", default="C:/qsys/daily")
    # systemd 整轮上限是 7200 秒；下载器必须提前退出，给回传、导入、校验留时间。
    parser.add_argument("--minute-timeout", type=int, default=6000)
    parser.add_argument("--minute-stall-timeout", type=int, default=900,
                        help="结构化进度多久不前进就判 stalled")
    parser.add_argument(
        "--minute-symbols", default="",
        help="只收敛这些逗号分隔的证券；供收盘同步补采晚到上市标的",
    )
    parser.add_argument("--config", default="config/deploy.local.toml")
    parser.add_argument("--paper-store", default="var/workbench/workbench.sqlite3",
                        help="模拟盘库；其持仓期间的分钟线一并归档")
    parser.add_argument("--service-runs", default="var/service-runs",
                        help="运行记录目录（服务管理页读取）")
    args = parser.parse_args()

    with ServiceRun.open(Path(args.service_runs), "daily-archive") as service_run:
        return _archive(args, service_run)


def _archive(args, service_run: ServiceRun) -> int:
    warehouse = Warehouse(Path(args.warehouse))
    today = date.today()
    started = time.time()

    # --- 1. 交易日历，确定采到哪天。与其他任务同源：Tushare trade_cal ---
    with service_run.step("确定交易日", interface="Tushare trade_cal") as step:
        calendar = load_trading_calendar(args.config)
        sessions = calendar.sessions_in_range(today - timedelta(days=90), today)
        complete_through = last_closed_session(calendar, datetime.now())
        step.detail = f"归档至 {complete_through}" if complete_through else "没有已收盘的交易日"
    if complete_through is None:
        log.warning("archive.no_closed_session")
        service_run.skip("没有已收盘的交易日")
        print("没有已收盘的交易日，退出")
        return 0
    service_run.session = complete_through
    start = complete_through - timedelta(days=args.lookback_days)
    print(f"=== 归档至 {complete_through}（回看 {args.lookback_days} 天）===")

    # suspend_d 官方为“不定期更新”。盘前快照服务 09:25 的策略判断；归档前
    # 必须再刷新一次，才能吸收盘中新增记录，避免把全天停牌交给 QMT 下载后
    # 误判成数据故障。查询失败不伪装成空集合，也不阻止可恢复的竞价/分钟采集，
    # 但最终退出码保持失败，等待 #187 的部分成功收敛状态机处理。
    suspension_refresh_ok = True
    with service_run.step(
        "归档前停复牌刷新", interface="Tushare suspend_d", table="suspend_daily"
    ) as step:
        try:
            token = load_tushare_config(args.config).token
            snapshot = sync_premarket_suspensions(
                TushareProvider(TushareClient(token)), warehouse, complete_through
            )
            step.rows = snapshot.row_count
            step.detail = snapshot.detail
            if not snapshot.accepted:
                step.warn("接口响应少于已有快照，已保留已有数据；" + snapshot.detail)
        except Exception as exc:
            suspension_refresh_ok = False
            step.fail(
                f"查询失败，继续采集但不得把未知空洞解释为停牌："
                f"{type(exc).__name__}: {exc}"
            )

    # --- 2. 标的主表与日线增量 ---
    # instruments 已由 16:30 的 Tushare 同步维护（含退市股）。这里若继续调用
    # AkShare，会在每天 15:40 把真值表覆盖回不含退市股的旧源。
    instruments = WarehouseQuery(warehouse).instruments()
    if instruments.height == 0:
        raise RuntimeError("instruments 为空；先恢复 qsys-tushare-sync，不回退 AkShare")
    print(f"标的主表 {instruments.height} 只（Tushare 已落库）")
    boards = {b.strip() for b in args.boards.split(",") if b.strip()}
    alive = instruments.filter(
        pl.col("board").is_in(list(boards))
        & (pl.col("delist_date").is_null() | (pl.col("delist_date") >= start))
    )
    symbols = alive["symbol"].to_list()

    # --- 1b. 竞价 tick：**最先做**。窗口只有约 20 个交易日，而全市场日线
    # 增量要跑 5 个多小时——压在后面等于拿不可恢复的数据去赌可恢复的数据。
    # 此时 daily_flags 还没有今天，故显式传入交易日历。
    key = str(Path(args.win_key).expanduser())
    ssh = ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
           args.win_host]
    stamp = complete_through.strftime("%Y%m%d")
    auction_ok = True
    if not args.skip_auction:
        with service_run.step("竞价逐笔（首板次日）", interface="QMT 竞价逐笔（Windows）",
                              table="auction_ticks") as step:
            # 与分钟线同样扣掉全天停牌：停牌日本就没有竞价，QMT 仍会返回全零快照。
            auction_ok = _archive_auction(
                args, warehouse,
                drop_suspended(
                    WarehouseQuery(warehouse),
                    candidate_stock_days(warehouse, start, complete_through,
                                         sessions=sessions),
                ),
                ssh, key, stamp,
            )
            if not auction_ok:
                step.fail("竞价采集或导入失败；QMT 只留约 20 个交易日，需尽快补采")
    if args.only_auction:
        print(f"=== 只做竞价 tick，完成，耗时 {(time.time()-started)/60:.1f} 分钟 ===")
        return _exit_code(
            0, auction_ok=auction_ok,
            suspension_refresh_ok=suspension_refresh_ok,
        )

    # 与竞价同样显式传入交易日历：15:40 时 daily_flags 还没有今天，不传的话
    # 昨日首板找不到「下一个交易日」，今天的候选分钟线要拖到明天才下（服务质检发现）
    candidates = candidate_stock_days(warehouse, start, complete_through, sessions=sessions)
    held = held_stock_days(_paper_fills(Path(args.paper_store)), start, complete_through,
                           sessions)
    archive_sessions = [day for day in sessions if start <= day <= complete_through]
    full_market = _full_market_stock_days(instruments, archive_sessions, boards)
    wanted = _minute_wanted(full_market, candidates, held)
    # 扣掉全天停牌：QMT 对停牌股会返回 241 根前收盘价、成交量全 0，整天是虚构的。
    # 下载侧先剔除已知停牌，避免 QMT 返回整日虚构数据；晚间服务质检另用
    # Tushare 当日实际有成交集合作独立期望，不能再让下载清单自证完整。
    before = wanted.height
    wanted = drop_suspended(WarehouseQuery(warehouse), wanted)
    suspended = before - wanted.height
    wanted, requested_symbols = _restrict_minute_symbols(wanted, args.minute_symbols)
    print(f"分钟线清单：全市场股票 {full_market.height} 组，"
          f"首板次日候选 {candidates.height} 组，模拟盘持仓 {held.height} 组，"
          f"合并去重 {before} 组，扣掉全天停牌 {suspended} 组，"
          f"实取 {wanted.height} 组"
          + (f"（收敛限定 {len(requested_symbols)} 只）" if requested_symbols else ""))

    if args.skip_minutes:
        service_run.add_step("分钟线", status=SKIPPED, detail="按参数跳过")
        print("按要求跳过分钟线")
        print(f"=== 完成，耗时 {(time.time()-started)/60:.1f} 分钟 ===")
        return _exit_code(
            0, auction_ok=auction_ok,
            suspension_refresh_ok=suspension_refresh_ok,
        )

    # --- 5. 算出还缺哪些股-日的分钟线 ---
    missing = missing_stock_days(warehouse, wanted)
    print(f"全市场及补充集合 {wanted.height} 组，其中缺分钟线 {missing.height} 组")
    if missing.height == 0:
        service_run.add_step("分钟线（全市场）", status=SUCCEEDED,
                             interface="QMT 1 分钟线（Windows）", table="minute_bars", rows=0,
                             detail=f"全市场及补充集合 {wanted.height} 组，均已到货")
        print("分钟线已齐，无需下载")
        publish_start, publish_end = _wanted_date_range(wanted, complete_through)
        days = sorted(set(wanted["dt"].to_list())) or [complete_through]
        issues = _check_minute_days(warehouse, days, service_run)
        if issues:
            print(f"=== 校验失败，未发布 canonical，耗时 "
                  f"{(time.time()-started)/60:.1f} 分钟 ===")
            return _exit_code(
                1, auction_ok=auction_ok,
                suspension_refresh_ok=suspension_refresh_ok,
            )
        _publish_canonical_minutes(warehouse, publish_start, publish_end, service_run)
        print(f"=== 完成，耗时 {(time.time()-started)/60:.1f} 分钟 ===")
        return _exit_code(
            0, auction_ok=auction_ok,
            suspension_refresh_ok=suspension_refresh_ok,
        )

    # 每次运行使用独立目录，旧 result.json 不能在 SSH 中断时冒充本次结果。
    local_dir = Path("var/minutes-incoming") / service_run.run_id
    local_dir.mkdir(parents=True, exist_ok=True)
    manifest = Path("var") / f"manifest_{stamp}.csv"
    write_manifest(missing, manifest)

    win_dir = f"{args.win_root}/{stamp}"
    win_manifest = f"{win_dir}/manifest.csv"

    lo, hi = min(missing["dt"].to_list()), max(missing["dt"].to_list())
    result = ImportResult(0, 0, 0)
    download_blocked = False
    with service_run.step(
        "分钟线（全市场）", interface="QMT 1 分钟线（Windows）",
        table="minute_bars",
    ) as minute_step:
        initial_progress = {
            "status": "starting", "total_stock_days": missing.height,
            "completed_stock_days": 0, "downloaded_stock_days": 0,
            "empty_stock_days": 0, "failed_symbols": 0, "parquet_files": 0,
            "last_progress_at": datetime.now().isoformat(timespec="seconds"),
            "estimated_remaining_seconds": None,
        }
        service_run.update_step(
            minute_step, detail=minute_progress_detail(initial_progress),
            progress=initial_progress,
        )

        protocol: dict | None = None
        stream_result = StreamResult(126, "", "下载尚未启动")
        transfer_error = ""
        setup_error = ""
        source_script = Path(args.win_script_source)
        remote_script = f"{win_dir}/win_download_minutes.py"
        try:
            if not source_script.is_file():
                raise FileNotFoundError(f"分钟下载器不存在：{source_script}")
            run(ssh + [f"New-Item -ItemType Directory -Force -Path {win_dir} | Out-Null"],
                what="建 Windows 目录")
            run(["scp", "-i", key, "-o", "BatchMode=yes", str(manifest),
                 f"{args.win_host}:{win_manifest}"], what="上传清单")
            # 固定入口只负责执行；业务下载器来自当前目标版本，杜绝 Windows 旧脚本漂移。
            run(["scp", "-i", key, "-o", "BatchMode=yes", str(source_script),
                 f"{args.win_host}:{remote_script}"], what="上传分钟下载器")

            remote_windows = remote_script.replace("/", "\\")
            manifest_windows = win_manifest.replace("/", "\\")
            out_windows = win_dir.replace("/", "\\")
            download = (
                f'$env:PYTHONIOENCODING="utf-8"; {args.win_python} -u {remote_windows} '
                f'--manifest {manifest_windows} --out {out_windows} '
                f'--window-start {lo.strftime("%Y%m%d")} '
                f'--window-end {hi.strftime("%Y%m%d")} '
                f'--run-id {service_run.run_id} --sleep 0.1 --whole-window'
            )

            def on_line(line: str) -> bool:
                progress = parse_minute_progress(line)
                if progress is None:
                    print("  " + line, flush=True)
                    return False
                detail = minute_progress_detail(progress)
                service_run.update_step(
                    minute_step,
                    rows=int(progress.get("downloaded_stock_days", 0)),
                    detail=detail,
                    progress=progress,
                )
                print("  [分钟进度] " + detail, flush=True)
                return True

            stream_result = run_streaming(
                ssh + [download], what="Windows 下载分钟线",
                timeout=args.minute_timeout,
                stall_timeout=args.minute_stall_timeout,
                on_line=on_line,
            )
        except Exception as exc:
            setup_error = f"{type(exc).__name__}: {exc}"
            log.error("archive.minute_setup_failed", error=setup_error)

        # 不论下载器返回什么状态，都先回收已经原子落盘的 Parquet、续传账本和摘要。
        if not setup_error:
            try:
                run(["scp", "-i", key, "-o", "BatchMode=yes",
                     f"{args.win_host}:{win_dir}/*", str(local_dir)],
                    what="回传分钟产物")
            except Exception as exc:
                transfer_error = f"{type(exc).__name__}: {exc}"
                log.error("archive.minute_transfer_failed", error=transfer_error)

        result_path = local_dir / "result.json"
        result_error = ""
        if result_path.is_file():
            try:
                protocol = _read_minute_result(
                    result_path, expected_run_id=service_run.run_id,
                )
            except Exception as exc:
                result_error = f"{type(exc).__name__}: {exc}"
        elif not setup_error:
            result_error = "Windows 未产出 result.json（进程或传输中断）"

        # 已经取回的成功文件始终先做幂等导入；少量空洞不能浪费大批成功产物。
        result = import_minute_bars(
            warehouse, local_dir, archive_dir=Path("var/minutes-done")
        )
        print(f"导入 {result.files} 文件 / {result.rows:,} 行 / {result.errors} 错误")

        authoritative: list[tuple[str, date]] = []
        unknown: list[tuple[str, date]] = []
        failures: list[dict] = []
        if protocol is not None:
            authoritative, unknown = classify_minute_empties(
                warehouse, protocol.get("empty", [])
            )
            failures = protocol.get("failed", [])

        transport_failed = stream_result.returncode not in (0, 2)
        unexpected_rc = stream_result.returncode == 2 and not failures
        process_error = ""
        if transport_failed:
            tail = " | ".join(stream_result.output.splitlines()[-3:])[-800:]
            process_error = f"SSH/Windows 下载进程异常 rc={stream_result.returncode}"
            if tail:
                process_error += f"，末尾输出：{tail}"
        elif unexpected_rc:
            process_error = "Windows 返回 rc=2，但结果没有单股异常明细"
        download_blocked = any((
            setup_error, transfer_error, result_error, stream_result.error,
            process_error, failures, unknown, result.errors,
        ))
        samples = ", ".join(f"{symbol}:{day}" for symbol, day in unknown[:5])
        parts = [
            f"清单 {missing.height:,} 股日",
            f"导入 {result.files} 个文件/{result.rows:,} 行",
            f"权威停牌空结果 {len(authoritative)}",
            f"未知空结果 {len(unknown)}",
            f"单股异常 {len(failures)}",
            f"坏文件 {result.errors}",
        ]
        if samples:
            parts.append(f"未知样例 {samples}")
        if failures:
            parts.append("异常样例 " + ", ".join(
                f"{item.get('symbol')}: {item.get('error')}" for item in failures[:3]
            ))
        for error in (
            setup_error, stream_result.error, process_error, transfer_error, result_error,
        ):
            if error:
                parts.append(error)
        detail = "；".join(parts)
        final_progress = dict(protocol or initial_progress)
        final_progress.update({
            "status": "failed" if download_blocked else "succeeded",
            "empty_authoritative": len(authoritative),
            "empty_unknown": len(unknown),
            "imported_files": result.files,
            "imported_rows": result.rows,
            "import_errors": result.errors,
        })
        service_run.update_step(
            minute_step, rows=result.rows, detail=detail, progress=final_progress,
        )
        if download_blocked:
            minute_step.fail(detail)
        if result.errors:
            log.warning("archive.import_errors", errors=result.errors)

    # --- 7. 跨源校验 ---
    days = sorted({d for d in missing["dt"].to_list()})
    issues = _check_minute_days(warehouse, days, service_run)

    if not download_blocked and not issues:
        _publish_canonical_minutes(
            warehouse,
            min(days) if days else complete_through,
            max(days) if days else complete_through,
            service_run,
        )

    print(f"=== 完成，耗时 {(time.time()-started)/60:.1f} 分钟 ===")
    log.info("archive.done", days=len(days), rows=result.rows, issues=issues,
             minutes=round((time.time()-started)/60, 1))
    return _exit_code(
        1 if download_blocked or issues else 0,
        auction_ok=auction_ok,
        suspension_refresh_ok=suspension_refresh_ok,
    )


if __name__ == "__main__":
    sys.exit(main())
