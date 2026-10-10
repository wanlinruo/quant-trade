#!/usr/bin/env python3
"""Generate and publish one promoted-model T+1 shadow ranking."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from qsys.paper.calendar import load_trading_calendar
from qsys.research.limit_up_shadow import build_daily_snapshot, latest_feature_session
from qsys.web.limit_up_shadow import LimitUpShadowPublisher, LimitUpShadowReader


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--research-run", type=Path, required=True,
                        help="通过 #210 门禁且含 manifest/verdict/models 的保留目录")
    parser.add_argument("--state", type=Path,
                        default=Path("var/limit-up-shadow.sqlite3"))
    parser.add_argument("--config", default=None, help="Tushare 交易日历配置")
    parser.add_argument("--prediction-date", type=date.fromisoformat,
                        help="默认取 market_regime 最新交易日")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--min-eligible", type=int, default=3_000,
                        help="全市场完整性下限，低于它拒绝发布")
    args = parser.parse_args()

    prediction_date = args.prediction_date or latest_feature_session(args.warehouse)
    calendar = load_trading_calendar(args.config)
    if not calendar.is_trading_day(prediction_date):
        raise ValueError(f"prediction date is not a trading session: {prediction_date}")
    target_session = calendar.next_session(prediction_date)

    existing = LimitUpShadowReader(args.state).view(prediction_date)
    if existing.state == "ready":
        print(json.dumps({
            "status": "skipped", "reason": "immutable snapshot already exists",
            "prediction_date": prediction_date.isoformat(),
        }, ensure_ascii=False))
        return 0

    snapshot = build_daily_snapshot(
        args.warehouse, args.research_run, prediction_date, target_session,
        threads=max(1, args.threads), min_eligible=max(1, args.min_eligible),
    )
    LimitUpShadowPublisher(args.state).publish(snapshot)
    print(json.dumps({
        "status": "published",
        "prediction_date": prediction_date.isoformat(),
        "target_session": target_session.isoformat(),
        "eligible_count": snapshot.eligible_count,
        "entries": len(snapshot.entries),
        "model_version": snapshot.model_version,
        "model_sha": snapshot.model_sha,
        "data_sha": snapshot.data_sha,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
