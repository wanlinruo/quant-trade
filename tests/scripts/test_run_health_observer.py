from datetime import datetime
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
