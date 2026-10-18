"""Monthly studies reuse the snapshot-only Research Runner boundary."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from qsys.data.snapshots import load_snapshot
from qsys.data.query_budget import query_budget
from qsys.research.jobs import JobRequest


MONTHLY_TIMEOUTS = {"auction-timing-study": 1800, "gate-study": 3600}


def _date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"monthly study {field} must be an ISO date")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError(f"monthly study {field} must be an ISO date")
    return parsed


def validate_monthly_request(request: JobRequest, snapshot_root: Path) -> tuple[date, date, str]:
    if request.task not in MONTHLY_TIMEOUTS:
        raise ValueError("unsupported monthly study task")
    parameters = request.parameters
    if set(parameters) != {"start", "end", "market_data_facade"}:
        raise ValueError("monthly study parameters are incomplete or unexpected")
    start, end = _date(parameters["start"], "start"), _date(parameters["end"], "end")
    facade = parameters["market_data_facade"]
    if facade not in ("legacy", "canonical"):
        raise ValueError("monthly study requires an explicit legacy/canonical facade")
    if start > end:
        raise ValueError("monthly study start must not follow end")
    if request.limits.timeout_seconds > MONTHLY_TIMEOUTS[request.task]:
        raise ValueError("monthly study timeout exceeds its task ceiling")
    snapshot = load_snapshot(snapshot_root)
    if (snapshot.snapshot_id != request.snapshot_id
            or snapshot.manifest["content_sha256"] != request.snapshot_content_sha256):
        raise ValueError("monthly study snapshot identity mismatch")
    if end > _date(snapshot.manifest["complete_through"], "snapshot complete_through"):
        raise ValueError("monthly study end exceeds snapshot complete_through")
    return start, end, facade


@contextmanager
def _facade(value: str):
    name = "QSYS_MARKET_DATA_FACADE"
    previous = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def _context(request: JobRequest, artifacts: Path, budget: dict) -> None:
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "study-context.json").write_text(json.dumps({
        "schema_version": 1,
        "task": request.task,
        "code_sha": request.code_sha,
        "snapshot_id": request.snapshot_id,
        "snapshot_content_sha256": request.snapshot_content_sha256,
        "parameters": dict(request.parameters),
        "duckdb_budget_per_connection": budget,
    }, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _execute(main, argv: list[str]) -> None:
    result = main(argv)
    if result != 0:
        raise RuntimeError(f"monthly study command failed with exit code {result}")


def _budget(request: JobRequest, artifacts: Path):
    # Reserve the remaining cgroup memory for Python/Polars and model state.
    # These are hard query limits, not a claim that a production job fits them.
    return query_budget(memory_limit_mb=max(1, request.limits.memory_max_mb // 2),
                        temp_root=artifacts.parent / "duckdb-temp",
                        max_temp_directory_mb=1024,
                        threads=max(1, request.limits.cpu_quota_percent // 100))


def auction_timing_study_task(request: JobRequest, snapshot_root: Path, artifacts: Path) -> None:
    from qsys.research.auction_timing_study_cli import main

    start, end, facade = validate_monthly_request(request, snapshot_root)
    with _facade(facade), _budget(request, artifacts) as budget:
        _context(request, artifacts, budget)
        _execute(main, [
            "--warehouse", str(snapshot_root / "warehouse"),
            "--start", start.isoformat(), "--end", end.isoformat(),
            "--market-data-facade", facade,
            "--out", str(artifacts / "auction-timing"),
            "--service-runs", str(artifacts / "service-runs"),
        ])


def gate_study_task(request: JobRequest, snapshot_root: Path, artifacts: Path) -> None:
    from qsys.research.gate_study_cli import main as study
    from qsys.research.structural_backtest_cli import main as backtest

    start, end, facade = validate_monthly_request(request, snapshot_root)
    runs = artifacts / "backtests"
    run = runs / "gate"
    with _facade(facade), _budget(request, artifacts) as budget:
        _context(request, artifacts, budget)
        # Keep the legacy monthly command's engine, strategy and cost wiring.
        _execute(backtest, [
            "--warehouse", str(snapshot_root / "warehouse"),
            "--start", start.isoformat(), "--end", end.isoformat(),
            "--cash", "100000", "--entry-fill", "continuous",
            "--entry-price-buffer", "0.03", "--sizing-price-buffer", "0.005",
            "--auction-at", "09:25:10", "--market-data-facade", facade,
            "--out", str(run / "equity.csv"),
            "--runs-dir", str(runs), "--run-name", "gate",
        ])
        _execute(study, [
            "--warehouse", str(snapshot_root / "warehouse"),
            "--decisions", str(run / "gate_decisions.csv"),
            "--horizon", "20", "--market-data-facade", facade,
            "--out", str(artifacts / "gate-study"),
            "--service-runs", str(artifacts / "service-runs"),
        ])
