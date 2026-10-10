"""Validate and publish one compact limit-up shadow snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from qsys.web.limit_up_shadow import LimitUpShadowPublisher, LimitUpShadowSnapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="发布 T+1 涨停概率只读影子榜单")
    parser.add_argument("--input", required=True, help="单份快照 JSON")
    parser.add_argument("--state", default="var/limit-up-shadow.sqlite3",
                        help="独立影子榜单 SQLite")
    args = parser.parse_args()
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    snapshot = LimitUpShadowSnapshot.model_validate(payload)
    LimitUpShadowPublisher(Path(args.state)).publish(snapshot)
    print(json.dumps({
        "published": snapshot.prediction_date.isoformat(),
        "target_session": snapshot.target_session.isoformat(),
        "status": snapshot.status,
        "entries": len(snapshot.entries),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
