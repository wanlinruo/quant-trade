from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest


def test_shadow_ranking_script_imports_without_running() -> None:
    path = Path("scripts/run_limit_up_shadow.py")
    spec = importlib.util.spec_from_file_location("run_limit_up_shadow", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main)


def test_shadow_observer_script_imports_without_running() -> None:
    path = Path("scripts/observe_limit_up_shadow.py")
    spec = importlib.util.spec_from_file_location("observe_limit_up_shadow", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main)


def test_shadow_drift_reference_script_imports_without_running() -> None:
    path = Path("scripts/build_limit_up_drift_reference.py")
    spec = importlib.util.spec_from_file_location("build_limit_up_drift_reference", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main)


def test_shadow_cycle_script_imports_without_running() -> None:
    path = Path("scripts/limit_up_shadow_cycle.py")
    spec = importlib.util.spec_from_file_location("limit_up_shadow_cycle", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main)


def test_shadow_cycle_surfaces_child_stderr(monkeypatch) -> None:
    from qsys.research import limit_up_shadow_cli as module

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(
            1, ["python", "child.py"], stderr="authoritative truth is missing",
        )

    monkeypatch.setattr(module.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="authoritative truth is missing"):
        module._run(["python", "child.py"])


@pytest.mark.parametrize("module", [
    "limit_up_shadow_cli", "limit_up_shadow_publish_cli", "limit_up_shadow_observe_cli",
])
def test_packaged_shadow_commands_work_outside_the_repository(module, tmp_path):
    completed = subprocess.run(
        [sys.executable, "-m", f"qsys.research.{module}", "--help"],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--warehouse" in completed.stdout


def test_cycle_observes_before_publishing_and_preserves_explicit_inputs(
    tmp_path, monkeypatch,
):
    from qsys.ops.services import load_runs
    from qsys.research import limit_up_shadow_cli as cycle

    commands = []
    session = date(2026, 10, 16)
    monkeypatch.setattr(cycle, "latest_feature_session", lambda root: session)
    monkeypatch.setattr(cycle, "expected_feature_session", lambda root, now: session)

    def execute(command, **kwargs):
        commands.append(command)
        assert command[:2] == [sys.executable, "-m"]
        assert kwargs == {"check": True, "capture_output": True, "text": True}
        payload = '{"recorded": [{"entries": 3}]}' if len(commands) == 1 else '{"entries": 20}'
        return subprocess.CompletedProcess(command, 0, payload + "\n", "")

    monkeypatch.setattr(cycle.subprocess, "run", execute)
    record_root = tmp_path / "service-runs" / "limit-up-shadow"
    paths = {name: str(tmp_path / name) for name in
             ("warehouse", "research-run", "drift-reference", "state", "config")}
    argv = ["qsys-limit-up-shadow", "--service-runs", str(record_root)]
    for name, path in paths.items():
        argv += [f"--{name}", path]
    monkeypatch.setattr(sys, "argv", argv + ["--threads", "3", "--min-eligible", "4321"])
    assert cycle.main() == 0
    assert [command[2] for command in commands] == [
        "qsys.research.limit_up_shadow_observe_cli",
        "qsys.research.limit_up_shadow_publish_cli",
    ]
    for command in commands:
        for name in ("warehouse", "state"):
            assert command[command.index(f"--{name}") + 1] == paths[name]
    publish = commands[1]
    for name in ("research-run", "drift-reference", "config"):
        assert publish[publish.index(f"--{name}") + 1] == paths[name]
    assert publish[publish.index("--threads") + 1] == "3"
    assert publish[publish.index("--min-eligible") + 1] == "4321"
    today = date.today()
    records = load_runs(tmp_path / "service-runs", today, today)
    assert len(records) == 1
    assert records[0]["status"] == "succeeded"
    assert records[0]["session"] == session.isoformat()
    assert [step["rows"] for step in records[0]["steps"]] == [1, 20]


def test_observation_failure_prevents_ranking_publication(tmp_path, monkeypatch):
    from qsys.research import limit_up_shadow_cli as cycle

    commands = []
    monkeypatch.setattr(cycle, "latest_feature_session", lambda root: date(2026, 10, 16))

    def execute(command, **kwargs):
        commands.append(command)
        raise subprocess.CalledProcessError(1, command, stderr="truth is missing")

    monkeypatch.setattr(cycle.subprocess, "run", execute)
    argv = ["qsys-limit-up-shadow"]
    for name in ("warehouse", "research-run", "drift-reference", "state", "config", "service-runs"):
        argv += [f"--{name}", str(tmp_path / name)]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(RuntimeError, match="truth is missing"):
        cycle.main()
    assert len(commands) == 1
    assert commands[0][2] == "qsys.research.limit_up_shadow_observe_cli"


def _cycle_argv(tmp_path):
    argv = ["qsys-limit-up-shadow"]
    for name in ("warehouse", "research-run", "drift-reference", "state", "config", "service-runs"):
        argv += [f"--{name}", str(tmp_path / name)]
    return argv


@pytest.mark.parametrize("expected,reason", [
    (date(2026, 11, 10), "上游当日数据未到"), (None, "交易日历不可用"),
])
def test_stale_features_warn_and_do_not_publish_an_old_day(tmp_path, monkeypatch, expected, reason):
    """#636：11-10 tushare-sync 失败时，影子榜单不能以成功+跳过旧日结束。"""
    from qsys.ops.services import load_runs
    from qsys.research import limit_up_shadow_cli as cycle

    commands = []
    monkeypatch.setattr(cycle, "latest_feature_session", lambda root: date(2026, 11, 9))
    monkeypatch.setattr(cycle, "expected_feature_session", lambda root, now: expected)

    def execute(command, **kwargs):
        commands.append(command[2])
        return subprocess.CompletedProcess(command, 0, '{"recorded": []}\n', "")

    monkeypatch.setattr(cycle.subprocess, "run", execute)
    monkeypatch.setattr(sys, "argv", _cycle_argv(tmp_path))
    assert cycle.main() == 0
    assert commands == ["qsys.research.limit_up_shadow_observe_cli"]
    today = date.today()
    record = load_runs(tmp_path / "service-runs", today, today)[0]
    assert record["status"] == "warning"
    ready = next(step for step in record["steps"] if step["name"] == "上游数据就绪")
    assert ready["status"] == "warning" and reason in ready["detail"]
    assert "2026-11-09" in ready["detail"]


def test_expected_feature_session_uses_stored_calendar_and_close_cutoff(tmp_path):
    from datetime import datetime

    import polars as pl

    from qsys.research.limit_up_shadow import expected_feature_session

    folder = tmp_path / "trading_calendar" / "year=2026"
    folder.mkdir(parents=True)
    pl.DataFrame({
        "market": ["SSE"] * 4, "dt": [date(2026, 11, d) for d in (7, 9, 10, 11)],
        "is_open": [False, True, True, True], "pretrade_date": [None] * 4,
    }).write_parquet(folder / "part.parquet")
    assert expected_feature_session(tmp_path, datetime(2026, 11, 10, 19, 35)) == date(2026, 11, 10)
    assert expected_feature_session(tmp_path, datetime(2026, 11, 11, 0, 3)) == date(2026, 11, 10)
    assert expected_feature_session(tmp_path, datetime(2026, 11, 10, 15, 59)) == date(2026, 11, 9)
    assert expected_feature_session(tmp_path / "missing", datetime(2026, 11, 10, 19, 35)) is None
