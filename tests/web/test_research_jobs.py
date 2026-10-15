from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from qsys.data.snapshots import create_snapshot
from qsys.ops.journal import SessionJournal
from qsys.research.jobs import JobLimits
from qsys.web.app import create_app
from qsys.web.config import WebConfig
from qsys.web.research_jobs import ResearchJobConsole, ResearchSubmissionProfile
from qsys.web.views import MonitorService


def _console(tmp_path: Path) -> ResearchJobConsole:
    jobs = tmp_path / "research-jobs"
    for name in ("inbox", "jobs", "control", "rejected"):
        (jobs / name).mkdir(parents=True)
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    (warehouse / "sample.txt").write_text("immutable\n", encoding="utf-8")
    snapshots = tmp_path / "snapshot-store"
    create_snapshot(
        warehouse, snapshots, complete_through="2026-10-13",
        schema_version="test-v1", snapshot_id="close-20261013",
        publish_current=True,
    )
    profile = ResearchSubmissionProfile(
        code_sha="a" * 40, wheel_sha256="b" * 64,
        snapshot_store=snapshots,
        limits=JobLimits(4096, 100, 10, 14_400),
    )
    return ResearchJobConsole(jobs, profile=profile)


def _client(warehouse, console: ResearchJobConsole) -> TestClient:
    config = WebConfig()
    monitor = MonitorService(SessionJournal(warehouse), config=config)
    return TestClient(create_app(monitor, config, research_jobs=console))


def test_web_submission_binds_server_identity_and_current_snapshot(warehouse, tmp_path):
    console = _console(tmp_path)
    client = _client(warehouse, console)

    response = client.post("/api/research-jobs", json={
        "task": "verify-snapshot", "parameters": {"purpose": "preflight"},
        "code_sha": "f" * 40, "snapshot_id": "client-choice",
    })

    assert response.status_code == 201
    job = console.job(response.json()["id"])
    assert job is not None and job.state == "queued"
    assert job.request["code_sha"] == "a" * 40
    assert job.request["wheel_sha256"] == "b" * 64
    assert job.request["snapshot_id"] == "close-20261013"
    assert "隔离研究任务" in client.get("/research-jobs").text


def test_web_submission_rejects_non_allowlisted_tasks_and_secrets(warehouse, tmp_path):
    client = _client(warehouse, _console(tmp_path))

    arbitrary = client.post("/api/research-jobs", json={
        "task": "shell", "parameters": {"command": "id"},
    })
    secret = client.post("/api/research-jobs", json={
        "task": "verify-snapshot", "parameters": {"api_token": "hidden"},
    })

    assert arbitrary.status_code == 422
    assert secret.status_code == 422


def test_web_reads_runner_status_log_and_manifest_without_mutating_them(
    warehouse, tmp_path,
):
    console = _console(tmp_path)
    client = _client(warehouse, console)
    job_id = client.post("/api/research-jobs", json={
        "task": "verify-snapshot", "parameters": {},
    }).json()["id"]
    claimed = console.store.claim_next()
    assert claimed is not None
    console.store.transition(claimed, "running")
    (claimed.root / "run.log").write_text("verified\n", encoding="utf-8")
    claimed.artifacts.mkdir()
    (claimed.artifacts / "result.json").write_text('{"ok":true}\n', encoding="utf-8")
    console.store.write_artifact_manifest(claimed)
    console.store.transition(claimed, "succeeded")

    payload = client.get(f"/api/research-jobs/{job_id}").json()
    artifact = client.get(f"/research-jobs/{job_id}/file/result.json")

    assert payload["status"] == "succeeded"
    assert payload["log"] == "verified\n"
    assert payload["artifacts"]["file_count"] == 1
    assert artifact.status_code == 200 and artifact.json() == {"ok": True}
    assert client.get(f"/research-jobs/{job_id}/file/manifest.json").status_code == 404


