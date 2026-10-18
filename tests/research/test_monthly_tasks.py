from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from qsys.data.snapshots import create_snapshot
from qsys.research import monthly_submit_cli, monthly_tasks, task_worker
from qsys.research.jobs import JobLimits, JobRequest, JobStore
from qsys.web.research_jobs import ResearchSubmissionProfile


@pytest.fixture
def snapshot(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "sample.txt").write_text("immutable input\n")
    return create_snapshot(source, tmp_path / "snapshots", complete_through="2026-10-13",
                           schema_version="test-v1", snapshot_id="close-20261013",
                           publish_current=True)


def request(snapshot, task="auction-timing-study", changes=None, **overrides):
    parameters = {"start": "2026-01-01", "end": "2026-10-13", "market_data_facade": "legacy"}
    parameters.update(changes or {})
    values = dict(job_id="monthly-1", code_sha="a" * 40, wheel_sha256="b" * 64,
                  snapshot_id=snapshot.snapshot_id,
                  snapshot_content_sha256=snapshot.manifest["content_sha256"],
                  task=task, parameters=parameters,
                  limits=JobLimits(4096, 100, 10, monthly_tasks.MONTHLY_TIMEOUTS[task]))
    values.update(overrides)
    return JobRequest.create(**values)


@pytest.mark.parametrize("changes,match", [
    ({"out": "/data/qsys/warehouse"}, "incomplete or unexpected"),
    ({"strategy": "user.py"}, "incomplete or unexpected"),
    ({"shell": "id"}, "incomplete or unexpected"),
    ({"market_data_facade": None}, "explicit"),
    ({"market_data_facade": "guess"}, "explicit"),
    ({"start": 20260101}, "ISO date"),
    ({"start": "20260101"}, "ISO date"),
    ({"start": "2026-11-01"}, "start must not follow"),
    ({"end": "2026-10-14"}, "complete_through"),
])
def test_monthly_request_rejects_paths_implicit_modes_and_out_of_snapshot_dates(snapshot, changes, match):
    with pytest.raises(ValueError, match=match):
        monthly_tasks.validate_monthly_request(request(snapshot, changes=changes), snapshot.root)


def test_monthly_request_binds_snapshot_identity_and_timeout(snapshot):
    with pytest.raises(ValueError, match="identity mismatch"):
        monthly_tasks.validate_monthly_request(
            request(snapshot, snapshot_content_sha256="c" * 64), snapshot.root,
        )
    with pytest.raises(ValueError, match="timeout"):
        monthly_tasks.validate_monthly_request(
            request(snapshot, limits=JobLimits(4096, 100, 10, 1801)), snapshot.root,
        )


def test_auction_task_uses_only_snapshot_and_artifacts(snapshot, tmp_path, monkeypatch):
    from qsys.research import auction_timing_study_cli as auction

    received = []
    def run(argv):
        received.extend(argv)
        assert monthly_tasks.os.environ["QSYS_MARKET_DATA_FACADE"] == "legacy"
        return 0
    monkeypatch.setattr(auction, "main", run)
    monkeypatch.setenv("QSYS_MARKET_DATA_FACADE", "canonical")
    artifacts = tmp_path / "job" / "artifacts"
    monthly_tasks.auction_timing_study_task(request(snapshot), snapshot.root, artifacts)
    assert received[received.index("--warehouse") + 1] == str(snapshot.warehouse)
    assert received[received.index("--out") + 1] == str(artifacts / "auction-timing")
    assert received[received.index("--service-runs") + 1] == str(artifacts / "service-runs")
    assert received[received.index("--start") + 1] == "2026-01-01"
    assert monthly_tasks.os.environ["QSYS_MARKET_DATA_FACADE"] == "canonical"
    context = json.loads((artifacts / "study-context.json").read_text())
    assert context["snapshot_content_sha256"] == snapshot.manifest["content_sha256"]
    assert context["parameters"]["market_data_facade"] == "legacy"


@pytest.mark.parametrize("failure", [False, True])
def test_gate_task_preserves_original_monthly_wiring_and_failure_order(
    snapshot, tmp_path, monkeypatch, failure,
):
    from qsys.research import gate_study_cli as study, structural_backtest_cli as backtest

    artifacts = tmp_path / "job" / "artifacts"
    calls = []
    def simulate(argv):
        calls.append("backtest")
        expected = {"--entry-fill": "continuous", "--entry-price-buffer": "0.03",
                    "--sizing-price-buffer": "0.005", "--auction-at": "09:25:10",
                    "--cash": "100000", "--warehouse": str(snapshot.warehouse),
                    "--market-data-facade": "legacy", "--run-name": "gate"}
        for flag, value in expected.items():
            assert argv[argv.index(flag) + 1] == value
        assert argv[argv.index("--runs-dir") + 1] == str(artifacts / "backtests")
        if failure:
            return 2
        root = artifacts / "backtests" / "gate"
        root.mkdir(parents=True)
        (root / "gate_decisions.csv").write_text("decision evidence\n")
        return 0
    def analyse(argv):
        calls.append("study")
        decisions = Path(argv[argv.index("--decisions") + 1])
        assert decisions == artifacts / "backtests" / "gate" / "gate_decisions.csv"
        assert decisions.read_text() == "decision evidence\n"
        assert argv[argv.index("--horizon") + 1] == "20"
        return 0
    monkeypatch.setattr(backtest, "main", simulate)
    monkeypatch.setattr(study, "main", analyse)
    monkeypatch.delenv("QSYS_MARKET_DATA_FACADE", raising=False)
    run = lambda: monthly_tasks.gate_study_task(request(snapshot, "gate-study"), snapshot.root, artifacts)
    if failure:
        with pytest.raises(RuntimeError, match="exit code 2"):
            run()
        assert calls == ["backtest"]
    else:
        run()
        assert calls == ["backtest", "study"]
    assert "QSYS_MARKET_DATA_FACADE" not in monthly_tasks.os.environ


