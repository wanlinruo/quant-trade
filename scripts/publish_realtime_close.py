#!/usr/bin/env python3
"""校验并发布阶段 7 收盘候选 JSON；失败时历史分区保持不变。"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from qsys.data.pipeline.realtime_close import publish_realtime_close
from qsys.data.realtime import RealtimeDailyBar, RealtimeMinuteBar
from qsys.data.storage import Warehouse


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args()
    raw = json.loads(args.candidate.read_text(encoding="utf-8"))
    day = date.fromisoformat(raw["day"])
    daily = [RealtimeDailyBar(
        symbol=row["symbol"], dt=date.fromisoformat(row["dt"]),
        open=float(row["open"]), high=float(row["high"]), low=float(row["low"]),
        close=float(row["close"]), prev_close=float(row["prev_close"]),
        volume=float(row["volume"]), amount=float(row["amount"]),
        source=row["source"], as_of=datetime.fromisoformat(row["as_of"]),
    ) for row in raw["daily"]]
    minutes = [RealtimeMinuteBar(
        symbol=row["symbol"], ts=datetime.fromisoformat(row["ts"]),
        open=float(row["open"]), high=float(row["high"]), low=float(row["low"]),
        close=float(row["close"]), volume=float(row["volume"]),
        amount=float(row["amount"]), source=row["source"],
        visible_at=datetime.fromisoformat(row["visible_at"]),
        finalized=bool(row["finalized"]), partial=bool(row["partial"]),
    ) for row in raw["minutes"]]
    result = publish_realtime_close(
        Warehouse(args.warehouse), day,
        published_at=datetime.fromisoformat(raw["published_at"]),
        daily=daily, minutes=minutes,
        expected_minutes={key: int(value) for key, value in raw["expected_minutes"].items()},
    )
    print(json.dumps({
        "status": result.status, "day": str(result.day),
        "daily_rows": result.daily_rows, "minute_rows": result.minute_rows,
        "manifest": str(result.manifest),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
