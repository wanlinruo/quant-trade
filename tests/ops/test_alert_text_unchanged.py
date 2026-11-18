"""通知文本回归（#662 计划 03）：改为结构化事件后，sink 收到的文本与改造前逐字相同。"""

from __future__ import annotations

from datetime import datetime

from qsys.ops.alert_events import SHANGHAI, render_text
from qsys.ops.alerts import AlertBus, RecordingAlertSink

T = datetime(2026, 11, 18, 9, 30, 1, tzinfo=SHANGHAI)

# 改造前各生产者写死的标题（runtime / core_cli 原文）
LEGACY_TITLES = {
    "live.reconcile.drift": "影子盘与券商账户不一致（不停机）",
    "live.reconcile.failed": "实盘账户对账失败",
    "live.heartbeat.timeout": "实盘心跳超时",
    "live.order.rejected": "实盘订单被拒",
    "live.reject.fuse": "实盘连续拒单熔断",
    "live.core.fuse": "实盘核心连续故障熔断",
    "live.fuse.cancel_resent": "熔断撤单已补发",
}
LEGACY_LEVEL = {"live.reconcile.drift": "WARNING", "live.order.rejected": "WARNING"}


def test_trading_side_text_unchanged():
    sink = RecordingAlertSink()
    bus = AlertBus([sink], source="live-core", clock=lambda: T)
    for key, title in LEGACY_TITLES.items():
        e = bus.emit(key, detail="详情")
        level = LEGACY_LEVEL.get(key, "CRITICAL")
        assert render_text(e) == f"[{level}] 2026-11-18 09:30:01 {title}\n详情", key


def test_strategy_notice_text_unchanged():
    sink = RecordingAlertSink()
    bus = AlertBus([sink], source="paper", clock=lambda: T)
    e = bus.emit("strategy.run.report", subject="模拟｜A", params={"title": "模拟｜A 竞价决策"}, detail="d")
    assert render_text(e) == "[INFO] 2026-11-18 09:30:01 模拟｜A 竞价决策\nd"
