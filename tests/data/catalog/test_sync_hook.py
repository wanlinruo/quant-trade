"""同步后的验证挂钩；验证故障不得把已落盘同步改报失败。"""

from datetime import date
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from qsys.data import tushare_sync_cli as tushare_daily_sync
from qsys.ops.services import record_step

D = date(2026, 9, 15)


def test_verification_runs_after_successful_sync(monkeypatch, warehouse):
    called = {}

    def fake_verify(actual_warehouse, day):
        called["warehouse"] = actual_warehouse
        called["day"] = day
        return 0

    monkeypatch.setattr(
        tushare_daily_sync, "run_incremental_verification", fake_verify
    )
    tushare_daily_sync.after_sync(warehouse, D, ok=True)
    assert called == {"warehouse": warehouse, "day": D}


def test_verification_is_skipped_after_failed_sync(monkeypatch, warehouse):
    called = {}
    monkeypatch.setattr(
        tushare_daily_sync,
        "run_incremental_verification",
        lambda actual_warehouse, day: called.setdefault("ran", True),
    )
    tushare_daily_sync.after_sync(warehouse, D, ok=False)
    assert "ran" not in called


def test_crashing_verifier_does_not_fail_sync(monkeypatch, warehouse):
    def boom(actual_warehouse, day):
        raise RuntimeError("验证器炸了")

    monkeypatch.setattr(tushare_daily_sync, "run_incremental_verification", boom)
    tushare_daily_sync.after_sync(warehouse, D, ok=True)


