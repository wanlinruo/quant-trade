"""连续失败要报一次警，不是每秒刷一条日志。

2026-09-18：Windows 执行代理 05:06 崩溃后，影子核心把同一条「券商账户快照已过期」
刷了 5.5 小时、约 2 万次，却没有任何告警，直到人工发现。
"""

from datetime import datetime, timedelta

from qsys.live.tick_guard import TickFailureGuard

T0 = datetime(2026, 9, 18, 5, 7, 0)


class _Bus:
    def __init__(self):
        self.alerts = []

    def critical(self, title, detail=""):
        self.alerts.append(("critical", title, detail))

    def info(self, title, detail=""):
        self.alerts.append(("info", title, detail))


def _guard(bus):
    return TickFailureGuard(bus, log_interval=timedelta(minutes=1),
                            alert_after=timedelta(minutes=5))


def test_repeated_failures_log_once_then_throttle():
    guard = _guard(_Bus())
    assert guard.failed("快照已过期", now=T0) is True                      # 第一次必报
    assert guard.failed("快照已过期", now=T0 + timedelta(seconds=30)) is False
    assert guard.failed("快照已过期", now=T0 + timedelta(seconds=61)) is True
    # 换了一种错误立刻报，不被上一条的节流挡住
    assert guard.failed("行情断流", now=T0 + timedelta(seconds=62)) is True


def test_sustained_failure_alerts_once_and_recovery_is_announced():
    bus = _Bus()
    guard = _guard(bus)
    guard.failed("快照已过期", now=T0)
    guard.failed("快照已过期", now=T0 + timedelta(minutes=4))
    assert bus.alerts == []                                   # 还没到持续阈值
    guard.failed("快照已过期", now=T0 + timedelta(minutes=5, seconds=1))
    guard.failed("快照已过期", now=T0 + timedelta(minutes=30))
    assert [a[0] for a in bus.alerts] == ["critical"]         # 只报一次
    assert "5 分钟" in bus.alerts[0][1] and "快照已过期" in bus.alerts[0][2]

    guard.succeeded(now=T0 + timedelta(minutes=40))
    assert [a[0] for a in bus.alerts] == ["critical", "info"]
    assert "恢复" in bus.alerts[1][1]
    # 恢复后重新开始计数，不会立刻再报 critical
    guard.failed("快照已过期", now=T0 + timedelta(minutes=41))
    assert len(bus.alerts) == 2


def test_success_without_prior_failure_is_silent():
    bus = _Bus()
    guard = _guard(bus)
    guard.succeeded(now=T0)
    assert bus.alerts == []
