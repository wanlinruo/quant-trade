"""Allowlisted task entrypoint executed as a Research Runner child process."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from qsys.backtest.runner import ENTRY_FILL_MODES, execute_backtest
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


def structural_backtest_task(
    request: JobRequest, snapshot_root: Path, artifacts: Path,
) -> None:
    expected = {"start", "end", "cash", "params", "jq_compat", "entry_fill"}
    parameters = dict(request.parameters)
    if set(parameters) != expected:
        raise ValueError("structural-backtest parameters are incomplete or unexpected")
    start = date.fromisoformat(str(parameters["start"]))
    end = date.fromisoformat(str(parameters["end"]))
    cash = float(parameters["cash"])
    params = parameters["params"]
    if not isinstance(params, dict):
        raise ValueError("structural-backtest params must be an object")
    if not isinstance(parameters["jq_compat"], bool):
        raise ValueError("structural-backtest jq_compat must be boolean")
    entry_fill = str(parameters["entry_fill"])
    if entry_fill not in ENTRY_FILL_MODES:
        raise ValueError(f"unsupported structural-backtest entry_fill: {entry_fill}")
    strategy = (
        Path(__file__).parent / "builtin_strategies" / "structural_v1_2.py"
    ).resolve(strict=True)

    def progress(done: int, total: int, session: date) -> None:
        print(json.dumps({
            "event": "progress", "done": done, "total": total,
            "session": session.isoformat(),
        }), flush=True)

    execute_backtest(
        strategy, start=start, end=end, cash=cash,
        warehouse=snapshot_root / "warehouse", out_dir=artifacts,
        params=params, jq_compat=parameters["jq_compat"],
        progress=progress, entry_fill=entry_fill,
        strategy_source=strategy.read_text(encoding="utf-8"),
    )


TASKS = {
    "verify-snapshot": verify_snapshot_task,
    "structural-backtest": structural_backtest_task,
}


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
