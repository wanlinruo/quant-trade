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

    def fake_verify(actual_warehouse, day, *, step=None):
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
    def boom(actual_warehouse, day, *, step=None):
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
        lambda provider, warehouse, day, run=None, block_trade=False: events.append("facts") or {"facts": 1},
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


def test_actual_verification_records_one_rule_without_multiplying_its_cases(
    monkeypatch, warehouse, tmp_path,
):
    import polars as pl
    from qsys.data import schema
    from qsys.data.catalog import checks as C
    from qsys.data.query import WarehouseQuery
    from qsys.ops.services import ServiceRun

    metrics = tuple(f"bar_minute_1m.{name}" for name in schema.BAR_MINUTE_1M.names)
    check = C.Check(
        "X.leading_zero", C.Category.COMPLETENESS, C.Scope.INCREMENTAL,
        metrics, (), "固定规则结果",
        lambda _: C.CheckResult(12, 5570, "成因待核验"),
    )
    monkeypatch.setattr(C, "CHECKS", {check.id: check})
    with ServiceRun.open(tmp_path / "runs", "tushare-sync") as run:
        tushare_daily_sync.after_sync(warehouse, D, ok=True, run=run)
    saved = json.loads(run.path.read_text())
    step = saved["steps"][0]
    assert saved["status"] == step["status"] == "warning"
    assert "字段结果未通过 10 条，涉及 1 条规则" in step["detail"]
    assert "违规数 12 / 检查数 5570" in step["detail"]
    assert "独立问题总数" in step["detail"]
    progress = step["progress"]
    assert progress["blocking_field_results"] == 10
    assert progress["blocking_rules"] == [{
        "check_id": "X.leading_zero", "field_results": 10,
        "counts": [{"violations": 12, "checked_rows": 5570}], "sample_paths": [],
    }]
    frame = WarehouseQuery(warehouse).metric_checks()
    assert frame.height == 10
    assert not frame["passed"].any()
    assert set(frame["severity"]) == {"blocking"}
    assert set(frame["violations"]) == {12}
    assert set(frame["run_id"]) == {progress["verification_run_id"]}


def test_rule_summary_keeps_different_responsibility_counts_and_excludes_passed_rows():
    import polars as pl
    frame = pl.DataFrame({
        "check_id": ["X.a", "X.a", "X.a", "X.b", "X.c"],
        "passed": [False, False, False, False, True],
        "severity": ["blocking", "blocking", "blocking", "warning", "warning"],
        "violations": [12, 12, None, 7, 0],
        "checked_rows": [5570, 5570, None, 9, 4],
        "sample_path": ["samples/a.parquet", "samples/a.parquet", None, None, None],
    })
    result = tushare_daily_sync.incremental_verification_summary(frame)
    assert result["field_results"] == 5
    assert result["blocking_field_results"] == 3
    assert result["blocking_rule_count"] == 1
    assert result["blocking_rules"][0]["counts"] == [
        {"violations": None, "checked_rows": None},
        {"violations": 12, "checked_rows": 5570},
    ]
    assert result["blocking_rules"][0]["sample_paths"] == ["samples/a.parquet"]


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

    def verify(warehouse, day, *, artifacts, step=None):
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


def test_deferred_cash_events_warn_without_failing_sync(monkeypatch, sync_main, tmp_path):
    """#636：上游现金缺陷只记警告，派生与发布照常运行，任务不判失败。"""
    from qsys.data.pipeline import dividend_sync
    from qsys.data import windows_data_transport
    monkeypatch.setattr(windows_data_transport.WindowsDataTransport, "__init__", lambda *a, **kw: None)
    monkeypatch.setattr(windows_data_transport.WindowsDataTransport, "close", lambda *_: None)
    sync_main.argv.extend([
        "--dividend-sync", "--dividend-initial-start", str(D),
        "--dividend-win-script-source", str(tmp_path / "approved.py"),
        "--late-minute-win-transport", "gateway",
        "--late-minute-win-known-hosts", str(tmp_path / "known_hosts"),
    ])
    def snapshot(*args, warn, **kwargs):
        sync_main.events.append("dividends")
        warn("Tushare 现金暂缓 1 条：603596.SH 2026-11-10 cash_value_missing")
        return 0
    monkeypatch.setattr(dividend_sync, "sync_dividends", snapshot)
    monkeypatch.setattr(tushare_daily_sync, "converge_late_listing_minutes", lambda *a, **kw: 0)
    assert tushare_daily_sync.main() == 0
    assert sync_main.events == ["facts", "dividends", "derived", "canonical", "quality"]
    record = sync_main.record()
    step = next(s for s in record["steps"] if s["table"] == "corporate_actions")
    assert step["status"] == "warning" and "603596.SH" in step["detail"]
    assert record["status"] != "failed"


@pytest.mark.parametrize("fails", [False, True])
def test_dividend_snapshot_precedes_factors_and_failure_keeps_quality(
    monkeypatch, sync_main, tmp_path, fails,
):
    from qsys.data.pipeline import dividend_sync
    from qsys.data import windows_data_transport
    monkeypatch.setattr(windows_data_transport.WindowsDataTransport, "__init__", lambda *a, **kw: None)
    monkeypatch.setattr(windows_data_transport.WindowsDataTransport, "close", lambda *_: None)
    sync_main.argv.extend([
        "--dividend-sync", "--dividend-initial-start", str(D),
        "--dividend-win-script-source", str(tmp_path / "approved.py"),
        "--late-minute-win-transport", "gateway",
        "--late-minute-win-known-hosts", str(tmp_path / "known_hosts"),
    ])
    def snapshot(*args, **kwargs):
        sync_main.events.append("dividends")
        if fails:
            raise RuntimeError("incomplete event input")
        return 0
    monkeypatch.setattr(dividend_sync, "sync_dividends", snapshot)
    monkeypatch.setattr(tushare_daily_sync, "converge_late_listing_minutes", lambda *a, **kw: 0)
    if fails:
        with pytest.raises(RuntimeError, match="incomplete event"):
            tushare_daily_sync.main()
        assert sync_main.events == ["facts", "dividends", "quality"]
        assert sync_main.record()["status"] == "failed"
    else:
        assert tushare_daily_sync.main() == 0
        assert sync_main.events == ["facts", "dividends", "derived", "canonical", "quality"]


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
