"""Allowlisted task entrypoint executed as a Research Runner child process."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from qsys.data.snapshots import load_snapshot, verify_snapshot
from qsys.research.jobs import JobRequest


def verify_snapshot_task(request: JobRequest, snapshot_root: Path, artifacts: Path) -> None:
    failures = verify_snapshot(snapshot_root)
    if failures:
        raise RuntimeError("snapshot verification failed: " + "; ".join(failures[:3]))
    snapshot = load_snapshot(snapshot_root)
    if snapshot.snapshot_id != request.snapshot_id:
        raise ValueError("job snapshot_id does not match the mounted snapshot")
    if snapshot.manifest.get("content_sha256") != request.snapshot_content_sha256:
        raise ValueError("job snapshot hash does not match the mounted snapshot")
    partitions = snapshot.manifest["partitions"]
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "snapshot-verification.json").write_text(
        json.dumps(
            {
                "snapshot_id": request.snapshot_id,
                "content_sha256": request.snapshot_content_sha256,
                "complete_through": snapshot.manifest["complete_through"],
                "partition_count": len(partitions),
                "bytes": sum(int(item["bytes"]) for item in partitions),
                "rows": sum(int(item.get("rows", 0)) for item in partitions),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )


TASKS = {"verify-snapshot": verify_snapshot_task}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--artifacts", required=True, type=Path)
    args = parser.parse_args()
    request = JobRequest.from_dict(json.loads(args.request.read_text(encoding="utf-8")))
    try:
        task = TASKS[request.task]
    except KeyError as exc:
        raise ValueError(f"task is not allowlisted: {request.task}") from exc
    task(request, args.snapshot, args.artifacts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
