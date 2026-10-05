"""看门狗的新判据：心跳是否新鲜，而不是进程是否存在。

2026-10-05：代理卡在 xtquant 的永久阻塞里，进程活着 11 小时 45 分，
按进程存在判活的看门狗全程报告正常并什么都没做。
"""

from datetime import datetime, timedelta

from qsys.live.heartbeat import HeartbeatMonitor
from qsys.live.messages import Heartbeat
from qsys.live.transport import MemoryTransport

from scripts.check_agent_alive import ALIVE, MISSING, STALE, decide

PREFIX = "state:heartbeat"


def _monitor(beat_at: datetime | None):
    transport = MemoryTransport()
    monitor = HeartbeatMonitor(transport, prefix=PREFIX)
    if beat_at is not None:
        transport.set_key(
            f"{PREFIX}:agent",
            Heartbeat(source="agent", at=beat_at).model_dump_json(),
        )
    return monitor


def test_a_fresh_heartbeat_is_alive():
    now = datetime(2026, 10, 5, 20, 34, 54)
    code, detail = decide(_monitor(now - timedelta(seconds=3)), "agent", 120.0, now)
    assert code == ALIVE
    assert "3" in detail


def test_the_actual_incident_is_detected():
    """实测数据：心跳冻结在 08:45:04，发现时是 20:25。"""
    now = datetime(2026, 10, 5, 20, 25, 44)
    code, detail = decide(
        _monitor(datetime(2026, 10, 5, 8, 45, 4)), "agent", 120.0, now
    )
    assert code == STALE
    assert "42040" in detail.replace(",", "")


def test_a_missing_key_is_not_reported_as_alive():
    """键不存在就是不知道它活着——绝不当成正常。"""
    now = datetime(2026, 10, 5, 20, 34, 54)
    code, _ = decide(_monitor(None), "agent", 120.0, now)
    assert code == MISSING


def test_the_boundary_is_inclusive():
    now = datetime(2026, 10, 5, 20, 0, 0)
    at_limit = decide(_monitor(now - timedelta(seconds=120)), "agent", 120.0, now)
    just_over = decide(_monitor(now - timedelta(seconds=121)), "agent", 120.0, now)
    assert at_limit[0] == ALIVE
    assert just_over[0] == STALE


def test_exit_codes_are_distinct_and_nonzero_for_trouble():
    """看门狗只看退出码，所以三个状态不能混。"""
    assert ALIVE == 0
    assert STALE != 0 and MISSING != 0 and STALE != MISSING


def test_a_future_dated_beat_is_not_silently_alive():
    """时钟倒退时不许把一个未来的时间戳当成新鲜。"""
    now = datetime(2026, 10, 5, 20, 0, 0)
    code, _ = decide(_monitor(now + timedelta(hours=1)), "agent", 120.0, now)
    assert code != ALIVE
