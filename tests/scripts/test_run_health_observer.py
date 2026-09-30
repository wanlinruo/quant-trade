from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from qsys.ops.alerts import AlertBus, RecordingAlertSink, Severity
from qsys.ops.health import DEGRADED, DOWN, HEALTHY, MORNING
from qsys.ops.trading_chain import ChainAssessment
from scripts.run_health_observer import BusinessAlertTracker, observation


def row(service="shadow-core", state="ok"):
    return {
        "spec": SimpleNamespace(
            id=service, host="Linux", windows_task=None,
        ),
        "state": state,
        "heartbeat_assessment": None,
    }


def test_business_failure_overrides_a_healthy_process():
    item = observation(
        row(), datetime(2026, 9, 21, 9, 30, 15), MORNING,
        ChainAssessment(DOWN, "strategy_event_failed", "竞价行情超时", 2, 1),
    )
    assert item.state == DOWN
    assert item.process_state == HEALTHY
    assert item.business_state == DOWN
    assert item.reason_code == "strategy_event_failed"
    assert item.details["trading_chain"]["completed"] == 1


def test_business_alerts_fire_once_and_report_recovery():
    sink = RecordingAlertSink()
    tracker = BusinessAlertTracker(AlertBus([sink]))
    now = datetime(2026, 9, 21, 9, 30, 15)
    delayed = observation(
        row(), now, MORNING,
        ChainAssessment(DEGRADED, "strategy_event_delayed", "竞价未落库"),
    )
    failed = observation(
        row(), now, MORNING,
        ChainAssessment(DOWN, "strategy_event_failed", "竞价行情超时"),
    )
    recovered = observation(
        row(), now, MORNING,
        ChainAssessment(HEALTHY, "strategy_events_current", "2/2 完成"),
    )
    tracker.update(delayed)
    tracker.update(delayed)
    tracker.update(failed)
    tracker.update(failed)
    tracker.update(recovered)
    assert [alert.severity for alert in sink.alerts] == [
        Severity.WARNING, Severity.CRITICAL, Severity.INFO,
    ]


def test_a_task_caught_mid_run_is_not_a_failure():
    """0x41301 是"正在运行、尚无结果"；看门狗每 5 分钟跑一次，探测可能正好撞上。"""
    from scripts.run_health_observer import _watchdog_state

    now = datetime(2026, 9, 26, 23, 30, tzinfo=timezone.utc)
    running = {"State": "Running", "LastTaskResult": 267009,
               "LastRunTime": (now - timedelta(seconds=30)).replace(tzinfo=None).isoformat()}
    state, reason = _watchdog_state(running, now.replace(tzinfo=None))
    assert reason == "watchdog_fresh"

    failed = {**running, "State": "Ready", "LastTaskResult": 267009}
    assert _watchdog_state(failed, now.replace(tzinfo=None))[1] == "watchdog_failed"


# ---- 告警要进运维群（2026-10-01 发现一直发在策略群）----

def _deploy(ops="", strategy="https://x/live"):
    from qsys.live.config import AlertConfig
    return SimpleNamespace(alerts=AlertConfig(
        webhook_url=strategy, ops_webhook_url=ops, timeout_seconds=5.0))


def _urls(sinks):
    return [s._url for s in sinks if hasattr(s, "_url")]


def test_chain_alerts_go_to_the_ops_robot_not_the_strategy_one():
    """链路断了是运维事故，不是策略行情——发进实盘群会把实盘群淹掉。"""
    from scripts.run_health_observer import ops_sinks

    assert _urls(ops_sinks(_deploy(ops="https://x/ops"))) == ["https://x/ops"]


def test_a_single_robot_still_gets_everything():
    """只配了一个机器人时不能静默——ops_url 会回落到它。"""
    from scripts.run_health_observer import ops_sinks

    assert _urls(ops_sinks(_deploy(ops="", strategy="https://x/only"))) == ["https://x/only"]


def test_no_robot_configured_still_logs():
    from scripts.run_health_observer import ops_sinks

    sinks = ops_sinks(_deploy(ops="", strategy=""))
    assert _urls(sinks) == [] and len(sinks) == 1
