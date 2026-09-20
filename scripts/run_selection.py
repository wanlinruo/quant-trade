"""全区间跑选股，导出每日候选，供与聚宽对账。

选股是离散的——错一只就看得出来，比对净值严格得多。

用法：
  uv run python scripts/run_selection.py --start 2023-04-01 --end 2026-09-09 \
      --out var/selection_strict.csv
  # 近似版：分钟线缺失时跳过竞价量过滤
  uv run python scripts/run_selection.py ... --no-require-auction-volume \
      --out var/selection_loose.csv
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import polars as pl

from qsys.core.calendar import AShareCalendar
from qsys.data.feed import HistoricalFeed
from qsys.data.market_feed import MarketFeed
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.data.cutover import add_facade_argument, apply_facade_argument
from qsys.data.cutover import CutoverMarketData
from qsys.strategy.structural.selection import StructuralSelection


def main() -> int:
    parser = argparse.ArgumentParser(description="全区间跑选股")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--out", default="var/selection.csv")
    parser.add_argument(
        "--no-require-auction-volume",
        dest="require_auction_volume",
        action="store_false",
        help="近似版：分钟线缺失时跳过 LOW_MAIN 的竞价量过滤",
    )
    add_facade_argument(parser)
    args = parser.parse_args()
    facade_config = apply_facade_argument(args.market_data_facade)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    warehouse = Warehouse(Path(args.warehouse))
    query = WarehouseQuery(warehouse)
    market_data = CutoverMarketData(query, facade_config)

    # 交易日历取自实际有日线的日子——比另配一份日历更不容易走样
    sessions = sorted(
        market_data.market_regime()["dt"].to_list()
    )
    if not sessions:
        print("仓库里没有生态因子，先跑 daily_archive")
        return 1
    calendar = AShareCalendar(sessions)

    selector = StructuralSelection(
        HistoricalFeed(query, calendar, market_data),
        MarketFeed(query, calendar, market_data),
        query,
        require_auction_volume=args.require_auction_volume,
    )

    window = [d for d in sessions if start <= d <= end]
    print(f"区间 {window[0]} ~ {window[-1]}，共 {len(window)} 个交易日")
    print(f"竞价量过滤：{'忠实版（必需）' if args.require_auction_volume else '近似版（缺则跳过）'}")

    started = time.time()
    for i, day in enumerate(window):
        position = sessions.index(day)
        if position == 0:
            continue
        selector.select(day, sessions[position - 1])
        if (i + 1) % 100 == 0:
            elapsed = time.time() - started
            rate = (i + 1) / elapsed
            print(
                f"  {i + 1}/{len(window)}  选中 {sum(d.allow for d in selector.decisions)}  "
                f"{rate:.1f} 天/秒  剩余约 {(len(window) - i - 1) / rate / 60:.0f} 分钟",
                flush=True,
            )

    rows = selector.decisions
    frame = pl.DataFrame(
        {
            "dt": [r.dt for r in rows],
            "symbol": [r.symbol for r in rows],
            "tag": [r.tag for r in rows],
            "state": [r.state for r in rows],
            "reason": [r.reason for r in rows],
            "allow": [r.allow for r in rows],
        }
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.write_csv(out)

    allowed = frame.filter(pl.col("allow"))
    print(f"\n判定 {frame.height} 条，通过 Gate {allowed.height} 条")
    if allowed.height:
        print(f"入选交易日 {allowed['dt'].n_unique()} 天，标的 {allowed['symbol'].n_unique()} 只")
        print(allowed.group_by("tag").len().sort("tag"))
        per_day = allowed.group_by("dt").len()["len"]
        print(f"单日入选 中位 {per_day.median():.0f}  最大 {per_day.max()}")
    if frame.height:
        print("\nGate 判定分布：")
        print(frame.group_by(["tag", "state"]).len().sort(["tag", "state"]))
    print(f"\n已写入 {out}  耗时 {(time.time()-started)/60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    sys.exit(main())
