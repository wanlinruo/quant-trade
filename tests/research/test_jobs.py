from __future__ import annotations

import json
from pathlib import Path

import pytest

from qsys.research.jobs import JobLimits, JobRequest, JobStore


SHA = "a" * 40
DIGEST = "b" * 64


def request(job_id: str = "job-1") -> JobRequest:
    return JobRequest.create(
        job_id=job_id,
        code_sha=SHA,
        wheel_sha256=DIGEST,
        snapshot_id="20261013-close",
        snapshot_content_sha256="c" * 64,
        task="structural-backtest",
        parameters={"start": "2023-01-01", "cash": 100_000},
        limits=JobLimits(4096, 100, 100, 3600),
        created_at="2026-10-14T06:30:00+00:00",
    )


def test_request_round_trip_and_tamper_detection():
    original = request()
    assert JobRequest.from_dict(original.as_dict()) == original

    changed = original.as_dict()
    changed["parameters"]["cash"] = 1
    with pytest.raises(ValueError, match="request_sha256 mismatch"):
        JobRequest.from_dict(changed)


def test_enqueue_rechecks_digest_after_nested_parameter_mutation(tmp_path: Path):
    store = JobStore(tmp_path / "spool")
    original = request()
    original.parameters["cash"] = 1
    with pytest.raises(ValueError, match="request_sha256 mismatch"):
        store.enqueue(original)


def test_request_rejects_secrets_and_moving_code_refs():
    with pytest.raises(ValueError, match="full lowercase commit SHA"):
        JobRequest.create(
            job_id="job", code_sha="main", wheel_sha256=DIGEST,
            snapshot_id="snapshot", snapshot_content_sha256=DIGEST,
            task="backtest", parameters={}, limits=JobLimits(1024, 100, 100, 300),
        )
    with pytest.raises(ValueError, match="secret-shaped parameter"):
        JobRequest.create(
            job_id="job", code_sha=SHA, wheel_sha256=DIGEST,
            snapshot_id="snapshot", snapshot_content_sha256=DIGEST,
            task="backtest", parameters={"api_token": "do-not-store"},
            limits=JobLimits(1024, 100, 100, 300),
        )


def test_store_claims_once_and_runner_owns_status(tmp_path: Path):
    store = JobStore(tmp_path / "spool")
    store.enqueue(request())

    claimed = store.claim_next()
    assert claimed is not None
    assert store.claim_next() is None
    assert claimed.request.job_id == "job-1"
    assert json.loads((claimed.root / "status.json").read_text())["state"] == "claimed"

    store.transition(claimed, "running")
    store.transition(claimed, "succeeded")
    assert json.loads((claimed.root / "status.json").read_text())["state"] == "succeeded"
    with pytest.raises(ValueError, match="invalid job transition"):
        store.transition(claimed, "running")


def test_duplicate_enqueue_and_invalid_request_are_fail_closed(tmp_path: Path):
    store = JobStore(tmp_path / "spool")
    store.enqueue(request())
    with pytest.raises(FileExistsError):
        store.enqueue(request())

    bad = store.root / "inbox" / "bad.json"
    bad.write_text('{"not":"a request"}\n', encoding="utf-8")
    claimed = store.claim_next()
    assert claimed is not None
    assert (store.root / "rejected" / "bad.json").is_file()
    assert (store.root / "rejected" / "bad.error.json").is_file()


def test_cancel_marker_and_restart_recovery(tmp_path: Path):
    store = JobStore(tmp_path / "spool")
    store.enqueue(request())
    store.request_cancel("job-1")
    assert store.cancel_requested("job-1")
    claimed = store.claim_next()
    assert claimed is not None
    store.transition(claimed, "running")

    assert store.recover_interrupted() == ["job-1"]
    status = json.loads((claimed.root / "status.json").read_text())
    assert status["state"] == "interrupted"


def test_artifact_manifest_binds_outputs_to_request(tmp_path: Path):
    store = JobStore(tmp_path / "spool")
    store.enqueue(request())
    claimed = store.claim_next()
    assert claimed is not None
    claimed.artifacts.mkdir()
    (claimed.artifacts / "report.json").write_text('{"return":0.1}\n', encoding="utf-8")

    path = store.write_artifact_manifest(claimed)
    manifest = json.loads(path.read_text())
    assert manifest["request_sha256"] == claimed.request.request_sha256
    assert manifest["file_count"] == 1
    assert manifest["files"][0]["path"] == "report.json"
    assert len(manifest["content_sha256"]) == 64
