"""Build and verify isolated test-Web operational snapshots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from qsys.ops.test_web_snapshots import create_ops_snapshot, verify_ops_snapshot


def _named_path(value: str) -> tuple[str, Path]:
    name, separator, raw_path = value.partition("=")
    if not separator or not raw_path:
        raise argparse.ArgumentTypeError("alert root must use NAME=/absolute/path")
    path = Path(raw_path)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("alert root path must be absolute")
    return name, path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--store", required=True, type=Path)
    create.add_argument("--service-runs", required=True, type=Path)
    create.add_argument("--health-state", required=True, type=Path)
    create.add_argument("--board-root", required=True, type=Path)
    create.add_argument("--alert-root", required=True, action="append", type=_named_path)
    create.add_argument("--source-revision", required=True)
    create.add_argument("--retained-days", type=int, default=35)
    create.add_argument("--snapshot-id")
    create.add_argument("--publish-current", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("--snapshot", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "verify":
        failures = verify_ops_snapshot(args.snapshot)
        if failures:
            print("\n".join(failures))
            return 2
        print("snapshot verified")
        return 0
    manifest = create_ops_snapshot(
        args.store, service_runs=args.service_runs, health_state=args.health_state,
        board_root=args.board_root, alert_roots=args.alert_root,
        source_revision=args.source_revision, retained_days=args.retained_days,
        snapshot_id=args.snapshot_id, publish_current=args.publish_current,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
