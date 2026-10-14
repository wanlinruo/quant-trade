"""CLI for versioned Warehouse snapshots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from qsys.data.snapshots import (
    create_snapshot,
    current_snapshot,
    publish_current_snapshot,
    verify_snapshot,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create")
    create.add_argument("--warehouse", required=True, type=Path)
    create.add_argument("--store", required=True, type=Path)
    create.add_argument("--complete-through", required=True)
    create.add_argument("--schema-version", required=True)
    create.add_argument("--snapshot-id")
    create.add_argument("--source-revision", default="")
    create.add_argument("--publish-current", action="store_true")

    verify = commands.add_parser("verify")
    verify.add_argument("--snapshot", required=True, type=Path)

    publish = commands.add_parser("publish-current")
    publish.add_argument("--store", required=True, type=Path)
    publish.add_argument("--snapshot-id", required=True)

    show = commands.add_parser("show-current")
    show.add_argument("--store", required=True, type=Path)

    args = parser.parse_args()
    if args.command == "create":
        snapshot = create_snapshot(
            args.warehouse,
            args.store,
            complete_through=args.complete_through,
            schema_version=args.schema_version,
            snapshot_id=args.snapshot_id,
            source_revision=args.source_revision,
            publish_current=args.publish_current,
        )
        print(json.dumps(snapshot.manifest, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "verify":
        failures = verify_snapshot(args.snapshot)
        if failures:
            for failure in failures:
                print(failure)
            return 2
        print("snapshot verified")
        return 0
    if args.command == "publish-current":
        snapshot = publish_current_snapshot(args.store, args.snapshot_id)
    else:
        snapshot = current_snapshot(args.store)
    print(json.dumps(snapshot.manifest, ensure_ascii=False, sort_keys=True))
    return 0
