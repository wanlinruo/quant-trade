#!/usr/bin/env python3
"""Record complete T+1 outcomes for published shadow rankings."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from qsys.research.limit_up_shadow import latest_truth_session, outcome_truth
from qsys.web.limit_up_shadow import LimitUpShadowPublisher, LimitUpShadowReader

SHANGHAI = ZoneInfo("Asia/Shanghai")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--state", type=Path,
                        default=Path("var/limit-up-shadow.sqlite3"))
    args = parser.parse_args()

    latest = latest_truth_session(args.warehouse)
    reader = LimitUpShadowReader(args.state)
    publisher = LimitUpShadowPublisher(args.state)
    recorded = []
    for prediction_date, target_session in reader.pending_outcomes(latest):
        view = reader.view(prediction_date)
        if view.snapshot is None or view.snapshot.target_session != target_session:
            raise ValueError("published snapshot is unavailable or inconsistent")
        symbols = tuple(entry.symbol for entry in view.snapshot.entries)
        outcomes, source_sha = outcome_truth(args.warehouse, target_session, symbols)
        publisher.record_outcomes(
            prediction_date, outcomes, observed_at=datetime.now(SHANGHAI),
            source_data_sha=source_sha,
        )
        recorded.append({
            "prediction_date": prediction_date.isoformat(),
            "target_session": target_session.isoformat(),
            "entries": len(outcomes),
            "source_data_sha": source_sha,
        })
    print(json.dumps({"latest_truth_session": latest.isoformat(),
                      "recorded": recorded}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
