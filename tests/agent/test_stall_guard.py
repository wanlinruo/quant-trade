"""主循环停滞检测。

2026-10-05 的实际故障：miniQMT 08:45:04 自行重启，代理的 xtquant 同步调用
永久阻塞（xtquant 内部 `Future.result()` 不带 timeout），卡住的线程杀不掉，
循环里的 `except Exception` 永远不触发，心跳冻结在那一秒，而进程活着。
按「进程是否存在」判活的看门狗对此结构性失明，11 小时 45 分无人发现。

所以让进程自己死——死掉之后现有看门狗就能把它拉回来。
"""

import threading

import pytest

from qsys.agent.stall_guard import StallGuard


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_a_fresh_guard_is_not_stalled():
    clock = FakeClock()
    guard = StallGuard(timeout_s=60.0, on_stall=lambda reason: None, clock=clock)
    assert guard.check() is False


def test_no_progress_past_the_timeout_is_a_stall():
    clock, fired = FakeClock(), []
    guard = StallGuard(timeout_s=60.0, on_stall=fired.append, clock=clock)
    clock.advance(59.9)
    assert guard.check() is False
    assert fired == []
    clock.advance(0.2)
    assert guard.check() is True
    assert len(fired) == 1
    assert "60" in fired[0]


def test_progress_resets_the_clock():
    clock, fired = FakeClock(), []
    guard = StallGuard(timeout_s=60.0, on_stall=fired.append, clock=clock)
    for _ in range(10):
        clock.advance(59.0)
        guard.progress()
        assert guard.check() is False
    assert fired == []


def test_on_stall_fires_only_once():
    """反复触发会把日志刷满，也会对同一次故障反复调用 os._exit。"""
    clock, fired = FakeClock(), []
    guard = StallGuard(timeout_s=10.0, on_stall=fired.append, clock=clock)
    clock.advance(100.0)
    for _ in range(5):
        guard.check()
    assert len(fired) == 1


def test_progress_after_a_stall_does_not_re_arm():
    """已经判定停滞并打算退出，之后即使阻塞调用返回了也不该假装没事。"""
    clock, fired = FakeClock(), []
    guard = StallGuard(timeout_s=10.0, on_stall=fired.append, clock=clock)
    clock.advance(20.0)
    assert guard.check() is True
    guard.progress()
    clock.advance(20.0)
    guard.check()
    assert len(fired) == 1


def test_the_watcher_thread_really_fires():
    """真线程下的集成验证：不靠注入时钟也必须触发。"""
    fired = threading.Event()
    guard = StallGuard(
        timeout_s=0.05, on_stall=lambda reason: fired.set(), interval_s=0.01
    )
    guard.start()
    try:
        assert fired.wait(timeout=5.0), "看守线程没有在超时后触发"
    finally:
        guard.stop()


def test_stop_ends_the_watcher_thread():
    guard = StallGuard(timeout_s=5.0, on_stall=lambda reason: None, interval_s=0.01)
    guard.start()
    guard.stop()
    assert guard.thread is not None
    guard.thread.join(timeout=5.0)
    assert not guard.thread.is_alive()


def test_timeout_must_be_positive():
    with pytest.raises(ValueError):
        StallGuard(timeout_s=0.0, on_stall=lambda reason: None)