def test_read_only_console_disables_submission_but_keeps_history(warehouse, tmp_path):
    writable = _console(tmp_path)
    request = writable.profile.request(task="verify-snapshot", parameters={})
    writable.store.enqueue(request)
    read_only = ResearchJobConsole(writable.root)
    client = _client(warehouse, read_only)

    assert request.job_id in client.get("/research-jobs").text
    assert client.post("/api/research-jobs", json={
        "task": "verify-snapshot", "parameters": {},
    }).status_code == 404
    methods = {
        method for route in client.app.routes
        if getattr(route, "path", "").startswith("/api/research-jobs")
        for method in getattr(route, "methods", set())
    }
    assert methods <= {"GET", "HEAD", "OPTIONS"}


def test_cancel_only_writes_control_marker(warehouse, tmp_path):
    console = _console(tmp_path)
    client = _client(warehouse, console)
    job_id = client.post("/api/research-jobs", json={
        "task": "verify-snapshot", "parameters": {},
    }).json()["id"]

    response = client.post(f"/api/research-jobs/{job_id}/cancel")

    assert response.status_code == 200
    marker = json.loads((console.root / "control" / f"{job_id}.cancel").read_text())
    assert marker["job_id"] == job_id
    assert console.job(job_id).state == "queued"


def test_profile_rejects_release_wheel_tampering(tmp_path):
    release = tmp_path / "release"
    (release / "dist").mkdir(parents=True)
    wheel = release / "dist" / "qsys-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    manifest = {
        "format": "qsys-research-release", "version": 2,
        "source_sha": "a" * 40, "source_tree": "b" * 40,
        "wheel_filename": wheel.name,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "wheel_bytes": wheel.stat().st_size,
        "pyproject_sha256": "c" * 64, "uv_lock_sha256": "d" * 64,
        "ci": {"repository": "owner/repo", "run_id": "1", "run_attempt": "1"},
    }
    (release / "release.json").write_text(json.dumps(manifest), encoding="utf-8")
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()

    profile = ResearchSubmissionProfile.from_release(
        release, snapshots, JobLimits(4096, 100, 10, 14_400),
    )
    assert profile.code_sha == "a" * 40
    wheel.write_bytes(b"tampered")
    try:
        ResearchSubmissionProfile.from_release(
            release, snapshots, JobLimits(4096, 100, 10, 14_400),
        )
    except ValueError as exc:
        assert "hash differs" in str(exc)
    else:
        raise AssertionError("tampered wheel was accepted")


def test_artifact_symlink_is_never_served(warehouse, tmp_path):
    console = _console(tmp_path)
    client = _client(warehouse, console)
    job_id = client.post("/api/research-jobs", json={
        "task": "verify-snapshot", "parameters": {},
    }).json()["id"]
    claimed = console.store.claim_next()
    assert claimed is not None
    claimed.artifacts.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    (claimed.artifacts / "result.txt").symlink_to(outside)
    (claimed.artifacts / "manifest.json").write_text(json.dumps({
        "files": [{"path": "result.txt", "bytes": 7, "sha256": "0" * 64}],
    }), encoding="utf-8")

    assert client.get(f"/research-jobs/{job_id}/file/result.txt").status_code == 404


def test_tampered_artifact_is_not_served(warehouse, tmp_path):
    console = _console(tmp_path)
    client = _client(warehouse, console)
    job_id = client.post("/api/research-jobs", json={
        "task": "verify-snapshot", "parameters": {},
    }).json()["id"]
    claimed = console.store.claim_next()
    assert claimed is not None
    claimed.artifacts.mkdir()
    result = claimed.artifacts / "result.txt"
    result.write_text("original", encoding="utf-8")
    console.store.write_artifact_manifest(claimed)
    result.write_text("tampered", encoding="utf-8")

    assert client.get(f"/research-jobs/{job_id}/file/result.txt").status_code == 404
