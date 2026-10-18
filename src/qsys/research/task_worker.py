"""Allowlisted task entrypoint executed as a Research Runner child process."""

from __future__ import annotations

import argparse
import errno
import json
import os
import socket
import time
from datetime import date
from pathlib import Path

from qsys.backtest.runner import ENTRY_FILL_MODES, execute_backtest
from qsys.data.snapshots import load_snapshot, verify_snapshot
from qsys.research.jobs import JobRequest
from qsys.research.monthly_tasks import auction_timing_study_task, gate_study_task


def _require_no_parameters(request: JobRequest) -> None:
    if request.parameters:
        raise ValueError(f"{request.task} does not accept parameters")


def isolation_permissions_task(
    request: JobRequest, snapshot_root: Path, artifacts: Path,
) -> None:
    """Test-only proof that the real service sandbox denies production paths."""
    _require_no_parameters(request)
    manifest = snapshot_root / "manifest.json"
    manifest.read_bytes()
    denials: dict[str, str] = {}

    def expect_denied(name: str, operation) -> None:
        try:
            operation()
        except OSError as exc:
            if exc.errno not in {
                errno.EACCES, errno.EPERM, errno.ENOENT, errno.EROFS,
                errno.EAFNOSUPPORT, errno.ENETUNREACH,
            }:
                raise
            denials[name] = f"{type(exc).__name__}:{exc.errno}"
            return
        raise RuntimeError(f"sandbox unexpectedly allowed {name}")

    expect_denied(
        "snapshot-write",
        lambda: os.open(manifest, os.O_WRONLY | os.O_APPEND),
    )
    for path in (
        "/data/qsys/warehouse/.qsys-research-deny-probe",
        "/data/qsys/warehouse-snapshot-store/.qsys-research-deny-probe",
        "/data/qsys/var-prod/.qsys-research-deny-probe",
        "/data/qsys/research-jobs/.qsys-research-deny-probe",
        "/home/ubuntu/qsys/config/config.toml",
    ):
        expect_denied(path, lambda path=path: os.open(path, os.O_RDONLY))
    expect_denied("ipv4-socket", lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM))

    def connect_redis() -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect("/run/redis/redis-server.sock")

    expect_denied("redis-unix-socket", connect_redis)
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "isolation-permissions.json").write_text(
        json.dumps({
            "snapshot_read": True,
            "job_store_write": True,
            "denials": denials,
        }, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def resource_pressure_cpu_io_task(
    request: JobRequest, snapshot_root: Path, artifacts: Path,
) -> None:
    """Test-only bounded CPU and write pressure inside the service cgroup."""
    _require_no_parameters(request)
    deadline = time.monotonic() + 8.0
    accumulator = 0
    while time.monotonic() < deadline:
        accumulator = (accumulator * 33 + 17) & 0xFFFFFFFF
    artifacts.mkdir(parents=True, exist_ok=True)
    pressure = artifacts / ".io-pressure.tmp"
    block = b"qsys-test-pressure\n" * 4096
    written = 0
    with pressure.open("wb", buffering=0) as stream:
        while written < 64 * 1024 * 1024:
            written += stream.write(block)
        os.fsync(stream.fileno())
    pressure.unlink()
    (artifacts / "resource-pressure.json").write_text(
        json.dumps({
            "cpu_wall_seconds": 8,
            "io_bytes_requested": written,
            "accumulator": accumulator,
        }, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def resource_pressure_memory_task(
    request: JobRequest, snapshot_root: Path, artifacts: Path,
) -> None:
    """Test-only intentional breach of the 512 MiB service MemoryMax."""
    _require_no_parameters(request)
    chunks = []
    for _ in range(48):
        block = bytearray(16 * 1024 * 1024)
        for offset in range(0, len(block), 4096):
            block[offset] = 1
        chunks.append(block)
        time.sleep(0.02)
    raise RuntimeError("memory pressure unexpectedly survived 768 MiB allocation")


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
    "auction-timing-study": auction_timing_study_task,
    "gate-study": gate_study_task,
    "isolation-permissions": isolation_permissions_task,
    "resource-pressure-cpu-io": resource_pressure_cpu_io_task,
    "resource-pressure-memory": resource_pressure_memory_task,
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
