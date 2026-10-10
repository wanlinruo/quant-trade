#!/usr/bin/env python3
"""Verify quarantined historical auction facts against QMT final snapshots."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.research.auction_parity import evaluate_parity


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    query = WarehouseQuery(Warehouse(args.warehouse))
    verdict = evaluate_parity(
        query.auction_open(None, args.start, args.end),
        query.auction_ticks(None, args.start, args.end),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0 if verdict["auction_parity_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
