"""每个定时任务的质检：它该写的表当天到齐没有、数量对不对。

由 `scripts/service_checks.py`（`qsys-service-checks.timer`，交易日 20:00）执行，
结果作为服务 `service-checks` 的运行记录落盘，服务管理页把每条检查挂回所属任务。

期望值只取直取源或运行记录，不做推断：
- 日线标的集合来自 Tushare daily；涨跌停价、ST、估值、flags 都应覆盖它；
- 停牌表当天可以真的为空，所以只认 Tushare 同步运行记录里「停牌」这一步成功；
- 服务当天**没有运行记录**（没按时执行）时，改验它该产出的数据：数据到齐且核对通过
  即判成功，备注「数据质检成功，但服务未执行」；执行了但失败的，仍判失败；
- 同一交易日早先的质检未通过、修复后重跑通过的，记「已修复」并保留当时的失败原因。
- 分钟线与竞价的应到集合与每日归档的下载清单同源（首板次日候选），扣掉直取停牌。
- 节点存储：磁盘剩余容量低于 10 GiB 判失败、低于 20 GiB 告警；保留策略到期项与未登记
  目录只告警，删除需维护者逐项批准（见 ``qsys.ops.storage``）。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

import polars as pl

from qsys.data.catalog import REGISTRY
from qsys.data.catalog.checks import CHECKS as CATALOG_CHECKS
from qsys.data.catalog.checks import Scope
from qsys.data.pipeline.auction import missing_auction_days
from qsys.data.pipeline.candidates import candidate_stock_days
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.markets.ashare import board_of
from qsys.ops.services import FAILED, REPAIRED, SKIPPED, SUCCEEDED, WARNING
from qsys.ops.storage import (
    ELIGIBLE,
    FREE_FAIL_BYTES,
    FREE_WARN_BYTES,
    GIB,
    disk_status,
    inventory,
    unregistered,
)

OK, BAD, WARN, SKIP = SUCCEEDED, FAILED, WARNING, SKIPPED

# 日线行数相对上一交易日跌幅超过 2% 视为没到齐（实测 9 月每日波动在 ±5 只以内）
DAILY_MIN_RATIO = 0.98
MINUTE_BARS_PER_DAY = 241
VOLUME_TOLERANCE_LOTS = 1.0
PRODUCTION_BOARDS = {"main", "gem", "star"}
# 指标字典应当全部被验证；全量规则必须真的跑过，不能悄悄退回成只跑增量
EXPECTED_METRIC_IDS = frozenset(REGISTRY)
FULL_SCOPE_CHECK_IDS = frozenset(
    check.id for check in CATALOG_CHECKS.values() if check.scope is Scope.FULL
)
SAMPLE_LIMIT = 20
NOT_RUN_NOTE = "数据质检成功，但服务未执行"


@dataclass(frozen=True, slots=True)
class CheckContext:
    warehouse: Warehouse
    query: WarehouseQuery
    day: date
    previous: date | None
    sessions: list[date]
    runs: list[dict]  # 当天所有服务的运行记录
    # 常驻服务自己的状态库：影子盘 var/shadow-state.sqlite3、
    # 模拟盘 var/workbench/workbench.sqlite3；不给则相关检查跳过
    shadow_state: Path | None = None
    workbench: Path | None = None
    # 节点存储：/data/qsys 与 ~/scratch；不给则存储检查跳过
    storage_base: Path | None = None
    scratch: Path | None = None

    def daily(self, day: date | None = None) -> pl.DataFrame:
        target = day or self.day
        return self.query.tushare_daily(None, target, target, columns=["symbol", "dt", "volume"])

    def last_run(self, service: str) -> dict | None:
        mine = [r for r in self.runs
                if r.get("service") == service and r.get("session") == self.day.isoformat()]
        return mine[-1] if mine else None


@dataclass(frozen=True, slots=True)
class CheckResult:
    status: str
    expected: str
    actual: str
    detail: str = ""
    rows: int | None = None


@dataclass(frozen=True, slots=True)
class ServiceCheck:
    service: str
    name: str
    table: str
    fn: Callable[[CheckContext], CheckResult]
    domain: str = "data"


def _samples(items) -> str:
    items = sorted(items)
    head = "、".join(str(i) for i in items[:SAMPLE_LIMIT])
    return head + (f" 等 {len(items)} 个" if len(items) > SAMPLE_LIMIT else "")


def _covers(ctx: CheckContext, frame: pl.DataFrame, what: str, *,
            boards: set[str] | None = None) -> CheckResult:
    daily = set(ctx.daily()["symbol"].to_list())
    if boards is not None:
        daily = {s for s in daily if board_of(s.split(".")[0]) in boards}
    if not daily:
        return CheckResult(BAD, "当日有日线", "日线为空，无法核对", rows=0)
    have = set(frame.filter(pl.col("dt") == ctx.day)["symbol"].to_list())
    missing = daily - have
    if missing:
        return CheckResult(BAD, f"覆盖日线 {len(daily)} 只", f"缺 {len(missing)} 只",
                           f"缺{what}：{_samples(missing)}", rows=len(have))
    return CheckResult(OK, f"覆盖日线 {len(daily)} 只", f"{len(have)} 只，全覆盖", rows=len(have))


def _judge_data_without_run(expected: str, data: CheckResult) -> CheckResult:
    """服务当天没有运行记录：以它该产出的数据为准，并注明服务未执行。"""
    actual = f"服务未执行；数据 {data.actual}"
    if data.status == OK:
        return CheckResult(OK, expected, actual, NOT_RUN_NOTE, rows=data.rows)
    return CheckResult(BAD, expected, actual, data.detail, rows=data.rows)


# ---- Tushare 收盘同步 ----

def _daily_arrived(ctx: CheckContext) -> CheckResult:
    today = ctx.daily().height
    if today == 0:
        return CheckResult(BAD, "当日有日线", "0 行", rows=0)
    if ctx.previous is None:
        return CheckResult(OK, "当日有日线", f"{today} 行", rows=today)
    before = ctx.daily(ctx.previous).height
    floor = int(before * DAILY_MIN_RATIO)
    status = OK if today >= floor else BAD
    return CheckResult(status, f"≥ 上一交易日 {before} 行的 {DAILY_MIN_RATIO:.0%}（{floor}）",
                       f"{today} 行", rows=today)


def _limits_cover_daily(ctx: CheckContext) -> CheckResult:
    return _covers(ctx, ctx.query.price_limits(None, ctx.day, ctx.day), "涨跌停价")


def _st_covers_daily(ctx: CheckContext) -> CheckResult:
    return _covers(ctx, ctx.query.stock_st(None, ctx.day, ctx.day), "ST 结论")


def _suspend_receipt(ctx: CheckContext) -> CheckResult:
    run = ctx.last_run("tushare-sync")
    if run is None:
        rows = ctx.query.suspensions(None, ctx.day, ctx.day).height
        # 停牌可以真的为 0 只：空表分不清「无停牌」与「没采」，不能算验证通过
        data = (CheckResult(OK, "", f"当日停牌 {rows} 只", rows=rows) if rows else
                CheckResult(BAD, "", "停牌表为空", "没有同步记录时，空表无法区分无停牌与未采集",
                            rows=0))
        return _judge_data_without_run("当日同步记录里「停牌」成功", data)
    step = next((s for s in run.get("steps", []) if s.get("table") == "suspend_daily"), None)
    if step is None:
        return CheckResult(BAD, "当日同步记录里「停牌」成功", "同步未执行到停牌这一步",
                           run.get("error", ""))
    if step.get("status") != SUCCEEDED:
        return CheckResult(BAD, "当日同步记录里「停牌」成功", f"停牌步骤 {step.get('status')}",
                           step.get("error") or step.get("detail", ""))
    rows = ctx.query.suspensions(None, ctx.day, ctx.day).height
    return CheckResult(OK, "当日同步记录里「停牌」成功", f"成功，当日停牌 {rows} 只",
                       "停牌可以真的为 0 只，所以以采集成功为准", rows=rows)


def _limit_list_arrived(ctx: CheckContext) -> CheckResult:
    rows = ctx.query.limit_list(None, ctx.day, ctx.day).height
    return CheckResult(OK if rows else BAD, "当日涨停/炸板列表非空", f"{rows} 行",
                       "" if rows else "Tushare limit_list_d 发布较晚（D-12），19:00 补跑后仍为空",
                       rows=rows)


def _index_arrived(ctx: CheckContext) -> CheckResult:
    rows = ctx.query.index_bars(None, ctx.day, ctx.day).filter(pl.col("symbol") == "000300.SH").height
    return CheckResult(OK if rows else BAD, "沪深300 当日 1 行", f"{rows} 行", rows=rows)


def _instruments_fresh(ctx: CheckContext) -> CheckResult:
    frame = ctx.query.instruments()
    as_of = frame["as_of"].max() if frame.height else None
    ok = as_of is not None and as_of >= ctx.day
    return CheckResult(OK if ok else BAD, f"主表更新日 ≥ {ctx.day}", str(as_of or "空表"),
                       rows=frame.height)


def _flags_cover_daily(ctx: CheckContext) -> CheckResult:
    return _covers(ctx, ctx.query.daily_flags(ctx.day, ctx.day), "首板等标记",
                   boards=PRODUCTION_BOARDS)


def _regime_arrived(ctx: CheckContext) -> CheckResult:
    rows = ctx.query.market_regime(ctx.day, ctx.day).height
    return CheckResult(OK if rows == 1 else BAD, "当日 1 行", f"{rows} 行", rows=rows)


def _valuation_covers_daily(ctx: CheckContext) -> CheckResult:
    return _covers(ctx, ctx.query.valuation(ctx.day, ctx.day), "估值")


def _delisting_pool(ctx: CheckContext) -> CheckResult:
    """独立重算：不调用 pipeline.delisting，直接按 namechange 区间判当天。"""
    history = ctx.query.name_history()
    instruments = ctx.query.instruments()
    delisted_before = {
        row["symbol"] for row in instruments.iter_rows(named=True)
        if row["delist_date"] is not None and row["delist_date"] < ctx.day
    } if instruments.height else set()
    expected = {
        row["symbol"] for row in history.iter_rows(named=True)
        if row["change_reason"] == "退市整理期" and row["start_date"] is not None
        and row["start_date"] <= ctx.day
        and (row["end_date"] is None or row["end_date"] >= ctx.day)
        and row["symbol"] not in delisted_before
    }
    pool = ctx.query.delisting_pool(ctx.day, ctx.day)
    actual = set(pool["symbol"].to_list())
    missing, extra = expected - actual, actual - expected
    text = f"{len(actual)} 只" + (f"：{_samples(actual)}" if actual else "")
    if missing or extra:
        detail = "；".join(part for part in (
            f"池中缺 {_samples(missing)}" if missing else "",
            f"池中多出 {_samples(extra)}" if extra else "") if part)
        return CheckResult(BAD, f"与 namechange 重算一致（{len(expected)} 只）", text, detail,
                           rows=len(actual))
    return CheckResult(OK, f"与 namechange 重算一致（{len(expected)} 只）", text, rows=len(actual))


def _metric_checks(ctx: CheckContext) -> CheckResult:
    frame = ctx.query.metric_checks()
    if frame.height == 0:
        return CheckResult(BAD, "当日跑过增量质检", "没有质检结果")
    today = frame.filter(pl.col("scope_end") == ctx.day)
    if today.height == 0:
        return CheckResult(BAD, "当日跑过增量质检", "当日没有质检结果")
    latest = today.filter(pl.col("run_id") == today["run_id"].max())
    blocking = latest.filter(~pl.col("passed") & (pl.col("severity") == "blocking"))
    if blocking.height:
        rules = set(blocking["check_id"].to_list())
        return CheckResult(WARN, "阻断级规则全部通过",
                           f"{len(rules)} 条规则未通过（{blocking.height} 条指标结果）",
                           _samples(rules), rows=latest.height)
    return CheckResult(OK, "阻断级规则全部通过", f"{latest.height} 项全部通过", rows=latest.height)


def _today_results(ctx: CheckContext) -> pl.DataFrame:
    """当日最后一次验证写下的全部结果。"""
    frame = ctx.query.metric_checks()
    if frame.height == 0:
        return frame
    today = frame.filter(pl.col("scope_end") == ctx.day)
    if today.height == 0:
        return today
    return today.filter(pl.col("run_id") == today["run_id"].max())


def _catalog_verify_ran(ctx: CheckContext) -> CheckResult:
    """没有运行记录时改验它该产出的结果，与其他任务同一口径。"""
    run = ctx.last_run("catalog-verify")
    if run is None:
        results = _today_results(ctx)
        data = (CheckResult(OK, "", f"当日 {results.height} 条结果", rows=results.height)
                if results.height else
                CheckResult(BAD, "", "当日没有验证结果", rows=0))
        return _judge_data_without_run("当日跑过指标字典验证", data)
    step = next((s for s in run.get("steps", []) if s.get("table") == "metric_checks"), None)
    if step is None or step.get("status") not in (SUCCEEDED, SKIPPED):
        return CheckResult(BAD, "当日跑过指标字典验证",
                           "未执行" if step is None else f"步骤 {step.get('status')}",
                           (step or {}).get("detail", "") or run.get("error", ""))
    if step.get("status") == SKIPPED:
        # 与同步撞车时会主动跳过；结果得由同步那次的增量补上，否则不算数
        results = _today_results(ctx)
        return _judge_data_without_run(
            "当日跑过指标字典验证",
            CheckResult(OK, "", f"验证跳过（{step.get('detail', '')}），"
                        f"当日另有 {results.height} 条结果", rows=results.height)
            if results.height else CheckResult(BAD, "", "验证跳过且当日没有结果", rows=0),
        )
    rows = step.get("rows") or 0
    return CheckResult(OK, "当日跑过指标字典验证",
                       f"{step.get('finished_at', '')[11:]} 完成，{rows} 条结果", rows=rows)


def _catalog_verify_covers_metrics(ctx: CheckContext) -> CheckResult:
    results = _today_results(ctx)
    if results.height == 0:
        return CheckResult(BAD, f"覆盖字典里 {len(EXPECTED_METRIC_IDS)} 个指标",
                           "当日没有验证结果", rows=0)
    covered = set(results["metric_id"].to_list())
    missing = EXPECTED_METRIC_IDS - covered
    if missing:
        return CheckResult(BAD, f"覆盖字典里 {len(EXPECTED_METRIC_IDS)} 个指标",
                           f"缺 {len(missing)} 个", _samples(missing), rows=len(covered))
    return CheckResult(OK, f"覆盖字典里 {len(EXPECTED_METRIC_IDS)} 个指标",
                       f"{len(covered)} 个，全覆盖", rows=len(covered))


def _catalog_verify_ran_full_scope(ctx: CheckContext) -> CheckResult:
    """全量规则从 2026-09-18 起才纳入每日执行，退回只跑增量必须能被发现。"""
    results = _today_results(ctx)
    if results.height == 0:
        return CheckResult(BAD, f"{len(FULL_SCOPE_CHECK_IDS)} 条全量规则都执行",
                           "当日没有验证结果", rows=0)
    ran = set(results["check_id"].to_list()) & FULL_SCOPE_CHECK_IDS
    missing = FULL_SCOPE_CHECK_IDS - ran
    if missing:
        return CheckResult(BAD, f"{len(FULL_SCOPE_CHECK_IDS)} 条全量规则都执行",
                           f"缺 {len(missing)} 条", _samples(missing), rows=len(ran))
    return CheckResult(OK, f"{len(FULL_SCOPE_CHECK_IDS)} 条全量规则都执行",
                       f"{len(ran)} 条，全部执行", rows=len(ran))


def _shadow_events(ctx: CheckContext) -> CheckResult:
    """影子盘当日的策略事件都真的跑了。

    2026-09-18：Windows 执行代理崩溃后核心取不到账户快照，09:30 的竞价事件
    被记成「事件已过期，未补跑」——心跳正常、页面看不出，直到人工发现。
    """
    expected = "当日策略事件都已执行"
    if ctx.shadow_state is None:
        return CheckResult(SKIP, expected, "未配置影子盘状态库")
    path = Path(ctx.shadow_state)
    if not path.exists():
        return CheckResult(BAD, expected, f"找不到状态库 {path}")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        rows = [dict(row) for row in con.execute(
            "SELECT event_key, phase, status, detail FROM live_events "
            "WHERE event_key LIKE ?", (f"{ctx.day}:%",),
        )]
    if not rows:
        return CheckResult(BAD, expected, "当日没有任何策略事件", rows=0)
    bad = [r for r in rows if r["status"] != "completed" or r["detail"]]
    if bad:
        return CheckResult(
            BAD, expected, f"{len(bad)} / {len(rows)} 个事件未正常执行",
            _samples(f"{r['phase']}（{r['detail'] or r['status']}）" for r in bad),
            rows=len(rows),
        )
    return CheckResult(OK, expected, f"{len(rows)} 个事件全部完成", rows=len(rows))


def _paper_events(ctx: CheckContext) -> CheckResult:
    """每个运行中的模拟账户当日都跑过策略事件。"""
    expected = "运行中的账户当日都有策略事件"
    if ctx.workbench is None:
        return CheckResult(SKIP, expected, "未配置模拟盘工作台")
    path = Path(ctx.workbench)
    if not path.exists():
        return CheckResult(BAD, expected, f"找不到工作台 {path}")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        # 生效日之后才该有事件：账户当天还没上线，没有事件是正常的
        accounts = {row["id"]: row["name"] for row in con.execute(
            "SELECT id, name FROM paper_accounts "
            "WHERE status='running' AND activation_date <= ?", (ctx.day.isoformat(),))}
        if not accounts:
            return CheckResult(SKIP, expected, "当日没有已生效且运行中的模拟账户")
        rows = [dict(row) for row in con.execute(
            "SELECT account_id, phase, status, detail FROM paper_events "
            "WHERE event_key LIKE ?", (f"{ctx.day}:%",))]
    ran = {row["account_id"] for row in rows}
    silent = {name for ident, name in accounts.items() if ident not in ran}
    bad = [r for r in rows if r["status"] != "completed" or r["detail"]]
    if silent or bad:
        problems = [f"{accounts.get(r['account_id'], r['account_id'])}："
                    f"{r['phase']}（{r['detail'] or r['status']}）" for r in bad]
        problems += [f"{name}：当日没有任何策略事件" for name in sorted(silent)]
        return CheckResult(BAD, expected,
                           f"{len(silent)} 个账户无事件，{len(bad)} 个事件未正常执行",
                           _samples(problems), rows=len(rows))
    return CheckResult(OK, expected,
                       f"{len(accounts)} 个账户共 {len(rows)} 个事件全部完成",
                       rows=len(rows))


# ---- 盘前涨跌停价 ----

def _premarket_rows(ctx: CheckContext) -> CheckResult:
    run = ctx.last_run("premarket-limits")
    if run is None:
        # 盘前没执行时，收盘同步会补上当日 stk_limit；覆盖全部日线即视为数据到齐
        return _judge_data_without_run("开盘前取到当日涨跌停价", _limits_cover_daily(ctx))
    step = next((s for s in run.get("steps", []) if s.get("table") == "price_limits_daily"), None)
    if step is None or step.get("status") != SUCCEEDED:
        return CheckResult(BAD, "开盘前取到当日涨跌停价",
                           "未执行" if step is None else f"步骤 {step.get('status')}",
                           (step or {}).get("detail", "") or run.get("error", ""))
    rows = step.get("rows") or 0
    return CheckResult(OK, "开盘前取到当日涨跌停价",
                       f"{step.get('finished_at', '')[11:]} 完成，{rows} 只", rows=rows)


# ---- 每日归档（QMT） ----

def _archive_wanted(ctx: CheckContext) -> pl.DataFrame:
    """与每日归档的下载清单同源：首板次日候选，扣掉当日直取停牌。"""
    lookback = ctx.sessions[max(0, ctx.sessions.index(ctx.day) - 10)]
    wanted = candidate_stock_days(ctx.warehouse, lookback, ctx.day, sessions=ctx.sessions)
    wanted = wanted.filter(pl.col("dt") == ctx.day)
    suspended = (ctx.query.suspensions(None, ctx.day, ctx.day)
                 .filter(pl.col("suspend_type") == "S").select(["symbol", "dt"]))
    return wanted.join(suspended, on=["symbol", "dt"], how="anti")


def _auction_arrived(ctx: CheckContext) -> CheckResult:
    wanted = _archive_wanted(ctx)
    if wanted.height == 0:
        return CheckResult(OK, "首板次日候选都有竞价", "当日没有候选", rows=0)
    missing = missing_auction_days(ctx.warehouse, wanted)
    status = BAD if missing.height else OK
    return CheckResult(status, f"候选 {wanted.height} 只", f"缺 {missing.height} 只",
                       _samples(set(missing["symbol"].to_list())) if missing.height else "",
                       rows=wanted.height - missing.height)


def _minute_bars(ctx: CheckContext) -> tuple[pl.DataFrame, pl.DataFrame]:
    wanted = _archive_wanted(ctx)
    if wanted.height == 0:
        return wanted, pl.DataFrame(schema={"symbol": pl.Utf8, "bars": pl.UInt32,
                                            "volume": pl.Int64})
    bars = ctx.query.minute_bars(
        wanted["symbol"].to_list(),
        datetime.combine(ctx.day, time(0, 0)), datetime.combine(ctx.day, time(23, 59, 59)),
    )
    per = bars.group_by("symbol").agg(pl.len().alias("bars"), pl.col("volume").sum())
    return wanted, per


def _minutes_arrived(ctx: CheckContext) -> CheckResult:
    wanted, per = _minute_bars(ctx)
    if wanted.height == 0:
        return CheckResult(OK, "首板次日候选都有分钟线", "当日没有候选", rows=0)
    missing = set(wanted["symbol"].to_list()) - set(per["symbol"].to_list())
    return CheckResult(BAD if missing else OK, f"候选 {wanted.height} 只", f"缺 {len(missing)} 只",
                       _samples(missing) if missing else "", rows=per.height)


def _minutes_complete(ctx: CheckContext) -> CheckResult:
    wanted, per = _minute_bars(ctx)
    if per.height == 0:
        return CheckResult(SKIP, f"每只 {MINUTE_BARS_PER_DAY} 根", "没有分钟线可核对")
    bad = per.filter(pl.col("bars") != MINUTE_BARS_PER_DAY)
    return CheckResult(BAD if bad.height else OK, f"每只 {MINUTE_BARS_PER_DAY} 根",
                       f"{bad.height} 只不是 {MINUTE_BARS_PER_DAY} 根",
                       _samples({f"{s}({n})" for s, n in bad.select("symbol", "bars").rows()}),
                       rows=per.height)


def _minutes_volume(ctx: CheckContext) -> CheckResult:
    wanted, per = _minute_bars(ctx)
    if per.height == 0:
        return CheckResult(SKIP, "全天成交量与日线一致", "没有分钟线可核对")
    daily = ctx.daily().select("symbol", pl.col("volume").alias("daily_volume"))
    joined = per.join(daily, on="symbol", how="left")
    no_daily = joined.filter(pl.col("daily_volume").is_null())
    bad = joined.filter((pl.col("volume") - pl.col("daily_volume")).abs() > VOLUME_TOLERANCE_LOTS)
    if no_daily.height:
        return CheckResult(BAD, "全天成交量与日线一致", f"{no_daily.height} 只没有日线可对",
                           _samples(set(no_daily["symbol"].to_list())), rows=per.height)
    return CheckResult(BAD if bad.height else OK, f"差额 ≤ {VOLUME_TOLERANCE_LOTS:g} 手",
                       f"{bad.height} 只不一致",
                       _samples({f"{s}(分钟 {v} / 日线 {d:g})"
                                 for s, v, d in bad.select("symbol", "volume", "daily_volume").rows()}),
                       rows=per.height)


# ---- 节点存储 ----

def _gib(value: int) -> str:
    return f"{value / GIB:.1f} GiB"


def _disk_capacity(ctx: CheckContext) -> CheckResult:
    expected = f"剩余 ≥ {_gib(FREE_WARN_BYTES)}（低于 {_gib(FREE_FAIL_BYTES)} 判失败）"
    if ctx.storage_base is None or not ctx.storage_base.exists():
        return CheckResult(SKIP, expected, "未提供存储根目录")
    disk = disk_status(ctx.storage_base)
    actual = f"剩余 {_gib(disk.free)} / 共 {_gib(disk.total)}（已用 {disk.used / disk.total:.0%}）"
    status = {"ok": OK, "warn": WARN, "fail": BAD}[disk.level]
    return CheckResult(status, expected, actual)


def _retention(ctx: CheckContext) -> CheckResult:
    expected = "无到期未处理的副本，无未登记目录"
    if ctx.storage_base is None or not ctx.storage_base.exists():
        return CheckResult(SKIP, expected, "未提供存储根目录")
    scratch = ctx.scratch if ctx.scratch is not None and ctx.scratch.exists() else None
    items = inventory(ctx.storage_base, scratch, datetime.now())
    eligible = [item for item in items if item.verdict == ELIGIBLE]
    stray = unregistered(ctx.storage_base)
    freeable = sum(item.exclusive_bytes for item in eligible)
    actual = f"到期 {len(eligible)} 项（可释放 {_gib(freeable)}），未登记 {len(stray)} 项"
    if not eligible and not stray:
        return CheckResult(OK, expected, actual, rows=len(items))
    parts = []
    if eligible:
        parts.append("到期可申请删除：" + _samples(
            f"{item.path.name}（{_gib(item.exclusive_bytes)}）" for item in eligible))
    if stray:
        parts.append("未登记保留策略：" + _samples(path.name for path in stray))
    return CheckResult(WARN, expected, actual, "；".join(parts), rows=len(items))


CHECKS: tuple[ServiceCheck, ...] = (
    ServiceCheck("premarket-limits", "开盘前取到当日涨跌停价", "price_limits_daily", _premarket_rows),
    ServiceCheck("daily-archive", "竞价逐笔到货", "auction_ticks", _auction_arrived),
    ServiceCheck("daily-archive", "分钟线到货", "minute_bars", _minutes_arrived),
    ServiceCheck("daily-archive", "分钟线每只 241 根", "minute_bars", _minutes_complete),
    ServiceCheck("daily-archive", "分钟线成交量对日线", "minute_bars", _minutes_volume),
    ServiceCheck("tushare-sync", "日线到货", "tushare_daily", _daily_arrived),
    ServiceCheck("tushare-sync", "涨跌停价覆盖日线", "price_limits_daily", _limits_cover_daily),
    ServiceCheck("tushare-sync", "ST 结论覆盖日线", "stock_st_daily", _st_covers_daily),
    ServiceCheck("tushare-sync", "停牌采集成功", "suspend_daily", _suspend_receipt),
    ServiceCheck("tushare-sync", "涨停列表到货", "limit_list_daily", _limit_list_arrived),
    ServiceCheck("tushare-sync", "沪深300 到货", "index_bars", _index_arrived),
    ServiceCheck("tushare-sync", "标的主表已更新", "instruments", _instruments_fresh),
    ServiceCheck("tushare-sync", "派生标记覆盖日线", "daily_flags", _flags_cover_daily),
    ServiceCheck("tushare-sync", "市场广度到货", "market_regime", _regime_arrived),
    ServiceCheck("tushare-sync", "估值覆盖日线", "valuation_snapshot", _valuation_covers_daily),
    ServiceCheck("tushare-sync", "退市整理期池与 namechange 一致", "delisting_pool", _delisting_pool),
    ServiceCheck("shadow-core", "当日策略事件已执行", "live_events", _shadow_events, "strategy"),
    ServiceCheck("paper", "当日策略事件已执行", "paper_events", _paper_events, "strategy"),
    ServiceCheck("catalog-verify", "当日验证已执行", "metric_checks", _catalog_verify_ran),
    ServiceCheck("catalog-verify", "覆盖全部指标", "metric_checks",
                 _catalog_verify_covers_metrics),
    ServiceCheck("catalog-verify", "全量规则已执行", "metric_checks",
                 _catalog_verify_ran_full_scope),
    ServiceCheck("catalog-verify", "阻断级规则全部通过", "metric_checks", _metric_checks),
    ServiceCheck("service-checks", "磁盘剩余容量", "/data/qsys", _disk_capacity),
    ServiceCheck("service-checks", "存储保留策略", "/data/qsys", _retention),
)


def step_name(check: ServiceCheck) -> str:
    """运行记录里的步骤名：`服务id｜检查名`，页面据此把结果挂回所属任务。"""
    return f"{check.service}｜{check.name}"


def parse_step_name(name: str) -> tuple[str, str] | None:
    service, sep, check = name.partition("｜")
    return (service, check) if sep else None


def earlier_failures(runs: list[dict], day: date) -> dict[str, str]:
    """同一交易日早先各次质检里未通过的检查：步骤名 → 「HH:MM 质检未通过：原因」（取最近一次）。"""
    failures: dict[str, str] = {}
    for run in runs:  # load_runs 已按开始时间排序
        if run.get("service") != "service-checks" or run.get("session") != day.isoformat():
            continue
        for step in run.get("steps", []):
            if step.get("status") == FAILED:
                failures[step["name"]] = (f"{(run.get('started_at') or '')[11:16]} 质检未通过："
                                          f"{step.get('detail', '')}")
    return failures


def mark_repaired(result: CheckResult, failure: str | None) -> CheckResult:
    """本次通过、而早先未通过：记「已修复」，保留当时的失败原因。"""
    if failure is None or result.status != OK:
        return result
    detail = "；".join(part for part in (result.detail, f"已修复（{failure}）") if part)
    return CheckResult(REPAIRED, result.expected, result.actual, detail, rows=result.rows)
