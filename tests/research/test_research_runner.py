"""Research Runner service-boundary tests."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from qsys.data.snapshots import create_snapshot
from qsys.research.drill_cli import MEMORY_PRESSURE_WAIT_SECONDS
from qsys.research.jobs import JobLimits, JobRequest, JobStore
from qsys.research.runner import (
    ResearchRunner,
    RunnerIdentity,
    _cancelled_worker_was_oom_killed,
)


CODE_SHA = "a" * 40
CREATED = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)


def test_memory_pressure_drill_allows_for_memory_high_throttling() -> None:
    assert MEMORY_PRESSURE_WAIT_SECONDS == 360.0


def test_runner_distinguishes_service_stop_after_oom_from_user_cancel() -> None:
    assert _cancelled_worker_was_oom_killed("cancelled", -9, False)
    assert _cancelled_worker_was_oom_killed("cancelled", 137, False)
    assert not _cancelled_worker_was_oom_killed("cancelled", -9, True)
    assert not _cancelled_worker_was_oom_killed("cancelled", -15, False)


def _rig(tmp_path: Path):
    warehouse = tmp_path / "warehouse"
    parquet = warehouse / "daily" / "year=2026" / "part.parquet"
    parquet.parent.mkdir(parents=True)
    pq.write_table(pa.table({"dt": ["2026-10-13"], "close": [10.0]}), parquet)
    snapshot_store = tmp_path / "snapshots"
    snapshot = create_snapshot(
        warehouse,
        snapshot_store,
        complete_through="2026-10-13",
        schema_version="v1",
        snapshot_id="snapshot-1",
        created_at=CREATED,
    )
    wheel = tmp_path / "qsys.whl"
    wheel.write_bytes(b"test wheel identity")
    wheel_sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
    store = JobStore(tmp_path / "jobs")
    identity = RunnerIdentity(CODE_SHA, wheel, JobLimits(4096, 200, 1000, 3600))
    runner = ResearchRunner(store, snapshot_store, identity, poll_seconds=0.01)
    return store, runner, snapshot, wheel_sha


def _request(snapshot, wheel_sha: str, **overrides) -> JobRequest:
    values = {
        "job_id": "verify-1",
        "code_sha": CODE_SHA,
        "wheel_sha256": wheel_sha,
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_content_sha256": snapshot.manifest["content_sha256"],
        "task": "verify-snapshot",
        "parameters": {},
        "limits": JobLimits(4096, 200, 1000, 300),
        "created_at": "2026-10-14T07:00:00+00:00",
    }
    values.update(overrides)
    return JobRequest.create(**values)


def _status(store: JobStore, job_id: str) -> dict:
    return json.loads((store.root / "jobs" / job_id / "status.json").read_text())


def test_runner_executes_allowlisted_task_and_hashes_artifacts(tmp_path: Path):
    store, runner, snapshot, wheel_sha = _rig(tmp_path)
    store.enqueue(_request(snapshot, wheel_sha))

    assert runner.run_once()
    assert _status(store, "verify-1")["state"] == "succeeded"
    artifacts = store.root / "jobs" / "verify-1" / "artifacts"
    result = json.loads((artifacts / "snapshot-verification.json").read_text())
    manifest = json.loads((artifacts / "manifest.json").read_text())
    resources = json.loads((store.root / "jobs" / "verify-1" / "resources.json").read_text())
    assert result["snapshot_id"] == "snapshot-1"
    assert manifest["file_count"] == 1
    assert manifest["files"][0]["path"] == "snapshot-verification.json"
    assert len(manifest["log"]["sha256"]) == 64
    assert resources["format"] == "qsys-research-resource-usage"
    assert resources["exit_code"] == 0
    assert resources["wall_seconds"] >= 0
    assert resources["max_rss_bytes"] > 0
    assert manifest["resources"] == resources


def test_runner_rejects_test_drill_without_explicit_unit_flag(tmp_path: Path):
    store, runner, snapshot, wheel_sha = _rig(tmp_path)
    store.enqueue(_request(
        snapshot, wheel_sha, task="isolation-permissions",
    ))

    assert runner.run_once()
    assert _status(store, "verify-1")["state"] == "failed"
    assert "test drill task is disabled" in _status(store, "verify-1")["detail"]


def test_monthly_worker_rejects_future_period_and_keeps_failure_resources(tmp_path):
    store, runner, snapshot, wheel_sha = _rig(tmp_path)
    store.enqueue(_request(snapshot, wheel_sha, task="auction-timing-study", parameters={
        "start": "2026-01-01", "end": "2026-10-14", "market_data_facade": "legacy",
    }))
    assert runner.run_once()
    assert _status(store, "verify-1")["state"] == "failed"
    job = store.root / "jobs" / "verify-1"
    assert "complete_through" in (job / "run.log").read_text()
    resources = json.loads((job / "resources.json").read_text())
    assert resources["exit_code"] != 0
    assert not (job / "artifacts" / "auction-timing").exists()


def test_runner_fails_closed_on_release_or_snapshot_mismatch(tmp_path: Path):
    store, runner, snapshot, wheel_sha = _rig(tmp_path)
    store.enqueue(_request(snapshot, "d" * 64))
    assert runner.run_once()
    assert _status(store, "verify-1")["state"] == "failed"
    assert "wheel_sha256" in _status(store, "verify-1")["detail"]

    store.enqueue(_request(
        snapshot, wheel_sha, job_id="verify-2", snapshot_content_sha256="e" * 64,
    ))
    assert runner.run_once()
    assert _status(store, "verify-2")["state"] == "failed"
    assert "snapshot_content_sha256" in _status(store, "verify-2")["detail"]


def test_runner_honours_pre_start_cancel_and_resource_ceiling(tmp_path: Path):
    store, runner, snapshot, wheel_sha = _rig(tmp_path)
    store.enqueue(_request(snapshot, wheel_sha))
    store.request_cancel("verify-1")
    assert runner.run_once()
    assert _status(store, "verify-1")["state"] == "cancelled"

    store.enqueue(_request(
        snapshot,
        wheel_sha,
        job_id="verify-2",
        limits=JobLimits(8192, 200, 1000, 300),
    ))
    assert runner.run_once()
    assert _status(store, "verify-2")["state"] == "failed"
    assert "cgroup ceiling" in _status(store, "verify-2")["detail"]


def test_runner_identity_pins_the_resolved_wheel_and_its_hash(tmp_path: Path):
    first = tmp_path / "release-1"
    second = tmp_path / "release-2"
    first.mkdir()
    second.mkdir()
    (first / "qsys.whl").write_bytes(b"first immutable wheel")
    (second / "qsys.whl").write_bytes(b"second immutable wheel")
    current = tmp_path / "current"
    current.symlink_to(first, target_is_directory=True)

    identity = RunnerIdentity(
        CODE_SHA, current / "qsys.whl", JobLimits(4096, 200, 1000, 3600),
    )
    pinned_hash = identity.wheel_sha256
    current.unlink()
    current.symlink_to(second, target_is_directory=True)

    assert identity.wheel == (first / "qsys.whl").resolve()
    assert identity.wheel_sha256 == pinned_hash
    assert identity.wheel_sha256 != hashlib.sha256(
        (second / "qsys.whl").read_bytes(),
    ).hexdigest()


def test_runner_identity_rejects_a_wheel_file_symlink(tmp_path: Path):
    target = tmp_path / "release.whl"
    target.write_bytes(b"wheel")
    link = tmp_path / "qsys.whl"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="wheel path must not be a symlink"):
        RunnerIdentity(CODE_SHA, link, JobLimits(4096, 200, 1000, 3600))
