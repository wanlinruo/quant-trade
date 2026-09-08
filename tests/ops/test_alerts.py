from datetime import datetime

import pytest

from qsys.ops.alerts import (
    Alert,
    AlertBus,
    RecordingAlertSink,
    Severity,
    WebhookAlertSink,
)

T = datetime(2024, 1, 3, 10, 0)


def test_alert_text_contains_severity_and_title():
    text = Alert(Severity.CRITICAL, "持仓不一致", "600036.SH local=500 remote=400", T).as_text()
    assert "CRITICAL" in text
    assert "持仓不一致" in text
    assert "600036.SH" in text


def test_alert_text_without_detail():
    text = Alert(Severity.INFO, "启动完成", "", T).as_text()
    assert "启动完成" in text


def test_recording_sink_captures_alerts():
    sink = RecordingAlertSink()
    AlertBus([sink]).warning("心跳超时", "agent 90 秒无心跳")
    assert len(sink.alerts) == 1
    assert sink.alerts[0].severity is Severity.WARNING


def test_bus_fans_out_to_every_sink():
    a, b = RecordingAlertSink(), RecordingAlertSink()
    AlertBus([a, b]).critical("停机")
    assert len(a.alerts) == 1 and len(b.alerts) == 1


def test_severity_helpers():
    sink = RecordingAlertSink()
    bus = AlertBus([sink])
    bus.info("i")
    bus.warning("w")
    bus.critical("c")
    assert [a.severity for a in sink.alerts] == [
        Severity.INFO, Severity.WARNING, Severity.CRITICAL,
    ]


def test_a_failing_sink_does_not_block_the_others():
    """告警发不出去也不能让主流程崩——告警是旁路，不是主路。"""

    class Broken(RecordingAlertSink):
        def send(self, alert):
            raise RuntimeError("webhook down")

    good = RecordingAlertSink()
    AlertBus([Broken(), good]).critical("重要告警")
    assert len(good.alerts) == 1


def test_bus_with_no_sinks_does_not_raise():
    AlertBus([]).critical("nobody listening")


def test_webhook_posts_the_alert_text():
    posted = []

    def poster(url, payload, timeout):
        posted.append((url, payload, timeout))

    WebhookAlertSink("https://example.invalid/hook", poster=poster).send(
        Alert(Severity.CRITICAL, "熔断", "日内亏损 5%", T)
    )
    assert posted[0][0] == "https://example.invalid/hook"
    assert "熔断" in str(posted[0][1])
    assert posted[0][2] == 5.0


def test_webhook_timeout_is_configurable():
    posted = []
    WebhookAlertSink("https://x.invalid", timeout=1.5,
                     poster=lambda u, p, t: posted.append(t)).send(
        Alert(Severity.INFO, "x", "", T)
    )
    assert posted[0] == 1.5


def test_alert_is_frozen():
    with pytest.raises(Exception):
        Alert(Severity.INFO, "x", "", T).title = "y"
