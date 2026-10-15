#!/usr/bin/env python3
"""Fail-closed admission checks for the production-hosted CI runner."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
from pathlib import Path


MIN_AVAILABLE_BYTES = 3 * 1024**3
MIN_FREE_DISK_BYTES = 15 * 1024**3
MAX_IO_FULL_AVG10 = 5.0
BLACKOUT_START = dt.time(18, 25)
BLACKOUT_END = dt.time(21, 0)
HIGH_WATER_UNITS = (
    "qsys-tushare-sync.service",
    "qsys-catalog-verify.service",
    "qsys-daily-archive.service",
    "qsys-auction-study.service",
    "qsys-gate-study.service",
)
REQUIRED_UNITS = ("qsys-live-core.service", "qsys-monitor.service")


def parse_mem_available(text: str) -> int:
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise ValueError("MemAvailable is missing")


def parse_pressure_avg10(text: str, category: str = "full") -> float:
    for line in text.splitlines():
        fields = line.split()
        if fields and fields[0] == category:
            for field in fields[1:]:
                if field.startswith("avg10="):
                    return float(field.removeprefix("avg10="))
    raise ValueError(f"{category} avg10 is missing")


def in_blackout(now: dt.datetime) -> bool:
    local_time = now.timetz().replace(tzinfo=None)
    return BLACKOUT_START <= local_time < BLACKOUT_END


def audit_values(
    *,
    enabled: bool,
    now: dt.datetime,
    available_bytes: int,
    free_disk_bytes: int,
    io_full_avg10: float,
    active_units: set[str],
    failed_units: set[str],
) -> dict[str, object]:
    errors: list[str] = []
    if not enabled:
        errors.append("operator activation gate is absent")
    if in_blackout(now):
        errors.append("production high-water blackout is active")
    if available_bytes < MIN_AVAILABLE_BYTES:
        errors.append("available memory is below 3 GiB")
    if free_disk_bytes < MIN_FREE_DISK_BYTES:
        errors.append("system disk free space is below 15 GiB")
    if io_full_avg10 > MAX_IO_FULL_AVG10:
        errors.append("I/O full pressure avg10 exceeds 5 percent")
    running_batch = sorted(active_units.intersection(HIGH_WATER_UNITS))
    if running_batch:
        errors.append("production batch unit is active")
    missing_required = sorted(set(REQUIRED_UNITS).difference(active_units))
    if missing_required:
        errors.append("required production service is not active")
    if failed_units:
        errors.append("systemd has failed units")
    return {
        "ok": not errors,
        "errors": errors,
        "available_bytes": available_bytes,
        "free_disk_bytes": free_disk_bytes,
        "io_full_avg10": io_full_avg10,
        "active_high_water_units": running_batch,
        "missing_required_units": missing_required,
        "failed_units": sorted(failed_units),
    }


def _unit_set(state: str) -> set[str]:
    result = subprocess.run(
        ["systemctl", "list-units", "--type=service", "--state", state,
         "--no-legend", "--plain"],
        check=True,
        capture_output=True,
        text=True,
    )
    return {line.split()[0] for line in result.stdout.splitlines() if line.split()}


def audit_host(enable_file: Path, disk_path: Path) -> dict[str, object]:
    return audit_values(
        enabled=enable_file.is_file(),
        now=dt.datetime.now().astimezone(),
        available_bytes=parse_mem_available(Path("/proc/meminfo").read_text()),
        free_disk_bytes=shutil.disk_usage(disk_path).free,
        io_full_avg10=parse_pressure_avg10(Path("/proc/pressure/io").read_text()),
        active_units=_unit_set("active"),
        failed_units=_unit_set("failed"),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--enable-file",
        type=Path,
        default=Path(os.environ.get("QSYS_CI_ENABLE_FILE", "/nonexistent")),
    )
    parser.add_argument(
        "--disk-path",
        type=Path,
        default=Path(os.environ.get("QSYS_CI_STATE_DIR", "/var/lib/qsys-ci-runner")),
    )
    args = parser.parse_args()
    result = audit_host(args.enable_file, args.disk_path)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
