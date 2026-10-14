from __future__ import annotations

from pathlib import Path

import pytest

from qsys.research import task_worker
from qsys.research.jobs import JobLimits, JobRequest


def _request(parameters: dict) -> JobRequest:
    return JobRequest.create(
        job_id="isolated-backtest", code_sha="a" * 40,
        wheel_sha256="b" * 64, snapshot_id="snapshot",
        snapshot_content_sha256="c" * 64, task="structural-backtest",
        parameters=parameters, limits=JobLimits(4096, 100, 10, 14_400),
    )


def _parameters() -> dict:
    return {
        "start": "2026-01-05", "end": "2026-01-09", "cash": 100_000,
        "params": {"gate_enabled": True}, "jq_compat": False,
        "entry_fill": "continuous",
    }


def test_structural_backtest_uses_snapshot_warehouse_and_job_artifacts(
    monkeypatch, tmp_path: Path,
) -> None:
    called = {}

    def fake_execute(strategy, **kwargs):
        called["strategy"] = strategy
        called.update(kwargs)
        kwargs["out_dir"].mkdir()
        (kwargs["out_dir"] / "config.json").write_text("{}\n", encoding="utf-8")
        return kwargs["out_dir"]

    monkeypatch.setattr(task_worker, "execute_backtest", fake_execute)
    snapshot = tmp_path / "snapshot"
    (snapshot / "warehouse").mkdir(parents=True)
    artifacts = tmp_path / "job" / "artifacts"
    artifacts.parent.mkdir()

    task_worker.structural_backtest_task(
        _request(_parameters()), snapshot, artifacts,
    )

    assert called["warehouse"] == snapshot / "warehouse"
    assert called["out_dir"] == artifacts
    assert called["strategy"].name == "structural_v1_2.py"
    assert called["entry_fill"] == "continuous"
    assert called["params"] == {"gate_enabled": True}
    assert "StructuralSelection" in called["strategy_source"]


@pytest.mark.parametrize(
    "change, match",
    [
        ({"shell": "id"}, "incomplete or unexpected"),
        ({"params": []}, "params must be an object"),
        ({"jq_compat": 1}, "jq_compat must be boolean"),
        ({"entry_fill": "arbitrary"}, "unsupported.*entry_fill"),
    ],
)
def test_structural_backtest_rejects_unbounded_inputs(tmp_path, change, match):
    parameters = _parameters()
    parameters.update(change)
    snapshot = tmp_path / "snapshot"
    (snapshot / "warehouse").mkdir(parents=True)

    with pytest.raises(ValueError, match=match):
        task_worker.structural_backtest_task(
            _request(parameters), snapshot, tmp_path / "artifacts",
        )
