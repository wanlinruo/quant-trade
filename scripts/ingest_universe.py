"""全市场日线采集与派生数据构建。

可中断可续传：已完成的标的记入 ingest_log，重跑时自动跳过。
5400 只标的的采集会跑很久，建议放 nohup 或 tmux 里。

用法：
  uv run python scripts/ingest_universe.py --start 2023-01-01 --end 2026-09-08
  uv run python scripts/ingest_universe.py --start 2023-01-01 --end 2024-06-30 --limit 50
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl
import structlog

from qsys.core.calendar import AShareCalendar, last_closed_session
from qsys.data.coverage import coverage
from qsys.data.pipeline.flags import build_daily_flags
from qsys.data.pipeline.instruments import build_instruments
from qsys.data.pipeline.regime import build_market_regime
from qsys.data.pipeline.universe import ingest_universe
from qsys.data.pipeline.valuation import ingest_valuation_snapshot
from qsys.data.providers.akshare_provider import AkshareProvider
from qsys.data.providers.mootdx_provider import MootdxProvider
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger("ingest")


def main() -> int:
    parser = argparse.ArgumentParser(description="全市场日线采集")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--limit", type=int, default=0, help="只采前 N 只，用于试跑")
    parser.add_argument(
        "--boards",
        default="main,gem,star",
        help="逗号分隔；默认排除北交所与 B 股",
    )
    parser.add_argument("--skip-valuation", action="store_true")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    warehouse = Warehouse(Path(args.warehouse))
    reference = AkshareProvider()

    print("=== 标的主表 ===")
    print(f"  {build_instruments(reference, warehouse)} 只")

    instruments = WarehouseQuery(warehouse).instruments()
    boards = {b.strip() for b in args.boards.split(",") if b.strip()}
    # 退市股保留：只按「在 start 之后才退市」过滤。全部剔除会造成幸存者偏差，
    # 而首板策略恰恰要扫历史上的全市场。
    alive = instruments.filter(
        pl.col("board").is_in(list(boards))
        & (pl.col("delist_date").is_null() | (pl.col("delist_date") >= start))
    )
    symbols = alive["symbol"].to_list()
    if args.limit:
        symbols = symbols[: args.limit]
    print(f"  待采集 {len(symbols)} 只（板块 {sorted(boards)}）")

    # 收盘前采到的「今日」只有半天数据：收盘价其实是采集那一刻的价格，
    # 成交量约为整日的六成。落盘会让涨停判定与生态因子一路错下去且不报错。
    sessions = reference.fetch_sessions(end - timedelta(days=60), end)
    complete_through = last_closed_session(AShareCalendar(sessions), datetime.now())
    if complete_through is None:
        print("  没有已收盘的交易日可采，退出")
        return 1
    if complete_through < end:
        print(f"  ⚠ 末日截到 {complete_through}（{end} 尚未收盘，半天的 bar 不落盘）")

    print("=== 日线采集 ===")
    started = time.time()

    def show(p):
        elapsed = time.time() - started
        rate = (p.done + p.failed) / elapsed if elapsed > 0 else 0
        eta = p.remaining / rate if rate > 0 else 0
        print(
            f"  {p.done + p.failed}/{p.total}  成功 {p.done} 失败 {p.failed}  "
            f"{rate:.1f} 只/秒  剩余约 {eta / 60:.0f} 分钟",
            flush=True,
        )

    progress = ingest_universe(
        MootdxProvider(),
        warehouse,
        symbols,
        start,
        end,
        complete_through=complete_through,
        batch_size=args.batch_size,
        on_progress=show,
    )
    print(f"  完成: 成功 {progress.done} 失败 {progress.failed}")

    if not args.skip_valuation:
        # 只有当日快照。非交易日跑会把上一交易日的数字记到今天名下，
        # 定时任务应只在交易日收盘后触发。
        print("=== 估值快照（当日）===")
        try:
            rows = ingest_valuation_snapshot(reference, warehouse, date.today())
            print(f"  {rows} 行")
        except Exception as exc:
            # 估值是附加数据，它取不到不该让已经跑了几小时的日线采集
            # 连同派生数据一起白费。
            print(f"  跳过：{type(exc).__name__}: {exc}")
            log.warning("ingest.valuation_failed", error=str(exc))

    print("=== 派生数据 ===")
    print(f"  涨跌停标记 {build_daily_flags(warehouse, start, complete_through, boards=boards)} 行")
    print(f"  市场生态因子 {build_market_regime(warehouse, start, complete_through, boards=boards)} 行")

    print("=== 覆盖情况 ===")
    for c in coverage(warehouse):
        span = f"{c.first_dt} ~ {c.last_dt}" if c.first_dt else "（无日期）"
        print(f"  {c.dataset:<20} {c.rows:>10,} 行  {c.symbols:>5} 只  {span}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
