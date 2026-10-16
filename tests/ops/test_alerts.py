from datetime import datetime

import pytest

from qsys.ops.alerts import (
    LogAlertSink,
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


# ---- 投递留账：通知链路自己挂了，得有办法发现（2026-10-01）----

from datetime import date  # noqa: E402

from qsys.ops.alerts import AlertLedger  # noqa: E402

EMPTY_DAY = date(2024, 1, 3)


class Broken(RecordingAlertSink):
    def send(self, alert):
        raise RuntimeError("webhook 40001 invalid key")


def test_a_delivered_alert_is_written_down(tmp_path):
    ledger = AlertLedger(tmp_path)
    AlertBus([RecordingAlertSink()], ledger).warning("心跳超时", "90 秒无心跳")

    rows = ledger.read(date.today())
    assert len(rows) == 1
    assert rows[0]["severity"] == "WARNING"
    assert rows[0]["delivered"] == 1 and rows[0]["failed"] == []


def test_a_failed_sink_is_named_in_the_ledger(tmp_path):
    ledger = AlertLedger(tmp_path)
    AlertBus([RecordingAlertSink(), Broken()], ledger).critical("停机")

    row = ledger.read(date.today())[0]
    assert row["delivered"] == 1
    assert row["failed"] == ["Broken"]


def test_an_unwritable_ledger_never_breaks_the_alert(tmp_path):
    """留账是旁路的旁路，绝不能反过来影响主流程。"""
    sink = RecordingAlertSink()
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    AlertBus([sink], AlertLedger(blocked / "under")).critical("停机")

    assert len(sink.alerts) == 1


def test_a_half_written_line_does_not_destroy_the_day(tmp_path):
    """进程被 kill 会留下半行 JSON，不能让当天的账整份读不出来。"""
    ledger = AlertLedger(tmp_path)
    AlertBus([RecordingAlertSink()], ledger).info("开工")
    (tmp_path / f"{date.today():%Y-%m-%d}.jsonl").open("a").write('{"at": "2024')

    assert len(ledger.read(date.today())) == 1


def test_a_day_with_no_alerts_reads_empty(tmp_path):
    assert AlertLedger(tmp_path).read(EMPTY_DAY) == []


def test_alert_ledger_merges_component_namespace(tmp_path):
    today = date.today()
    shared = AlertLedger(tmp_path)
    component = AlertLedger(tmp_path / "service-checks")
    alert = Alert(Severity.WARNING, "质检告警", "缺表", datetime.now())
    shared.record(alert, 1, [])
    component.record(Alert(Severity.INFO, "质检通过", "", datetime.now()), 1, [])

    rows = shared.read(today)

    assert [row["title"] for row in rows] == ["质检告警", "质检通过"]


# ---- CRITICAL 双发：既是交易事件也是运维事故（2026-10-03）----

from qsys.ops.alerts import MinSeveritySink, strategy_sinks  # noqa: E402


def test_only_critical_crosses_into_the_ops_channel():
    ops = RecordingAlertSink()
    bus = AlertBus([MinSeveritySink(ops)])
    bus.info("竞价决策"); bus.warning("订单被拒"); bus.critical("对账失败")

    assert [a.title for a in ops.alerts] == ["对账失败"]


def test_the_strategy_channel_still_gets_everything():
    sinks = strategy_sinks("https://x/live", "https://x/ops")
    urls = [s._url for s in sinks if hasattr(s, "_url")]

    assert urls == ["https://x/live"]          # 策略群直连
    assert any(isinstance(s, MinSeveritySink) for s in sinks)


def test_one_robot_for_both_is_not_wired_twice():
    """只配了一个机器人时重复装配会让每条严重告警发两遍。"""
    sinks = strategy_sinks("https://x/only", "https://x/only")

    assert sum(1 for s in sinks if hasattr(s, "_url")) == 1
    assert not any(isinstance(s, MinSeveritySink) for s in sinks)


def test_no_webhook_at_all_still_logs():
    sinks = strategy_sinks("", "")

    assert len(sinks) == 1 and isinstance(sinks[0], LogAlertSink)


def test_a_filtered_sink_is_not_counted_as_a_delivery(tmp_path):
    """留账里的「今天推了几条」要是把挡下的也算进去，就不能用来判断链路好坏。"""
    book = AlertLedger(tmp_path)
    bus = AlertBus([RecordingAlertSink(), MinSeveritySink(RecordingAlertSink())], book)
    bus.info("竞价决策")
    bus.critical("对账失败")

    rows = book.read(date.today())
    assert [r["delivered"] for r in rows] == [1, 2]


def test_a_filter_does_not_hide_a_real_delivery_failure(tmp_path):
    book = AlertLedger(tmp_path)
    AlertBus([MinSeveritySink(Broken())], book).critical("对账失败")

    row = book.read(date.today())[0]
    assert row["delivered"] == 0 and row["failed"] == ["MinSeveritySink"]


def test_a_sink_without_accepts_still_works():
    """sink 只要有 send 就该能用，不该因为少一个可选方法把告警链打死。"""
    seen = []

    class Minimal:
        def send(self, alert):
            seen.append(alert.title)

    AlertBus([Minimal()]).info("开工")

    assert seen == ["开工"]


def test_a_sink_whose_accepts_explodes_is_still_sent_to():
    """宁可多发不可不发。"""
    seen = []

    class Moody:
        def accepts(self, alert):
            raise RuntimeError("坏了")

        def send(self, alert):
            seen.append(alert.title)

    AlertBus([Moody()]).critical("对账失败")

    assert seen == ["对账失败"]
