#!/usr/bin/env python3
"""生成规范切换所需的 12 张表五维健康证据。"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

from qsys.data.cutover_health import generate_cutover_health
from qsys.data.pipeline.canonical_reference import require_calendar_coverage
from qsys.data.providers.tushare import TushareProvider
from qsys.data.providers.tushare_client import TushareClient
from qsys.data.storage import Warehouse
from qsys.live.config import load_tushare_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--market", default="SSE")
    args = parser.parse_args()
    provider = TushareProvider(TushareClient(load_tushare_config(args.config).token))
    raw_calendar = provider.trade_cal(args.start, args.end, exchange=args.market)
    require_calendar_coverage(
        raw_calendar, args.start, args.end, market=args.market
    )
    summary = generate_cutover_health(
        Warehouse(args.warehouse), args.start, args.end, raw_calendar,
        market=args.market,
    )
    print(json.dumps(
        {dataset: status.value for dataset, status in summary.items()},
        ensure_ascii=False, indent=2, sort_keys=True,
    ))


if __name__ == "__main__":
    main()
