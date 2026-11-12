"""打板看板历史交易日只读汇总（#648 P1 验收）。

只读 warehouse：当日封板家数、连板高度分布、未知高度数、今/昨晋级；给出概念快照时
附题材列家数。上线前的历史日没有当日概念快照，不分题材（不得用当前成分回填）。

用法：
    python -m qsys.data.board.history_cli --warehouse /data/qsys/warehouse --day 2026-11-11
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from qsys.data.board.assign import assign, concept_counts
from qsys.data.board.concepts import ConceptSnapshot
from qsys.data.board.ladder import previous_heights, promotions, today_height
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


def summarize(day: date, prev_day: date, query: WarehouseQuery, sessions: Sequence[date],
              snapshot: ConceptSnapshot | None) -> dict:
    start = min(sessions)
    flags = query.daily_flags(start, day)
    limit_list = query.limit_list(None, start, prev_day)
    prev = previous_heights(prev_day, limit_list, flags, sessions)
    today = flags.filter((pl.col("dt") == day) & (pl.col("is_limit_up") == True))  # noqa: E712
    sealed = set(today["symbol"].to_list())
    heights = {s: today_height(prev.get(s), True) for s in sealed}  # 昨日无行即未知
    columns = None
    if snapshot is not None:
        counts = concept_counts(sealed, snapshot)
        columns = sorted(Counter(assign(s, counts, snapshot) for s in sealed).items(),
                         key=lambda item: (-item[1], item[0]))
    return {
        "day": day.isoformat(),
        "sealed": len(sealed),
        "heights": dict(Counter(h for h in heights.values() if h is not None)),
        "unknown_heights": sum(h is None for h in heights.values()),
        "promotions": [{"level": r.level, "yesterday": list(r.yesterday),
                        "promoted": list(r.promoted)}
                       for r in promotions(prev, sealed)],
        "columns": columns,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    parser.add_argument("--lookback-days", type=int, default=60)
    args = parser.parse_args()
    query = WarehouseQuery(Warehouse(args.warehouse))
    cal = query.trading_calendar(args.day - timedelta(days=args.lookback_days), args.day,
                                 open_only=True)
    sessions = sorted(cal["dt"].to_list())
    if not sessions or sessions[-1] != args.day or len(sessions) < 2:
        print(json.dumps({"error": "交易日历未覆盖该日或缺前一交易日"}, ensure_ascii=False))
        return 1
    print(json.dumps(summarize(args.day, sessions[-2], query, sessions, None),
                     ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
