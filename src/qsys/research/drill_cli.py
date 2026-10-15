"""Run the bounded, test-only Research Runner isolation acceptance drill."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from qsys.data.snapshots import load_snapshot
from qsys.research.jobs import JobLimits, JobRequest, JobStore


UNIT = "qsys-research-runner-test.service"
LIMITS = JobLimits(512, 25, 10, 900)


def _run(*command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, capture_output=True)


def _service_identity(service: str) -> dict[str, str]:
    output = _run(
        "systemctl", "show", service, "--no-pager",
        "-p", "ActiveState", "-p", "SubState", "-p", "MainPID",
        "-p", "ExecMainStartTimestampMonotonic", "-p", "NRestarts",
    ).stdout
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def _runner_properties() -> dict[str, str]:
    names = (
        "MemoryHigh", "MemoryMax", "MemorySwapMax", "CPUQuotaPerSecUSec",
        "CPUWeight", "IOWeight",
        "Nice", "OOMScoreAdjust", "PrivateNetwork", "RestrictAddressFamilies",
        "IPAddressDeny", "NoNewPrivileges", "UnitFileState",
    )
    command = ["systemctl", "show", UNIT, "--no-pager"]
    for name in names:
        command.extend(("-p", name))
    values = dict(
        line.split("=", 1) for line in _run(*command).stdout.splitlines() if "=" in line
    )
    expected = {
        "MemoryHigh": "402653184", "MemoryMax": "536870912", "MemorySwapMax": "0",
        "CPUQuotaPerSecUSec": "250ms", "CPUWeight": "10", "IOWeight": "10",
        "Nice": "10", "OOMScoreAdjust": "900", "PrivateNetwork": "yes",
        "RestrictAddressFamilies": "AF_UNIX", "IPAddressDeny": "0.0.0.0/0 ::/0",
        "NoNewPrivileges": "yes", "UnitFileState": "static",
    }
    if values != expected:
        raise RuntimeError(f"test Runner systemd properties differ from contract: {values}")
    return values


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _wait_job(store: Path, job_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = store / "jobs" / job_id / "status.json"
        if status.is_file():
            value = _json(status)
            if value.get("state") in {"succeeded", "failed", "cancelled", "interrupted"}:
                return value
        time.sleep(0.25)
    raise TimeoutError(f"Research Runner job did not finish: {job_id}")


def _enqueue(
    store: JobStore, *, job_id: str, code_sha: str, wheel_sha256: str,
    snapshot_id: str, snapshot_sha256: str, task: str,
) -> None:
    store.enqueue(JobRequest.create(
        job_id=job_id,
        code_sha=code_sha,
        wheel_sha256=wheel_sha256,
        snapshot_id=snapshot_id,
        snapshot_content_sha256=snapshot_sha256,
        task=task,
        parameters={},
        limits=LIMITS,
    ))


def _job_evidence(store: Path, job_id: str) -> dict[str, Any]:
    root = store / "jobs" / job_id
    value: dict[str, Any] = {
        "request": _json(root / "request.json"),
        "status": _json(root / "status.json"),
    }
    for key, path in {
        "resources": root / "resources.json",
        "artifacts": root / "artifacts" / "manifest.json",
    }.items():
        if path.is_file():
            value[key] = _json(path)
    return value


class HealthMonitor:
    def __init__(self, services: tuple[str, ...], health_url: str) -> None:
        self.services = services
        self.health_url = health_url
        self.before = {service: _service_identity(service) for service in services}
        self.samples = 0
        self.failures: list[str] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        self._thread.join(timeout=10)
        after = {service: _service_identity(service) for service in self.services}
        for service in self.services:
            if after[service] != self.before[service]:
                self.failures.append(f"service identity changed: {service}")
        return {
            "samples": self.samples,
            "failures": self.failures,
            "before": self.before,
            "after": after,
        }

    def _serve(self) -> None:
        while not self._stop.wait(1):
            self.samples += 1
            for service, expected in self.before.items():
                try:
                    actual = _service_identity(service)
                    if actual != expected or actual.get("ActiveState") != "active":
                        self.failures.append(f"service unhealthy or changed: {service}")
                except (OSError, subprocess.SubprocessError) as exc:
                    self.failures.append(f"service probe failed: {service}: {exc}")
            try:
                with urllib.request.urlopen(self.health_url, timeout=3) as response:
                    if response.status != 200:
                        self.failures.append(f"HTTP health returned {response.status}")
            except OSError as exc:
                self.failures.append(f"HTTP health failed: {exc}")


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        raise ValueError("drill SHA must be 40 lowercase hexadecimal characters")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.job_prefix):
        raise ValueError("drill job prefix is invalid")
    release = args.release.resolve(strict=True)
    manifest = _json(release / "release.json")
    if manifest.get("source_sha") != args.sha or manifest.get("version") != 2:
        raise ValueError("staged release identity does not match the drill SHA")
    wheel_filename = manifest.get("wheel_filename")
    if not isinstance(wheel_filename, str) or Path(wheel_filename).name != wheel_filename:
        raise ValueError("staged release wheel filename is invalid")
    wheel = release / "dist" / wheel_filename
    wheel_sha256 = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if wheel_sha256 != manifest.get("wheel_sha256"):
        raise ValueError("staged wheel hash does not match its release manifest")
    snapshot = load_snapshot((args.snapshots / "current").resolve(strict=True))
    snapshot_sha256 = str(snapshot.manifest["content_sha256"])
    store = JobStore(args.jobs, initialize=False)

    enabled = _run("systemctl", "is-enabled", UNIT, check=False).stdout.strip()
    if _run("systemctl", "is-active", "--quiet", UNIT, check=False).returncode == 0:
        raise RuntimeError("test Research Runner must be inactive before the drill")
    if enabled in {"enabled", "enabled-runtime", "linked", "linked-runtime"}:
        raise RuntimeError("test Research Runner must not be enabled")
    runner_properties = _runner_properties()

    monitor = HealthMonitor((args.live_service, args.test_service), args.health_url)
    jobs: dict[str, dict[str, Any]] = {}
    monitor.start()
    try:
        _run("systemctl", "start", UNIT)
        if _run("systemctl", "is-active", "--quiet", UNIT, check=False).returncode != 0:
            raise RuntimeError("test Research Runner failed to become active")
        plan = (
            ("permissions", "isolation-permissions", 120.0),
            ("verify-a", "verify-snapshot", 930.0),
            ("verify-b", "verify-snapshot", 930.0),
            ("cpu-io", "resource-pressure-cpu-io", 120.0),
            ("memory", "resource-pressure-memory", 120.0),
        )
        for suffix, task, timeout in plan:
            job_id = f"{args.job_prefix}-{suffix}"
            _enqueue(
                store, job_id=job_id, code_sha=args.sha,
                wheel_sha256=wheel_sha256, snapshot_id=snapshot.snapshot_id,
                snapshot_sha256=snapshot_sha256, task=task,
            )
            status = _wait_job(args.jobs, job_id, timeout)
            jobs[suffix] = _job_evidence(args.jobs, job_id)
            expected = "failed" if suffix == "memory" else "succeeded"
            if status.get("state") != expected:
                raise RuntimeError(f"{job_id} ended as {status.get('state')}, expected {expected}")

        verify_hashes = []
        for suffix in ("verify-a", "verify-b"):
            files = jobs[suffix]["artifacts"]["files"]
            match = [item["sha256"] for item in files if item["path"] == "snapshot-verification.json"]
            if len(match) != 1:
                raise RuntimeError(f"{suffix} lacks one snapshot verification artifact")
            verify_hashes.append(match[0])
        if len(set(verify_hashes)) != 1:
            raise RuntimeError("repeated snapshot verification artifacts differ")

        cpu = jobs["cpu-io"]["resources"]
        cpu_seconds = float(cpu["user_cpu_seconds"]) + float(cpu["system_cpu_seconds"])
        wall_seconds = float(cpu["wall_seconds"])
        if not (wall_seconds >= 7.5 and 0.5 <= cpu_seconds <= wall_seconds * 0.40):
            raise RuntimeError(f"CPU quota evidence is outside bounds: cpu={cpu_seconds}, wall={wall_seconds}")
        if int(cpu["output_blocks"]) <= 0:
            raise RuntimeError("IO pressure did not record output blocks")

        memory = jobs["memory"]["resources"]
        if int(memory["exit_code"]) not in {-9, 137}:
            raise RuntimeError(f"memory pressure was not OOM-killed: {memory}")
        memory_rss = int(memory["max_rss_bytes"])
        if not (256 * 1024 * 1024 <= memory_rss <= 544 * 1024 * 1024):
            raise RuntimeError(f"memory pressure peak is outside the cgroup bound: {memory_rss}")
        memory_peak = _run(
            "systemctl", "show", UNIT, "--property=MemoryPeak", "--value",
        ).stdout.strip()
    finally:
        _run("systemctl", "stop", UNIT, check=False)
        health = monitor.stop()

    if health["failures"]:
        raise RuntimeError("health continuity failed: " + "; ".join(health["failures"][:5]))
    if _run("systemctl", "is-active", "--quiet", UNIT, check=False).returncode == 0:
        raise RuntimeError("test Research Runner remained active after the drill")
    return {
        "ok": True,
        "sha": args.sha,
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_content_sha256": snapshot_sha256,
        "repeated_verification_sha256": verify_hashes[0],
        "runner_memory_peak": memory_peak,
        "runner_properties": runner_properties,
        "health": health,
        "jobs": jobs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--snapshots", required=True, type=Path)
    parser.add_argument("--jobs", required=True, type=Path)
    parser.add_argument("--job-prefix", required=True)
    parser.add_argument("--live-service", required=True)
    parser.add_argument("--test-service", required=True)
    parser.add_argument("--health-url", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run(args), ensure_ascii=False, sort_keys=True))
    return 0
