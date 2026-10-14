"""Narrow Web control plane for the isolated Research Runner job store."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from qsys.data.snapshots import (
    SNAPSHOT_FORMAT,
    SNAPSHOT_VERSION,
    Snapshot,
    load_snapshot,
)
from qsys.research.jobs import HEX_40, HEX_64, JOB_ID, JobLimits, JobRequest, JobStore


RELEASE_FIELDS = {
    "format", "version", "source_sha", "source_tree", "wheel_sha256",
    "wheel_bytes", "pyproject_sha256", "uv_lock_sha256", "ci",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 << 20):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError(f"JSON symlink is forbidden: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _current_snapshot_identity(store_root: Path) -> Snapshot:
    """Read the atomically published identity without hashing all partitions."""
    store = Path(store_root).resolve(strict=True)
    pointer = store / "current"
    if not pointer.is_symlink():
        raise FileNotFoundError(f"snapshot current pointer is missing: {pointer}")
    resolved = pointer.resolve(strict=True)
    snapshots = (store / "snapshots").resolve(strict=True)
    if resolved.parent != snapshots:
        raise ValueError("snapshot current pointer escapes the snapshot store")
    snapshot = load_snapshot(resolved)
    manifest = snapshot.manifest
    if (manifest.get("format") != SNAPSHOT_FORMAT
            or manifest.get("version") != SNAPSHOT_VERSION
            or manifest.get("snapshot_id") != resolved.name
            or not HEX_64.fullmatch(str(manifest.get("content_sha256", "")))):
        raise ValueError("current snapshot identity is invalid")
    return snapshot


@dataclass(frozen=True, slots=True)
class ResearchSubmissionProfile:
    code_sha: str
    wheel_sha256: str
    snapshot_store: Path
    limits: JobLimits
    tasks: frozenset[str] = frozenset({"verify-snapshot"})

    @classmethod
    def from_release(
        cls, release_root: Path, snapshot_store: Path, limits: JobLimits,
        *, tasks: frozenset[str] = frozenset({"verify-snapshot"}),
    ) -> "ResearchSubmissionProfile":
        root = Path(release_root).resolve(strict=True)
        manifest = _json(root / "release.json")
        if (set(manifest) != RELEASE_FIELDS
                or manifest.get("format") != "qsys-research-release"
                or manifest.get("version") != 1):
            raise ValueError("Research Runner release manifest is invalid")
        wheel = root / "dist" / "qsys.whl"
        if wheel.is_symlink() or not wheel.is_file():
            raise ValueError("Research Runner wheel must be a regular file")
        wheel_hash = _sha256(wheel)
        if manifest.get("wheel_sha256") != wheel_hash:
            raise ValueError("Research Runner wheel hash differs from release manifest")
        if manifest.get("wheel_bytes") != wheel.stat().st_size:
            raise ValueError("Research Runner wheel size differs from release manifest")
        if not HEX_40.fullmatch(str(manifest.get("source_sha", ""))):
            raise ValueError("Research Runner release source SHA is invalid")
        limits.validate()
        if not tasks:
            raise ValueError("at least one Research Runner task must be allowed")
        return cls(
            code_sha=str(manifest["source_sha"]), wheel_sha256=wheel_hash,
            snapshot_store=Path(snapshot_store).resolve(strict=True),
            limits=limits, tasks=tasks,
        )

    def request(
        self, *, task: str, parameters: Mapping[str, Any],
        timeout_seconds: int | None = None,
    ) -> JobRequest:
        if task not in self.tasks:
            raise ValueError(f"task is not enabled for Web submission: {task}")
        snapshot = _current_snapshot_identity(self.snapshot_store)
        timeout = timeout_seconds if timeout_seconds is not None else self.limits.timeout_seconds
        limits = JobLimits(
            self.limits.memory_max_mb, self.limits.cpu_quota_percent,
            self.limits.io_weight, timeout,
        )
        return JobRequest.create(
            job_id=uuid.uuid4().hex,
            code_sha=self.code_sha,
            wheel_sha256=self.wheel_sha256,
            snapshot_id=snapshot.snapshot_id,
            snapshot_content_sha256=str(snapshot.manifest["content_sha256"]),
            task=task,
            parameters=parameters,
            limits=limits,
        )


@dataclass(frozen=True, slots=True)
class ResearchJobView:
    job_id: str
    state: str
    request: Mapping[str, Any]
    status: Mapping[str, Any]
    log: str
    artifacts: Mapping[str, Any] | None

    @property
    def detail(self) -> str:
        return str(self.status.get("detail", ""))


class ResearchJobConsole:
    def __init__(
        self, root: Path, *, profile: ResearchSubmissionProfile | None = None,
    ) -> None:
        self.root = Path(root).resolve(strict=True)
        self.store = JobStore(self.root, initialize=False)
        self.profile = profile

    @property
    def submission_enabled(self) -> bool:
        return self.profile is not None

    def submit(
        self, task: str, parameters: Mapping[str, Any],
        *, timeout_seconds: int | None = None,
    ) -> ResearchJobView:
        if self.profile is None:
            raise RuntimeError("Research Runner Web submission is disabled")
        request = self.profile.request(
            task=task, parameters=parameters, timeout_seconds=timeout_seconds,
        )
        self.store.enqueue(request)
        found = self.job(request.job_id)
        assert found is not None
        return found

    def cancel(self, job_id: str) -> None:
        job = self.job(job_id)
        if job is None:
            raise LookupError("no such research job")
        if job.state not in {"queued", "claimed", "running"}:
            raise ValueError("research job cannot be cancelled")
        self.store.request_cancel(job_id)

    def jobs(self, *, limit: int = 200) -> list[ResearchJobView]:
        ids = {
            path.stem for path in (self.root / "inbox").glob("*.json")
            if JOB_ID.fullmatch(path.stem)
        }
        ids.update(
            path.name for path in (self.root / "jobs").iterdir()
            if path.is_dir() and JOB_ID.fullmatch(path.name)
        )
        rows = [job for job_id in ids if (job := self.job(job_id)) is not None]
        rows.sort(key=lambda job: str(job.request.get("created_at", "")), reverse=True)
        return rows[:limit]

    def job(self, job_id: str, *, log_limit: int = 100_000) -> ResearchJobView | None:
        if not JOB_ID.fullmatch(job_id):
            return None
        job_root = self.root / "jobs" / job_id
        if job_root.is_dir() and not job_root.is_symlink():
            request_path = job_root / "request.json"
            status_path = job_root / "status.json"
            if not request_path.is_file() or not status_path.is_file():
                return None
            request, status = _json(request_path), _json(status_path)
            state = str(status.get("state", "unknown"))
            log_path = job_root / "run.log"
            log = (
                log_path.read_bytes()[-log_limit:].decode("utf-8", errors="replace")
                if log_path.is_file() and not log_path.is_symlink() else ""
            )
            manifest_path = job_root / "artifacts" / "manifest.json"
            artifacts = _json(manifest_path) if manifest_path.is_file() else None
            return ResearchJobView(job_id, state, request, status, log, artifacts)
        queued = self.root / "inbox" / f"{job_id}.json"
        if queued.is_file() and not queued.is_symlink():
            request = _json(queued)
            return ResearchJobView(
                job_id, "queued", request,
                {"job_id": job_id, "state": "queued", "updated_at": request.get("created_at", "")},
                "", None,
            )
        return None

    def artifact(self, job_id: str, filename: str) -> tuple[bytes, str] | None:
        if not JOB_ID.fullmatch(job_id) or not filename:
            return None
        root = self.root / "jobs" / job_id / "artifacts"
        if root.is_symlink() or not root.is_dir():
            return None
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            return None
        manifest = _json(manifest_path)
        entries = {
            str(item.get("path")): item for item in manifest.get("files", [])
            if isinstance(item, dict)
        }
        expected = entries.get(filename)
        if expected is None:
            return None
        target = root / filename
        try:
            resolved = target.resolve(strict=True)
            resolved_root = root.resolve(strict=True)
        except FileNotFoundError:
            return None
        if (target.is_symlink() or not target.is_file()
                or not resolved.is_relative_to(resolved_root)):
            return None
        body = target.read_bytes()
        if (expected.get("bytes") != len(body)
                or expected.get("sha256") != hashlib.sha256(body).hexdigest()):
            return None
        media = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return body, media
