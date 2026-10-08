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


def test_non_affecting_failure_stays_visible_without_failing_aggregate(tmp_path):
    with ServiceRun.open(tmp_path, "service-checks") as run:
        with run.step("shadow-core｜当日策略事件已执行", affects_run=False) as step:
            step.fail("事件已过期")
        with run.step("tushare-sync｜日线到货"):
            pass
    data = _only_run(tmp_path)
    assert data["status"] == "succeeded"
    assert data["steps"][0]["status"] == "failed"
    assert data["steps"][0]["affects_run"] is False


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


def test_running_step_progress_is_persisted_before_the_step_finishes(tmp_path):
    run = ServiceRun(tmp_path, "daily-archive")
    with run.step("分钟线（全市场）") as step:
        run.update_step(
            step,
            rows=1200,
            detail="已完成 25/100 组，最近推进 16:01:02，预计剩余 3 分钟",
            progress={
                "total_stock_days": 100,
                "completed_stock_days": 25,
                "last_progress_at": "2026-10-08T16:01:02",
                "estimated_remaining_seconds": 180,
            },
        )
        data = _only_run(tmp_path)
        saved = data["steps"][0]
        assert saved["status"] == "running" and saved["finished_at"] is None
        assert saved["rows"] == 1200
        assert saved["progress"]["completed_stock_days"] == 25
    assert _only_run(tmp_path)["steps"][0]["status"] == "succeeded"


def test_cannot_update_a_step_from_another_run(tmp_path):
    run = ServiceRun(tmp_path, "daily-archive")
    foreign = services.Step("别的任务")
    with pytest.raises(ValueError, match="does not belong"):
        run.update_step(foreign, progress={"completed": 1})


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
    """每个定时任务都要能挂回单元、说得出截止时间与依赖的接口。

    产出表（writes）不强制：通知类任务（daily-digest）只读不写，
    它的价值在发出去的那封信，不在落了什么表。
    """
    for spec in SERVICES:
        if spec.kind == "timer":
            assert spec.unit and spec.due and spec.interfaces


def test_catalog_verify_is_registered_between_sync_and_checks():
    """指标字典验证是自己的定时任务：19:40 跑，20:00 的质检读它的结果。"""
    spec = services.SERVICE_BY_ID["catalog-verify"]
    assert spec.kind == "timer" and spec.unit == "qsys-catalog-verify"
    assert "metric_checks" in spec.writes
    order = [s.id for s in SERVICES if s.kind == "timer"]
    assert order.index("tushare-sync") < order.index("catalog-verify") < order.index("service-checks")


def test_premarket_limits_is_due_before_the_earliest_possible_auction():
    """涨跌停价的截止时间必须早于竞价决策，否则「准时」了策略也已经用不上。

    策略拿涨跌停价给保护价封顶，而竞价时刻是参数 auction_at，最早可到 09:25:05。
    原本 due 写 09:30，等于允许这个任务比它的使用方还晚交货
    （2026-09-28 顺着同类问题查出来的：改了 auction_at，下游没跟着改）。
    """
    from datetime import time

    from qsys.strategy.structural.strategy import auction_time

    spec = services.SERVICE_BY_ID["premarket-limits"]
    earliest_auction = time(9, 25, 5)   # auction_time 允许的下限
    auction_time(earliest_auction)      # 还是合法值的话这行不抛

    assert time.fromisoformat(spec.due) <= earliest_auction
