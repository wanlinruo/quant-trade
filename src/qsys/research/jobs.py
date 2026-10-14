"""Filesystem contract between the Web control plane and Research Runner.

The Web side writes immutable requests and cancellation markers. The Runner
atomically claims a request, then becomes the only writer of status, logs and
artifacts. No shared writable SQLite database is required across the boundary.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


JOB_FORMAT = "qsys-research-job"
JOB_VERSION = 1
JOB_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,95}$")
TASK_NAME = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
SECRET_KEY = re.compile(r"(password|passwd|secret|token|private.?key|account)", re.I)

TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
TRANSITIONS = {
    "claimed": frozenset({"running", "cancelled", "failed", "interrupted"}),
    "running": frozenset({"succeeded", "failed", "cancelled", "interrupted"}),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_bytes(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 << 20):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any], *, replace: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(_canonical_bytes(value) + b"\n")
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_json(value: object, *, path: str = "parameters") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path} contains a non-finite number")
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings")
            if SECRET_KEY.search(key):
                raise ValueError(f"secret-shaped parameter is forbidden: {path}.{key}")
            _validate_json(item, path=f"{path}.{key}")
        return
    raise ValueError(f"{path} contains a non-JSON value: {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class JobLimits:
    memory_max_mb: int
    cpu_quota_percent: int
    io_weight: int
    timeout_seconds: int

    def validate(self) -> None:
        if not 256 <= self.memory_max_mb <= 65_536:
            raise ValueError("memory_max_mb must be between 256 and 65536")
        if not 10 <= self.cpu_quota_percent <= 800:
            raise ValueError("cpu_quota_percent must be between 10 and 800")
        if not 1 <= self.io_weight <= 10_000:
            raise ValueError("io_weight must be between 1 and 10000")
        if not 60 <= self.timeout_seconds <= 86_400:
            raise ValueError("timeout_seconds must be between 60 and 86400")


@dataclass(frozen=True, slots=True)
class JobRequest:
    job_id: str
    created_at: str
    code_sha: str
    wheel_sha256: str
    snapshot_id: str
    snapshot_content_sha256: str
    task: str
    parameters: Mapping[str, Any]
    limits: JobLimits
    request_sha256: str

    @classmethod
    def create(
        cls,
        *,
        job_id: str,
        code_sha: str,
        wheel_sha256: str,
        snapshot_id: str,
        snapshot_content_sha256: str,
        task: str,
        parameters: Mapping[str, Any],
        limits: JobLimits,
        created_at: str | None = None,
    ) -> "JobRequest":
        body = {
            "format": JOB_FORMAT,
            "version": JOB_VERSION,
            "job_id": job_id,
            "created_at": created_at or _now(),
            "code_sha": code_sha,
            "wheel_sha256": wheel_sha256,
            "snapshot_id": snapshot_id,
            "snapshot_content_sha256": snapshot_content_sha256,
            "task": task,
            "parameters": dict(parameters),
            "limits": asdict(limits),
        }
        request = cls._from_body(body, _sha256_bytes(body))
        request.validate()
        return request

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "JobRequest":
        expected = {
            "format", "version", "job_id", "created_at", "code_sha", "wheel_sha256",
            "snapshot_id", "snapshot_content_sha256", "task", "parameters", "limits",
            "request_sha256",
        }
        if set(value) != expected:
            raise ValueError("job request has unexpected or missing fields")
        body = {key: value[key] for key in expected if key != "request_sha256"}
        request = cls._from_body(body, str(value["request_sha256"]))
        request.validate()
        if request.request_sha256 != _sha256_bytes(body):
            raise ValueError("request_sha256 mismatch")
        return request

    @classmethod
    def _from_body(cls, body: Mapping[str, Any], digest: str) -> "JobRequest":
        if body.get("format") != JOB_FORMAT or body.get("version") != JOB_VERSION:
            raise ValueError("unsupported job request format or version")
        raw_limits = body.get("limits")
        if not isinstance(raw_limits, Mapping):
            raise ValueError("limits must be an object")
        try:
            limits = JobLimits(**raw_limits)
            parameters = body["parameters"]
            if not isinstance(parameters, Mapping):
                raise ValueError("parameters must be an object")
            return cls(
                job_id=str(body["job_id"]),
                created_at=str(body["created_at"]),
                code_sha=str(body["code_sha"]),
                wheel_sha256=str(body["wheel_sha256"]),
                snapshot_id=str(body["snapshot_id"]),
                snapshot_content_sha256=str(body["snapshot_content_sha256"]),
                task=str(body["task"]),
                parameters=dict(parameters),
                limits=limits,
                request_sha256=digest,
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"invalid job request: {exc}") from exc

    def validate(self) -> None:
        if not JOB_ID.fullmatch(self.job_id) or self.job_id in {".", ".."}:
            raise ValueError("invalid job_id")
        try:
            created = datetime.fromisoformat(self.created_at)
        except ValueError as exc:
            raise ValueError("invalid created_at") from exc
        if created.tzinfo is None:
            raise ValueError("created_at must include a timezone")
        if not HEX_40.fullmatch(self.code_sha):
            raise ValueError("code_sha must be a full lowercase commit SHA")
        if not HEX_64.fullmatch(self.wheel_sha256):
            raise ValueError("wheel_sha256 must be a lowercase SHA-256")
        if not JOB_ID.fullmatch(self.snapshot_id):
            raise ValueError("invalid snapshot_id")
        if not HEX_64.fullmatch(self.snapshot_content_sha256):
            raise ValueError("snapshot_content_sha256 must be a lowercase SHA-256")
        if not TASK_NAME.fullmatch(self.task):
            raise ValueError("invalid task name")
        _validate_json(dict(self.parameters))
        self.limits.validate()

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": JOB_FORMAT,
            "version": JOB_VERSION,
            "job_id": self.job_id,
            "created_at": self.created_at,
            "code_sha": self.code_sha,
            "wheel_sha256": self.wheel_sha256,
            "snapshot_id": self.snapshot_id,
            "snapshot_content_sha256": self.snapshot_content_sha256,
            "task": self.task,
            "parameters": dict(self.parameters),
            "limits": asdict(self.limits),
            "request_sha256": self.request_sha256,
        }


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    request: JobRequest
    root: Path

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"


class JobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        for name in ("inbox", "jobs", "control", "rejected"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def enqueue(self, request: JobRequest) -> Path:
        # Reparse the serialized form so mutation of a nested parameters dict
        # after ``create`` cannot bypass the request digest.
        request = JobRequest.from_dict(request.as_dict())
        target = self.root / "inbox" / f"{request.job_id}.json"
        if (self.root / "jobs" / request.job_id).exists():
            raise FileExistsError(f"job already exists: {request.job_id}")
        _atomic_json(target, request.as_dict(), replace=False)
        return target

    def request_cancel(self, job_id: str) -> Path:
        if not JOB_ID.fullmatch(job_id):
            raise ValueError("invalid job_id")
        target = self.root / "control" / f"{job_id}.cancel"
        _atomic_json(target, {"job_id": job_id, "requested_at": _now()})
        return target

    def cancel_requested(self, job_id: str) -> bool:
        return (self.root / "control" / f"{job_id}.cancel").is_file()

    def claim_next(self) -> ClaimedJob | None:
        for queued in sorted((self.root / "inbox").glob("*.json")):
            try:
                value = json.loads(queued.read_text(encoding="utf-8"))
                request = JobRequest.from_dict(value)
            except Exception as exc:
                rejected = self.root / "rejected" / queued.name
                try:
                    os.replace(queued, rejected)
                    _atomic_json(
                        rejected.with_suffix(".error.json"),
                        {"rejected_at": _now(), "error": str(exc)},
                    )
                except FileNotFoundError:
                    pass
                continue
            job_root = self.root / "jobs" / request.job_id
            try:
                job_root.mkdir()
            except FileExistsError:
                continue
            try:
                os.replace(queued, job_root / "request.json")
            except FileNotFoundError:
                job_root.rmdir()
                continue
            _atomic_json(
                job_root / "status.json",
                {"job_id": request.job_id, "state": "claimed", "updated_at": _now()},
            )
            return ClaimedJob(request, job_root)
        return None

    def transition(self, job: ClaimedJob, state: str, *, detail: str = "") -> None:
        status_path = job.root / "status.json"
        current = json.loads(status_path.read_text(encoding="utf-8"))
        before = str(current["state"])
        if state not in TRANSITIONS.get(before, frozenset()):
            raise ValueError(f"invalid job transition: {before} -> {state}")
        value = {"job_id": job.request.job_id, "state": state, "updated_at": _now()}
        if detail:
            value["detail"] = detail
        _atomic_json(status_path, value)

    def recover_interrupted(self) -> list[str]:
        recovered: list[str] = []
        for status_path in sorted((self.root / "jobs").glob("*/status.json")):
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if status.get("state") not in {"claimed", "running"}:
                continue
            request = JobRequest.from_dict(json.loads(
                (status_path.parent / "request.json").read_text(encoding="utf-8")
            ))
            self.transition(
                ClaimedJob(request, status_path.parent),
                "interrupted",
                detail="runner restarted before the job reached a terminal state",
            )
            recovered.append(request.job_id)
        return recovered

    def write_artifact_manifest(self, job: ClaimedJob) -> Path:
        job.artifacts.mkdir(parents=True, exist_ok=True)
        files = []
        for path in sorted(job.artifacts.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"artifact symlink is forbidden: {path}")
            if path.is_file() and path != job.artifacts / "manifest.json":
                files.append({
                    "path": path.relative_to(job.artifacts).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256_file(path),
                })
        value = {
            "format": "qsys-research-artifacts",
            "version": 1,
            "job_id": job.request.job_id,
            "request_sha256": job.request.request_sha256,
            "created_at": _now(),
            "file_count": len(files),
            "content_sha256": _sha256_bytes(files),
            "files": files,
        }
        log_path = job.root / "run.log"
        if log_path.is_file():
            value["log"] = {
                "bytes": log_path.stat().st_size,
                "sha256": _sha256_file(log_path),
            }
        target = job.artifacts / "manifest.json"
        _atomic_json(target, value)
        return target
