"""告警总线（#662 计划 03 起发布结构化事件）：旁路、失败隔离、留存与通道路由。"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from qsys.ops.alert_events import SHANGHAI, AlertEventLog, Severity, render_text
from qsys.ops.alerts import (
    AlertBus, LegacyLedger, LogAlertSink, MinSeveritySink, RecordingAlertSink, WebhookAlertSink, ledger,
    strategy_sinks,
)

T = datetime(2026, 11, 18, 9, 30, 1, tzinfo=SHANGHAI)
DAY = date(2026, 11, 18)


def bus(*sinks, log=None, source="live-core"):
    return AlertBus(list(sinks), log, source=source, clock=lambda: T)


class Broken(RecordingAlertSink):
    def send(self, event):
        raise RuntimeError("sink down")


def test_emit_builds_event_from_registry():
    sink = RecordingAlertSink()
    e = bus(sink).emit("live.reconcile.failed", detail="差 1 股", evidence={"diff": 1})
    assert [x.event_id for x in sink.events] == [e.event_id]  # sink 收到的是投递前的事件
    assert (e.key, e.source, e.severity, e.state, e.title) == (
        "live.reconcile.failed", "live-core", Severity.CRITICAL, "firing", "实盘账户对账失败")
    assert e.occurred_at == T and e.evidence == {"diff": 1} and len(e.event_id) == 32
    assert render_text(e) == "[CRITICAL] 2026-11-18 09:30:01 实盘账户对账失败\n差 1 股"


def test_emit_template_params_and_explicit_state():
    sink = RecordingAlertSink()
    b = bus(sink, source="reboot-drill")
    assert b.emit("drill.reboot.passed", params={"day": "2026-11-18"}).title == "月度重启演练通过（2026-11-18）"
    r = b.emit("observer.process.down", subject="paper", params={"title": "模拟交易已恢复"},
               severity=Severity.INFO, state="resolved")
    assert (r.state, r.severity, r.subject, r.title) == ("resolved", Severity.INFO, "paper", "模拟交易已恢复")
    notice = b.emit("strategy.run.report", subject="paper", params={"title": "竞价"})
    assert notice.state == "notice"


def test_ambiguous_severity_must_be_explicit():
    sink = RecordingAlertSink()
    e = bus(sink).emit("checks.service.problems", params={"title": "x"})  # 允许 W/C，未指定
    assert e.key == "unregistered:checks.service.problems" and e.severity is Severity.CRITICAL
    assert [x.event_id for x in sink.events] == [e.event_id]


def test_unregistered_key_still_delivered_as_critical(tmp_path):
    sink, log = RecordingAlertSink(), AlertEventLog(tmp_path)
    e = bus(sink, log=log).emit("nope.never.used", detail="原详情", params={"title": "原标题"})
    assert e.key == "unregistered:nope.never.used" and e.severity is Severity.CRITICAL and e.state == "firing"
    assert e.title == "原标题" and e.detail == "原详情"
    assert [x.event_id for x in sink.events] == [e.event_id] and log.read(DAY) == [e] and e.delivered == 1


def test_disallowed_severity_falls_back_to_unregistered_critical():
    sink = RecordingAlertSink()
    e = bus(sink).emit("live.reconcile.failed", severity=Severity.INFO)
    assert e.key == "unregistered:live.reconcile.failed" and e.severity is Severity.CRITICAL
    wrong_state = bus(sink).emit("strategy.run.report", params={"title": "x"}, state="firing")
    assert wrong_state.key == "unregistered:strategy.run.report"


def test_emit_isolates_sink_and_store_failures(tmp_path):
    good = RecordingAlertSink()
    blocker = tmp_path / "file"
    blocker.write_text("x")
    e = bus(Broken(), good, log=AlertEventLog(blocker / "sub")).emit("live.heartbeat.timeout", subject="agent")
    assert good.events and e.delivered == 1 and e.failed == ("Broken",)


def test_store_records_delivery_outcome(tmp_path):
    log = AlertEventLog(tmp_path)
    bus(RecordingAlertSink(), Broken(), log=log).emit("live.reject.fuse", detail="3 笔")
    [stored] = log.read(DAY)
    assert stored.delivered == 1 and stored.failed == ("Broken",) and stored.key == "live.reject.fuse"


def test_bus_with_no_sinks_does_not_raise(tmp_path):
    e = bus().emit("live.core.fuse")
    assert e.delivered == 0 and e.failed == ()


def test_webhook_posts_the_event_text():
    calls = []
    sink = WebhookAlertSink("https://hook", timeout=2.5, poster=lambda u, p, t: calls.append((u, p, t)))
    e = bus(sink).emit("live.reject.fuse", detail="日内亏损 5%")
    assert calls == [("https://hook", {"msgtype": "text", "text": {"content": render_text(e)}}, 2.5)]


def test_only_critical_crosses_into_the_ops_channel():
    ops = RecordingAlertSink()
    b = bus(MinSeveritySink(ops))
    b.emit("live.order.rejected", detail="x")
    b.emit("live.reject.fuse")
    assert [e.key for e in ops.events] == ["live.reject.fuse"]


def test_a_filtered_sink_is_not_counted_as_a_delivery(tmp_path):
    e = bus(RecordingAlertSink(), MinSeveritySink(RecordingAlertSink())).emit("live.order.rejected")
    assert e.delivered == 1 and e.failed == ()


def test_a_filter_does_not_hide_a_real_delivery_failure():
    e = bus(MinSeveritySink(Broken())).emit("live.reject.fuse")
    assert e.delivered == 0 and e.failed == ("MinSeveritySink",)


def test_a_sink_without_accepts_still_works():
    class Plain:
        def __init__(self):
            self.got = []

        def send(self, event):
            self.got.append(event)
    p = Plain()
    bus(p).emit("live.core.fuse")
    assert len(p.got) == 1


def test_a_sink_whose_accepts_explodes_is_still_sent_to():
    class Weird(RecordingAlertSink):
        def accepts(self, event):
            raise RuntimeError("bad filter")
    w = Weird()
    bus(w).emit("live.core.fuse")
    assert len(w.events) == 1


def test_strategy_sinks_unchanged():
    sinks = strategy_sinks("https://s", "https://o")
    assert [type(s).__name__ for s in sinks] == ["LogAlertSink", "WebhookAlertSink", "MinSeveritySink"]
    assert [type(s).__name__ for s in strategy_sinks("https://s", "https://s")] == ["LogAlertSink", "WebhookAlertSink"]
    assert [type(s).__name__ for s in strategy_sinks("")] == ["LogAlertSink"]


def test_log_sink_never_raises():
    LogAlertSink().send(bus().emit("live.core.fuse"))


def test_ledger_factory_returns_event_log(tmp_path):
    assert isinstance(ledger(tmp_path), AlertEventLog) and ledger(tmp_path).root == tmp_path


def test_legacy_ledger_reads_old_lines_and_namespaces(tmp_path):
    line = {"at": "2026-11-18T09:30:00", "severity": "WARNING", "title": "旧", "delivered": 1, "failed": []}
    (tmp_path / "2026-11-18.jsonl").write_text(json.dumps(line, ensure_ascii=False) + "\n{半行", encoding="utf-8")
    (tmp_path / "paper").mkdir()
    (tmp_path / "paper" / "2026-11-18.jsonl").write_text(json.dumps({**line, "title": "模拟"}) + "\n", encoding="utf-8")
    assert [r["title"] for r in LegacyLedger(tmp_path).read(DAY)] == ["旧", "模拟"]
    assert LegacyLedger(tmp_path / "none").read(DAY) == []


def test_old_text_api_is_gone():
    b = bus()
    for name in ("info", "warning", "critical", "publish"):
        assert not hasattr(b, name)
    with pytest.raises(ImportError):
        from qsys.ops.alerts import Alert  # noqa: F401