def test_main_calls_hook_after_facts_and_derived_refresh(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(
        tushare_daily_sync, "load_tushare_config",
        lambda path: SimpleNamespace(token="test"),
    )
    monkeypatch.setattr(tushare_daily_sync, "TushareClient", lambda token: object())
    monkeypatch.setattr(tushare_daily_sync, "TushareProvider", lambda client: object())
    monkeypatch.setattr(
        tushare_daily_sync, "sync",
        lambda provider, warehouse, day, run=None: events.append("facts") or {"facts": 1},
    )
    monkeypatch.setattr(
        tushare_daily_sync, "refresh_derived",
        lambda warehouse, day, run=None, next_session=None: events.append("derived") or {"derived": 1},
    )
    monkeypatch.setattr(
        tushare_daily_sync, "publish_canonical_after_sync",
        lambda provider, warehouse, day, run=None: events.append("canonical") or {"canonical": 1},
    )
    monkeypatch.setattr(
        tushare_daily_sync, "after_sync",
        lambda warehouse, day, ok, run=None, artifacts=None: events.append(("verify", ok)),
    )
    monkeypatch.setattr(tushare_daily_sync, "next_open_day", lambda client, day: None)
    monkeypatch.setattr(
        "sys.argv",
        ["tushare_daily_sync.py", "--warehouse", str(tmp_path), "--day", str(D),
         "--service-runs", str(tmp_path / "runs")],
    )
    assert tushare_daily_sync.main() == 0
    assert events == ["facts", "derived", "canonical", ("verify", True)]
    assert len(list((tmp_path / "runs").rglob("*.json"))) == 1


def test_explicit_samples_work_from_unwritable_service_cwd(monkeypatch, warehouse, tmp_path):
    import polars as pl
    from qsys.data.catalog import checks as C
    from qsys.data.query import WarehouseQuery

    sample = pl.DataFrame({'symbol': ['600000.SH']})
    check = C.Check('X.sample', C.Category.TRUTHFULNESS, C.Scope.INCREMENTAL,
                    ('tushare_daily.close',), (), '测试样本',
                    lambda _: C.CheckResult(1, 1, sample=sample))
    monkeypatch.setattr(C, 'CHECKS', {check.id: check})
    monkeypatch.chdir('/')
    target = tmp_path / 'explicit-samples'
    tushare_daily_sync.after_sync(warehouse, D, ok=True, artifacts=target)
    frame = WarehouseQuery(warehouse).metric_checks()
    assert frame.height == 1
    path = Path(frame['sample_path'][0])
    assert path.is_relative_to(target)
    assert pl.read_parquet(path).equals(sample)


@pytest.fixture
def sync_main(monkeypatch, tmp_path):
    """Exercise the real CLI and ServiceRun without data providers or Windows."""
    events = []
    monkeypatch.setattr(tushare_daily_sync, "load_tushare_config",
                        lambda path: SimpleNamespace(token="fixture"))
    monkeypatch.setattr(tushare_daily_sync, "TushareClient", lambda token: object())
    monkeypatch.setattr(tushare_daily_sync, "TushareProvider", lambda client: object())
    monkeypatch.setattr(tushare_daily_sync, "next_open_day", lambda client, day: None)

    def stage(name):
        def execute(*args, run=None, **kwargs):
            events.append(name)
            with record_step(run, name) as step:
                step.rows = 1
            return {name: 1}
        return execute

    for name, function in (("facts", "sync"), ("derived", "refresh_derived"),
                           ("canonical", "publish_canonical_after_sync")):
        monkeypatch.setattr(tushare_daily_sync, function, stage(name))

    artifacts = tmp_path / "quality-samples"

    def verify(warehouse, day, *, artifacts):
        assert day == D and artifacts == tmp_path / "quality-samples"
        events.append("quality")
        return 0

    monkeypatch.setattr(tushare_daily_sync, "run_incremental_verification", verify)
    argv = ["qsys-tushare-sync", "--warehouse", str(tmp_path / "warehouse"),
            "--day", str(D), "--service-runs", str(tmp_path / "runs"),
            "--artifacts", str(artifacts), "--late-minute-convergence"]
    monkeypatch.setattr("sys.argv", argv)

    def run_record():
        files = list((tmp_path / "runs").rglob("*.json"))
        assert len(files) == 1
        return json.loads(files[0].read_text())

    return SimpleNamespace(events=events, argv=argv, record=run_record)


@pytest.mark.parametrize("error", [RuntimeError("QMT unavailable"),
                                   subprocess.TimeoutExpired("fixture-archive", 1)])
@pytest.mark.parametrize("verifier_crashes", [False, True])
def test_supplement_failure_keeps_quality_and_failed_run(
    monkeypatch, sync_main, error, verifier_crashes,
):
    def supplement(warehouse, day, *, run, **kwargs):
        sync_main.events.append("supplement")
        with record_step(run, "晚到上市标的分钟线收敛"):
            raise error

    monkeypatch.setattr(tushare_daily_sync, "converge_late_listing_minutes", supplement)
    if verifier_crashes:
        def crash(*args, **kwargs):
            sync_main.events.append("quality")
            raise ValueError("quality scanner unavailable")
        monkeypatch.setattr(tushare_daily_sync, "run_incremental_verification", crash)

    with pytest.raises(type(error)) as caught:
        tushare_daily_sync.main()
    assert caught.value is error
    assert sync_main.events == ["facts", "derived", "canonical", "supplement", "quality"]
    record = sync_main.record()
    assert record["status"] == "failed" and str(error) in record["error"]
    assert [step["status"] for step in record["steps"]] == [
        "succeeded", "succeeded", "succeeded", "succeeded", "failed",
        "warning" if verifier_crashes else "succeeded",
    ]
    assert record["steps"][-1]["name"] == "增量质检"
    assert record["steps"][-1]["finished_at"] is not None
    if verifier_crashes:
        assert "quality scanner unavailable" in record["steps"][-1]["detail"]


def test_successful_supplement_is_verified_afterwards(monkeypatch, sync_main):
    def supplement(warehouse, day, *, run, **kwargs):
        sync_main.events.append("supplement")
        with record_step(run, "晚到上市标的分钟线收敛") as step:
            step.rows = 1
        return 1

    monkeypatch.setattr(tushare_daily_sync, "converge_late_listing_minutes", supplement)
    assert tushare_daily_sync.main() == 0
    assert sync_main.events == ["facts", "derived", "canonical", "supplement", "quality"]
    assert sync_main.record()["status"] == "succeeded"


@pytest.mark.parametrize("stage", ["sync", "refresh_derived", "publish_canonical_after_sync"])
def test_incomplete_main_sync_does_not_run_success_hook(monkeypatch, sync_main, stage):
    def fail(*args, **kwargs):
        raise RuntimeError("incomplete main sync")

    monkeypatch.setattr(tushare_daily_sync, stage, fail)
    monkeypatch.setattr(tushare_daily_sync, "converge_late_listing_minutes",
                        lambda *args, **kwargs: pytest.fail("must not request Windows"))
    with pytest.raises(RuntimeError, match="incomplete main sync"):
        tushare_daily_sync.main()
    assert "quality" not in sync_main.events
    assert sync_main.record()["status"] == "failed"


def test_operator_interruption_does_not_start_quality_work(monkeypatch, sync_main):
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(tushare_daily_sync, "converge_late_listing_minutes", interrupt)
    with pytest.raises(KeyboardInterrupt):
        tushare_daily_sync.main()
    assert "quality" not in sync_main.events
    assert sync_main.record()["status"] == "failed"
