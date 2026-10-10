#!/usr/bin/env python3
"""Backfill reusable PIT inputs for Issue #230.

Auction facts remain quarantined until a separate parity report validates them
against QMT 09:25 final snapshots.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from qsys.data.pipeline.first_board_inputs import (
    ingest_auction_open,
    ingest_industry_membership,
    ingest_moneyflow,
)
from qsys.data.providers.tushare import TushareProvider
from qsys.data.providers.tushare_client import TushareClient
from qsys.data.storage import Warehouse
from qsys.live.config import load_tushare_config


def _days(start: date, end: date) -> list[date]:
    out: list[date] = []
    day = start
    while day <= end:
        out.append(day)
        day += timedelta(days=1)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config/deploy.local.toml"))
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--moneyflow", action="store_true")
    parser.add_argument("--industry", action="store_true")
    parser.add_argument("--auction", action="store_true")
    args = parser.parse_args()
    if args.end < args.start:
        raise ValueError("end must be on or after start")
    if not (args.moneyflow or args.industry or args.auction):
        raise ValueError("select at least one of --moneyflow/--industry/--auction")

    provider = TushareProvider(TushareClient(load_tushare_config(args.config).token))
    warehouse = Warehouse(args.warehouse)
    sessions = provider.trade_cal(args.start, args.end).filter(
        pl.col("is_open").cast(pl.Utf8) == "1"
    )["cal_date"].cast(pl.Utf8).to_list()
    days = [date.fromisoformat(f"{raw[:4]}-{raw[4:6]}-{raw[6:]}") for raw in sessions]
    counts: dict[str, int] = {}
    if args.industry:
        counts["industry_membership"] = ingest_industry_membership(provider, warehouse)
    for day in days:
        if args.moneyflow:
            counts["moneyflow_daily"] = counts.get("moneyflow_daily", 0) + ingest_moneyflow(
                provider, warehouse, [day]
            )
        if args.auction:
            counts["auction_open_daily"] = counts.get("auction_open_daily", 0) + ingest_auction_open(
                provider, warehouse, [day]
            )
    for name, count in counts.items():
        print(f"{name}: {count:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
