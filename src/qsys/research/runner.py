"""Single-concurrency, fail-closed Research Runner service."""

from __future__ import annotations

import hashlib
import os
import re
import resource
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from qsys.data.snapshots import SNAPSHOT_FORMAT, SNAPSHOT_VERSION, load_snapshot
from qsys.research.jobs import ClaimedJob, JobLimits, JobStore
from qsys.research.task_worker import TASKS


TEST_DRILL_TASKS = frozenset({
    "isolation-permissions", "resource-pressure-cpu-io", "resource-pressure-memory",
})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 << 20):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class RunnerIdentity:
    code_sha: str
    wheel: Path
    max_limits: JobLimits
    wheel_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if self.wheel.is_symlink():
            raise ValueError("Runner wheel path must not be a symlink")
        wheel = self.wheel.resolve(strict=True)
        if not wheel.is_file():
            raise FileNotFoundError(wheel)
        object.__setattr__(self, "wheel", wheel)
        object.__setattr__(self, "wheel_sha256", _sha256(wheel))


class ResearchRunner:
    def __init__(
        self,
        store: JobStore,
        snapshot_store: Path,
        identity: RunnerIdentity,
        *,
        poll_seconds: float = 1.0,
        allow_test_drills: bool = False,
    ) -> None:
        self.store = store
        self.snapshot_store = Path(snapshot_store).resolve(strict=True)
        self.identity = identity
        self.poll_seconds = poll_seconds
        self.allow_test_drills = allow_test_drills
        self._stopping = False
        identity.max_limits.validate()
        if not re.fullmatch(r"[0-9a-f]{40}", identity.code_sha):
            raise ValueError("Runner code_sha must be a full lowercase commit SHA")

    def stop(self) -> None:
        self._stopping = True

    def serve(self) -> None:
        self.store.recover_interrupted()
        while not self._stopping:
            if not self.run_once():
                time.sleep(max(0.1, self.poll_seconds))

    def run_once(self) -> bool:
        job = self.store.claim_next()
        if job is None:
            return False
        try:
            snapshot = self._validate_job(job)
        except Exception as exc:
            self.store.transition(job, "failed", detail=str(exc))
            return True
        if self.store.cancel_requested(job.request.job_id):
            self.store.transition(job, "cancelled", detail="cancelled before execution")
            return True

        self.store.transition(job, "running")
        log_path = job.root / "run.log"
        command = [
            sys.executable,
            "-m",
            "qsys.research.task_worker",
            "--request",
            str(job.root / "request.json"),
            "--snapshot",
            str(snapshot),
            "--artifacts",
            str(job.artifacts),
        ]
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        with log_path.open("ab") as log:
            process = subprocess.Popen(
                command,
                cwd=job.root,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            result, usage = self._wait(job, process)
        self.store.write_resource_usage(job, usage)
        if result == "cancelled":
            self.store.transition(job, "cancelled", detail="cancel requested")
        elif result == "timeout":
            self.store.transition(job, "failed", detail="job exceeded timeout_seconds")
        elif process.returncode != 0:
            self.store.transition(job, "failed", detail=f"worker exited with {process.returncode}")
        else:
            self.store.write_artifact_manifest(job)
            self.store.transition(job, "succeeded")
        return True

    def _validate_job(self, job: ClaimedJob) -> Path:
        request = job.request
        if request.code_sha != self.identity.code_sha:
            raise ValueError("job code_sha does not match this Runner release")
        if request.wheel_sha256 != self.identity.wheel_sha256:
            raise ValueError("job wheel_sha256 does not match this Runner release")
        if request.task not in TASKS:
            raise ValueError(f"task is not allowlisted: {request.task}")
        if request.task in TEST_DRILL_TASKS and not self.allow_test_drills:
            raise ValueError("test drill task is disabled for this Runner")
        requested = request.limits
        maximum = self.identity.max_limits
        # The service runs exactly one child at a time. Its systemd cgroup is
        # therefore the job cgroup, and the request must record those actual
        # limits rather than implying a narrower limit that is not enforced.
        if requested.memory_max_mb != maximum.memory_max_mb:
            raise ValueError("job memory_max_mb differs from the Runner cgroup ceiling")
        if requested.cpu_quota_percent != maximum.cpu_quota_percent:
            raise ValueError("job cpu_quota_percent differs from the Runner cgroup ceiling")
        if requested.io_weight != maximum.io_weight:
            raise ValueError("job io_weight differs from the Runner cgroup ceiling")
        if requested.timeout_seconds > maximum.timeout_seconds:
            raise ValueError("job timeout_seconds exceeds the Runner ceiling")

        snapshot = self.snapshot_store / "snapshots" / request.snapshot_id
        resolved = snapshot.resolve(strict=True)
        snapshots_root = (self.snapshot_store / "snapshots").resolve(strict=True)
        if resolved.parent != snapshots_root:
            raise ValueError("job snapshot escapes the snapshot store")
        manifest = load_snapshot(resolved).manifest
        if (manifest.get("format") != SNAPSHOT_FORMAT
                or manifest.get("version") != SNAPSHOT_VERSION):
            raise ValueError("job snapshot manifest format or version is unsupported")
        if manifest.get("snapshot_id") != request.snapshot_id:
            raise ValueError("job snapshot_id does not match its manifest")
        if manifest.get("content_sha256") != request.snapshot_content_sha256:
            raise ValueError("job snapshot_content_sha256 does not match its manifest")
        return resolved

    def _wait(
        self, job: ClaimedJob, process: subprocess.Popen,
    ) -> tuple[str, dict[str, object]]:
        started = time.monotonic()
        deadline = time.monotonic() + job.request.limits.timeout_seconds
        while True:
            pid, status, usage = os.wait4(process.pid, os.WNOHANG)
            if pid:
                process.returncode = os.waitstatus_to_exitcode(status)
                return "completed", self._resource_usage(
                    usage, time.monotonic() - started, process.returncode,
                )
            if self._stopping or self.store.cancel_requested(job.request.job_id):
                usage = self._terminate(process)
                return "cancelled", self._resource_usage(
                    usage, time.monotonic() - started, process.returncode,
                )
            if time.monotonic() >= deadline:
                usage = self._terminate(process)
                return "timeout", self._resource_usage(
                    usage, time.monotonic() - started, process.returncode,
                )
            time.sleep(0.1)

    @staticmethod
    def _terminate(process: subprocess.Popen) -> resource.struct_rusage:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            pid, status, usage = os.wait4(process.pid, os.WNOHANG)
            if pid:
                process.returncode = os.waitstatus_to_exitcode(status)
                return usage
            time.sleep(0.05)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _, status, usage = os.wait4(process.pid, 0)
        process.returncode = os.waitstatus_to_exitcode(status)
        return usage

    @staticmethod
    def _resource_usage(
        usage: resource.struct_rusage, wall_seconds: float, exit_code: int,
    ) -> dict[str, object]:
        # Linux reports KiB; macOS/BSD report bytes. Production is Linux, while
        # normalizing here keeps local contract tests portable.
        max_rss_bytes = int(usage.ru_maxrss * (1024 if sys.platform.startswith("linux") else 1))
        return {
            "format": "qsys-research-resource-usage",
            "version": 1,
            "wall_seconds": round(wall_seconds, 6),
            "user_cpu_seconds": round(float(usage.ru_utime), 6),
            "system_cpu_seconds": round(float(usage.ru_stime), 6),
            "max_rss_bytes": max_rss_bytes,
            "input_blocks": int(usage.ru_inblock),
            "output_blocks": int(usage.ru_oublock),
            "voluntary_context_switches": int(usage.ru_nvcsw),
            "involuntary_context_switches": int(usage.ru_nivcsw),
            "exit_code": exit_code,
        }
