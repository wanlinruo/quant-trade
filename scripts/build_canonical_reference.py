#!/usr/bin/env python3
"""并行构建规范基础资料并输出逐键对账摘要；不切换消费者。"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from qsys.data.pipeline.canonical_reference import (
    build_security_master,
    build_security_name_history,
    calendar_reconciliation,
    name_interval_issues,
    publish_trading_calendar,
    require_calendar_coverage,
    require_reference_sources,
    reference_reconciliation,
)
from qsys.data.providers.tushare import TushareProvider
from qsys.data.providers.tushare_client import TushareClient
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.live.config import load_tushare_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--config")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--market", default="SSE")
    args = parser.parse_args()
    if args.start > args.end:
        parser.error("--start 不得晚于 --end")

    warehouse = Warehouse(args.warehouse)
    # 必须在联网和任何写入前通过；避免三张规范表只发布一部分。
    require_reference_sources(warehouse)
    provider = TushareProvider(TushareClient(load_tushare_config(args.config).token))
    raw_calendar = provider.trade_cal(args.start, args.end, exchange=args.market)
    # 在任何规范表写入前确认来源逐自然日完整；不根据周末或节假日补行。
    require_calendar_coverage(
        raw_calendar, args.start, args.end, market=args.market
    )
    counts = {
        "security_master": build_security_master(warehouse),
        "security_name_history": build_security_name_history(warehouse),
        "trading_calendar": publish_trading_calendar(
            warehouse, raw_calendar, market=args.market
        ),
    }
    reconciled = reference_reconciliation(warehouse)
    query = WarehouseQuery(warehouse)
    calendar_diff = calendar_reconciliation(
        raw_calendar,
        query.trading_calendar(args.start, args.end, market=args.market),
        market=args.market,
    )
    reconciled["trading_calendar"] = calendar_diff
    intervals = name_interval_issues(query.security_name_history())
    report = {
        "counts": counts,
        "reconciliation": {
            name: {
                "matches": result.matches,
                "left_rows": result.left_rows,
                "right_rows": result.right_rows,
                "left_only": len(result.left_only),
                "right_only": len(result.right_only),
                "mismatched": len(result.mismatched),
            }
            for name, result in reconciled.items()
        },
        "name_intervals": {
            "overlap": sum(item.kind == "overlap" for item in intervals),
            "gap": sum(item.kind == "gap" for item in intervals),
        },
        "consumer_switch": "not_performed",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not all(item.matches for item in reconciled.values()):
        raise SystemExit("规范基础资料对账失败；新表已保留，未切换消费者")


if __name__ == "__main__":
    main()
