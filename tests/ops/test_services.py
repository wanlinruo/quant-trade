"""运行记录：成功、失败、跳过、中断都要如实落盘，记录本身出错不得影响任务。"""

import json
from datetime import date, datetime, timedelta

import pytest

from qsys.ops import services
from qsys.ops.services import SERVICES, ServiceRun, effective_status, load_runs, record_step


def _only_run(root):
    files = list(root.rglob("*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding="utf-8"))


def test_successful_run_records_every_step(tmp_path):
    with ServiceRun.open(tmp_path, "tushare-sync") as run:
        run.session = date(2026, 9, 17)
        with run.step("日线", interface="Tushare daily", table="tushare_daily") as step:
            step.rows = 5457
    data = _only_run(tmp_path)
    assert data["status"] == "succeeded"
    assert data["session"] == "2026-09-17"
    assert data["steps"] == [{**data["steps"][0], "name": "日线", "status": "succeeded",
                              "rows": 5457, "interface": "Tushare daily",
                              "table": "tushare_daily"}]
    assert data["finished_at"]


def test_exception_marks_step_and_run_failed_and_is_reraised(tmp_path):
    with pytest.raises(RuntimeError, match="0 行"):
        with ServiceRun.open(tmp_path, "tushare-sync") as run:
            with run.step("日线"):
                raise RuntimeError("daily 返回 0 行")
    data = _only_run(tmp_path)
    assert data["status"] == "failed"
    assert "0 行" in data["error"]
    assert data["steps"][0]["status"] == "failed"
    assert "0 行" in data["steps"][0]["error"]


def test_step_can_fail_without_raising(tmp_path):
    with ServiceRun.open(tmp_path, "daily-archive") as run:
        with run.step("竞价逐笔") as step:
            step.fail("导入失败")
        with run.step("分钟线"):
            pass
    data = _only_run(tmp_path)
    assert data["status"] == "failed"
    assert [s["status"] for s in data["steps"]] == ["failed", "succeeded"]


def test_warning_step_makes_run_warning(tmp_path):
    with ServiceRun.open(tmp_path, "tushare-sync") as run:
        with run.step("增量质检") as step:
            step.warn("阻断级规则未通过 2 项")
    assert _only_run(tmp_path)["status"] == "warning"


def test_repaired_step_makes_run_repaired_unless_something_still_fails(tmp_path):
    with ServiceRun.open(tmp_path / "a", "service-checks") as run:
        with run.step("x") as step:
            step.status = services.REPAIRED
        with run.step("y"):
            pass
    assert _only_run(tmp_path / "a")["status"] == services.REPAIRED
    with ServiceRun.open(tmp_path / "b", "service-checks") as run:
        with run.step("x") as step:
            step.status = services.REPAIRED
        with run.step("y") as step:
            step.fail("缺")
    assert _only_run(tmp_path / "b")["status"] == services.FAILED


def test_skipped_run(tmp_path):
    with ServiceRun.open(tmp_path, "premarket-limits") as run:
        run.skip("2026-09-19 不是交易日")
    data = _only_run(tmp_path)
    assert data["status"] == "skipped"
    assert data["note"] == "2026-09-19 不是交易日"


def test_system_exit_is_recorded(tmp_path):
    with pytest.raises(SystemExit):
        with ServiceRun.open(tmp_path, "daily-archive"):
            raise SystemExit(1)
    assert _only_run(tmp_path)["status"] == "failed"


def test_unwritable_root_never_breaks_the_job(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("不是目录")
    with ServiceRun.open(blocker, "tushare-sync") as run:
        with run.step("日线") as step:
            step.rows = 1
    assert run.status == "succeeded"


def test_unknown_service_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        ServiceRun(tmp_path, "nope")


def test_record_step_without_run_is_a_no_op():
    with record_step(None, "日线") as step:
        step.rows = 3
    assert step.rows == 3


def test_add_step_records_a_finished_step(tmp_path):
    with ServiceRun.open(tmp_path, "daily-archive") as run:
        run.add_step("分钟线", status="failed", rows=10, detail="2 个坏文件")
    data = _only_run(tmp_path)
    assert data["status"] == "failed"
    assert data["steps"][0]["rows"] == 10


def test_long_running_record_is_interrupted():
    started = datetime(2026, 9, 17, 17, 0)
    run = {"service": "tushare-sync", "status": "running", "started_at": started.isoformat()}
    assert effective_status(run, now=started + timedelta(minutes=30)) == "running"
    assert effective_status(run, now=started + timedelta(hours=3)) == "interrupted"


def test_load_runs_skips_unreadable_files(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "_now", lambda: datetime(2026, 9, 17, 17, 0))
    with ServiceRun.open(tmp_path, "tushare-sync"):
        pass
    (tmp_path / "2026" / "09" / "17" / "broken.json").write_text("{", encoding="utf-8")
    runs = load_runs(tmp_path, date(2026, 9, 16), date(2026, 9, 17))
    assert [r["service"] for r in runs] == ["tushare-sync"]


def test_every_timer_has_a_unit_and_due_time():
    for spec in SERVICES:
        if spec.kind == "timer":
            assert spec.unit and spec.due and spec.interfaces and spec.writes


def test_catalog_verify_is_registered_between_sync_and_checks():
    """指标字典验证是自己的定时任务：19:40 跑，20:00 的质检读它的结果。"""
    spec = services.SERVICE_BY_ID["catalog-verify"]
    assert spec.kind == "timer" and spec.unit == "qsys-catalog-verify"
    assert "metric_checks" in spec.writes
    order = [s.id for s in SERVICES if s.kind == "timer"]
    assert order.index("tushare-sync") < order.index("catalog-verify") < order.index("service-checks")
