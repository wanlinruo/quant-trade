"""指标字典验证任务：写自己的运行记录；与同步撞车时跳过，不抢内存。"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from qsys.data import catalog_verify_cli as catalog_verify
from qsys.data.catalog.verification import VerificationSummary
from qsys.data.catalog.health import HealthStatus

DAY = date(2026, 9, 17)


class _Calendar:
    def is_trading_day(self, day):
        return True

    def sessions_in_range(self, start, end):
        return [date(2026, 9, d) for d in (16, 17)]


def _argv(monkeypatch, warehouse, runs):
    monkeypatch.setattr(catalog_verify, "load_trading_calendar", lambda config: _Calendar())
    monkeypatch.setattr(catalog_verify, "refresh_session_health",
                        lambda wh, day, config: {"bar_daily": HealthStatus.GREEN})
    monkeypatch.setattr("sys.argv", [
        "catalog_verify.py", "--warehouse", str(warehouse.root),
        "--service-runs", str(runs), "--day", DAY.isoformat(),
    ])


def _record(runs):
    return json.loads(next(runs.rglob("catalog-verify-*.json")).read_text(encoding="utf-8"))


def test_verification_is_recorded_as_its_own_run(monkeypatch, tmp_path, warehouse):
    summary = VerificationSummary(DAY, "r", 364, 0, 7, ("C3.span_gaps",))
    monkeypatch.setattr(catalog_verify, "verify_session", lambda wh, day, **kw: summary)
    runs = tmp_path / "runs"
    _argv(monkeypatch, warehouse, runs)

    assert catalog_verify.main() == 0

    record = _record(runs)
    assert record["service"] == "catalog-verify" and record["session"] == DAY.isoformat()
    assert record["status"] == "warning"
    step = record["steps"][1]
    assert step["name"] == "指标字典验证" and step["rows"] == 364
    assert "2026 年至今全量" in step["detail"] and "警告 7 条" in step["detail"]
    assert "C3.span_gaps" in step["detail"]
    assert step["status"] == "warning"


@pytest.mark.parametrize("blocking,warnings,health,exit_code,run_status,field_status,table_status", [
    (1, 0, HealthStatus.GREEN, 1, "failed", "failed", "succeeded"),
    (1, 1, HealthStatus.YELLOW, 1, "failed", "failed", "warning"),
    (0, 0, HealthStatus.RED, 1, "failed", "succeeded", "failed"),
    (0, 0, HealthStatus.GRAY, 1, "failed", "succeeded", "failed"),
    (0, 1, HealthStatus.GREEN, 0, "warning", "warning", "succeeded"),
    (0, 0, HealthStatus.YELLOW, 0, "warning", "succeeded", "warning"),
    (0, 0, HealthStatus.GREEN, 0, "succeeded", "succeeded", "succeeded"),
])
def test_field_and_table_results_both_reach_real_record_and_exit_code(
        monkeypatch, tmp_path, warehouse, blocking, warnings, health, exit_code,
        run_status, field_status, table_status):
    runs = tmp_path / "runs"
    _argv(monkeypatch, warehouse, runs)
    monkeypatch.setattr(catalog_verify, "verify_session", lambda *args, **kwargs:
                        VerificationSummary(DAY, "r", 7, blocking, warnings, ()))
    refreshed = []

    def refresh(*args):
        refreshed.append(args[1])
        return {"bar_daily": health}

    monkeypatch.setattr(catalog_verify, "refresh_session_health", refresh)
    assert catalog_verify.main() == exit_code
    record = _record(runs)
    assert refreshed == [DAY]  # 字段阻断仍须留下独立的表级验证结果。
    assert record["status"] == run_status
    assert record["steps"][1]["status"] == field_status
    assert record["steps"][2]["status"] == table_status
    assert f"阻断 {blocking} 条，警告 {warnings} 条" in record["steps"][1]["detail"]


def test_verification_is_skipped_while_the_sync_is_still_running(
        monkeypatch, tmp_path, warehouse):
    """两者峰值各约 5 GB，节点只有 7.5 GB：撞上就跳过，由当晚质检核对结果在不在。"""
    runs = tmp_path / "runs"
    # load_runs 按被验证交易日扫描目录；测试不能随实际运行日期跨日后失效。
    folder = runs / f"{DAY:%Y/%m/%d}"
    folder.mkdir(parents=True)
    (folder / "tushare-sync-190000-1.json").write_text(json.dumps({
        "run_id": "tushare-sync-190000-1", "service": "tushare-sync",
        "session": DAY.isoformat(),
        # 刚起步的同步才算「运行中」；超过 max_runtime 的是「已中断」，不该挡住验证
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "status": "running", "steps": [],
    }), encoding="utf-8")
    called = []
    monkeypatch.setattr(catalog_verify, "verify_session", lambda wh, day, **kw: called.append(day))
    _argv(monkeypatch, warehouse, runs)

    assert catalog_verify.main() == 0
    assert called == []
    step = _record(runs)["steps"][1]
    assert step["status"] == "skipped" and "同步仍在运行" in step["detail"]


def test_table_health_is_a_separate_step_and_red_fails_the_task(monkeypatch, tmp_path, warehouse):
    runs = tmp_path / 'runs'
    _argv(monkeypatch, warehouse, runs)
    monkeypatch.setattr(catalog_verify, 'verify_session',
                        lambda wh, day, **kw: VerificationSummary(DAY, 'r', 2, 0, 0, ()))
    monkeypatch.setattr(catalog_verify, 'refresh_session_health',
                        lambda wh, day, config: {'bar_daily': HealthStatus.RED})
    assert catalog_verify.main() == 1
    record = _record(runs)
    assert record['status'] == 'failed'
    assert record['steps'][-1]['name'] == '表级五维健康'
    assert record['steps'][-1]['status'] == 'failed'
    assert record['steps'][-1]['rows'] == 1
    assert '未覆盖表继续缺证据' in record['steps'][-1]['detail']


def test_private_output_still_observes_sync_in_global_records(monkeypatch, tmp_path, warehouse):
    import sys

    global_runs = tmp_path / "global"
    own_runs = global_runs / "catalog-verify"
    folder = global_runs / "tushare-sync" / f"{DAY:%Y/%m/%d}"
    folder.mkdir(parents=True)
    (folder / "tushare-sync-active.json").write_text(json.dumps({
        "run_id": "tushare-sync-active", "service": "tushare-sync",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "session": DAY.isoformat(), "status": "running", "steps": [],
    }))
    _argv(monkeypatch, warehouse, own_runs)
    monkeypatch.setattr(sys, "argv", sys.argv + ["--service-runs-input", str(global_runs)])
    monkeypatch.setattr(catalog_verify, "verify_session",
                        lambda *a, **kw: __import__("pytest").fail("sync guard bypassed"))
    assert catalog_verify.main() == 0
    assert "同步仍在运行" in _record(own_runs)["steps"][1]["detail"]


def test_explicit_samples_do_not_follow_private_record_directory(monkeypatch, tmp_path, warehouse):
    import sys

    own_runs = tmp_path / "service-runs/catalog-verify"
    artifacts = tmp_path / "state/artifacts/metric_checks"
    observed = []
    def verify(wh, day, **kw):
        observed.append(kw["artifacts"])
        return VerificationSummary(DAY, "r", 2, 0, 0, ())
    _argv(monkeypatch, warehouse, own_runs)
    monkeypatch.setattr(sys, "argv", sys.argv + ["--artifacts", str(artifacts)])
    monkeypatch.setattr(catalog_verify, "verify_session", verify)
    assert catalog_verify.main() == 0
    assert observed == [artifacts]
    assert _record(own_runs)["status"] == "succeeded"


def test_table_health_exception_is_recorded_without_refreshing_old_evidence(monkeypatch, tmp_path, warehouse):
    import pytest
    runs = tmp_path / 'runs'
    _argv(monkeypatch, warehouse, runs)
    monkeypatch.setattr(catalog_verify, 'verify_session',
                        lambda wh, day, **kw: VerificationSummary(DAY, 'r', 2, 0, 0, ()))
    def fail(*args):
        raise RuntimeError('calendar unavailable')
    monkeypatch.setattr(catalog_verify, 'refresh_session_health', fail)
    with pytest.raises(RuntimeError, match='calendar unavailable'):
        catalog_verify.main()
    assert _record(runs)['status'] == 'failed'
    assert not (warehouse.root / 'dataset_checks').exists()
