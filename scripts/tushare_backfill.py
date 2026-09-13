"""按交易日区间从 Tushare 回填 tushare_daily。

用法:
    .venv/bin/python scripts/tushare_backfill.py \
        --warehouse /data/qsys/warehouse --start 1990-12-19 --end 2026-09-11
"""

from __future__ import annotations

import argparse
import time
from datetime import date
from pathlib import Path

import polars as pl

from qsys.data.pipeline.tushare_daily import compose_daily, write_year
from qsys.data.providers.tushare import TushareProvider
from qsys.data.providers.tushare_client import TushareClient
from qsys.data.storage import Warehouse
from qsys.live.config import load_tushare_config


def trading_days(provider_client: TushareClient, start: date, end: date) -> list[date]:
    """交易日历取自 trade_cal，不自行推算——节假日规则年年变。"""
    rows = provider_client.call("trade_cal", {
        "exchange": "SSE", "start_date": start.strftime("%Y%m%d"),
        "end_date": end.strftime("%Y%m%d"), "is_open": "1",
    })
    return sorted(date.fromisoformat(
        f"{r['cal_date'][:4]}-{r['cal_date'][4:6]}-{r['cal_date'][6:]}") for r in rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--config", type=Path, default=Path("config/deploy.local.toml"))
    parser.add_argument("--sleep", type=float, default=0.15,
                        help="每个交易日之间的间隔秒数，避免触发限流")
    args = parser.parse_args()

    client = TushareClient(load_tushare_config(args.config).token)
    provider = TushareProvider(client)
    warehouse = Warehouse(args.warehouse)

    days = trading_days(client, args.start, args.end)
    print(f"共 {len(days)} 个交易日：{days[0]} ~ {days[-1]}")

    by_year: dict[int, list[pl.DataFrame]] = {}
    started = time.time()
    for i, day in enumerate(days, 1):
        frame = compose_daily(provider.daily(day), provider.daily_basic(day),
                              provider.adj_factor(day))
        if frame.height:
            by_year.setdefault(day.year, []).append(frame)
        time.sleep(args.sleep)

        # 攒满一年就落盘，避免整段历史驻留内存。
        done_years = [y for y in by_year if y < day.year]
        for y in done_years:
            write_year(warehouse, pl.concat(by_year.pop(y), how="vertical"), y)
        if i % 100 == 0:
            print(f"  {i}/{len(days)} {day} 已用 {(time.time()-started)/60:.1f} 分钟",
                  flush=True)

    for y, frames in by_year.items():
        write_year(warehouse, pl.concat(frames, how="vertical"), y)

    print(f"完成，耗时 {(time.time()-started)/60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