def cli_rig(tmp_path, snapshot):
    release = tmp_path / "release"
    (release / "dist").mkdir(parents=True)
    wheel = release / "dist" / "qsys-test.whl"
    wheel.write_bytes(b"trusted release identity")
    manifest = {"format": "qsys-research-release", "version": 2,
                "source_sha": "a" * 40, "source_tree": "d" * 40,
                "wheel_filename": wheel.name, "wheel_bytes": wheel.stat().st_size,
                "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                "pyproject_sha256": "e" * 64, "uv_lock_sha256": "f" * 64, "ci": {}}
    (release / "release.json").write_text(json.dumps(manifest))
    store = JobStore(tmp_path / "jobs")
    argv = ["--jobs", str(store.root), "--release", str(release),
            "--snapshots", str(snapshot.root.parent.parent), "--job-id", "auction-202610-v1",
            "--task", "auction-timing-study", "--start", "2026-01-01", "--end", "2026-10-13",
            "--market-data-facade", "legacy", "--memory-max-mb", "4096",
            "--cpu-quota-percent", "100", "--io-weight", "10"]
    return argv, store, wheel


def test_submitter_binds_release_snapshot_limits_and_refuses_duplicate_id(snapshot, tmp_path):
    argv, store, wheel = cli_rig(tmp_path, snapshot)
    assert monthly_submit_cli.main(argv) == 0
    payload = json.loads((store.root / "inbox" / "auction-202610-v1.json").read_text())
    bound = JobRequest.from_dict(payload)
    assert bound.code_sha == "a" * 40
    assert bound.wheel_sha256 == hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert bound.snapshot_id == snapshot.snapshot_id
    assert bound.snapshot_content_sha256 == snapshot.manifest["content_sha256"]
    assert bound.limits == JobLimits(4096, 100, 10, 1800)
    assert not list((store.root / "jobs").iterdir())  # Submission never executes a worker.
    with pytest.raises(FileExistsError):
        monthly_submit_cli.main(argv)
    assert len(list((store.root / "inbox").iterdir())) == 1


def test_submitter_rejects_tampered_release_without_enqueuing(snapshot, tmp_path):
    argv, store, wheel = cli_rig(tmp_path, snapshot)
    wheel.write_bytes(b"changed")
    with pytest.raises(ValueError, match="wheel hash"):
        monthly_submit_cli.main(argv)
    assert not list((store.root / "inbox").iterdir())


@pytest.mark.parametrize("flag,value", [("--code-sha", "f" * 40), ("--out", "/tmp/output"),
                                       ("--task", "shell"), ("--job-id", "../escape")])
def test_submitter_rejects_identity_path_and_task_overrides(snapshot, tmp_path, flag, value):
    argv, store, _ = cli_rig(tmp_path, snapshot)
    if flag in argv:
        argv[argv.index(flag) + 1] = value
    else:
        argv += [flag, value]
    with pytest.raises((ValueError, SystemExit)):
        monthly_submit_cli.main(argv)
    assert not list((store.root / "inbox").iterdir())


def test_monthly_tasks_are_available_to_runner_but_not_default_web_submission(snapshot):
    assert task_worker.TASKS["auction-timing-study"] is monthly_tasks.auction_timing_study_task
    assert task_worker.TASKS["gate-study"] is monthly_tasks.gate_study_task
    profile = ResearchSubmissionProfile("a" * 40, "b" * 64, snapshot.root.parent.parent,
                                        JobLimits(4096, 100, 10, 3600))
    for task in monthly_tasks.MONTHLY_TIMEOUTS:
        with pytest.raises(ValueError, match="not enabled"):
            profile.request(task=task, parameters={})


@pytest.mark.parametrize("module", ["auction_timing_study_cli", "gate_study_cli",
                                    "structural_backtest_cli", "monthly_submit_cli"])
def test_monthly_commands_start_without_repository_cwd(tmp_path, module):
    result = subprocess.run([sys.executable, "-m", f"qsys.research.{module}", "--help"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
