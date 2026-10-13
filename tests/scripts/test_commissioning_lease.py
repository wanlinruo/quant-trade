"""The heartbeat exemption may only be removed after a safe handoff."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from qsys.live.config import StreamConfig
from qsys.live.messages import AccountSnapshot, Heartbeat, MarketSchedule
from qsys.live.transport import MemoryTransport
from scripts.commissioning_lease import _arm_refusal, _disarm_refusal

NOW = datetime(2026, 10, 12, 10)


@dataclass
class FakeConfig:
    streams: StreamConfig = field(default_factory=StreamConfig)


def _arm_transport(*, core_age=1.0, activity="idle", schedule_age=1.0):
    transport = MemoryTransport()
    streams = StreamConfig()
    transport.set_key(streams.market_schedule, MarketSchedule(
        checked_at=NOW - timedelta(seconds=schedule_age),
        is_trading_day=True, is_open=True,
    ).model_dump_json())
    transport.set_key(streams.account, AccountSnapshot(
        at=NOW - timedelta(seconds=1), cash=100_000,
        positions={}, active_order_count=0,
    ).model_dump_json())
    transport.set_key(f"{streams.heartbeat}:agent", Heartbeat(
        source="agent", at=NOW - timedelta(seconds=1),
        account_protected=True, local_kill_engaged=False,
    ).model_dump_json())
    if core_age is not None:
        transport.set_key(f"{streams.heartbeat}:core", Heartbeat(
            source="core", at=NOW - timedelta(seconds=core_age),
            activity=activity,
        ).model_dump_json())
    return transport


def test_arm_requires_a_healthy_idle_core_before_handoff():
    assert _arm_refusal(FakeConfig(), _arm_transport(), NOW) is None
    assert "心跳" in _arm_refusal(
        FakeConfig(), _arm_transport(core_age=11), NOW,
    )
    assert "空闲" in _arm_refusal(
        FakeConfig(), _arm_transport(activity="event"), NOW,
    )


def test_arm_schedule_age_matches_the_30_second_publisher():
    assert _arm_refusal(
        FakeConfig(), _arm_transport(schedule_age=44), NOW,
    ) is None
    assert "45 秒" in _arm_refusal(
        FakeConfig(), _arm_transport(schedule_age=46), NOW,
    )


def _transport(*, agent_mode="live", core_age=None, timed_out=False):
    transport = MemoryTransport()
    streams = StreamConfig()
    transport.set_key(
        f"{streams.heartbeat}:agent",
        Heartbeat(
            source="agent", at=NOW - timedelta(seconds=1),
            trading_mode=agent_mode,
        ).model_dump_json(),
    )
    if core_age is not None:
        transport.set_key(
            f"{streams.heartbeat}:core",
            Heartbeat(
                source="core", at=NOW - timedelta(seconds=core_age),
                event_timed_out=timed_out,
            ).model_dump_json(),
        )
    return transport


def test_disarm_is_safe_after_agent_returns_to_shadow():
    assert _disarm_refusal(FakeConfig(), _transport(agent_mode="shadow"), NOW) is None


def test_disarm_is_safe_after_healthy_core_takes_over():
    assert _disarm_refusal(FakeConfig(), _transport(core_age=1), NOW) is None


def test_disarm_refuses_live_agent_without_healthy_core():
    assert "核心未健康接管" in _disarm_refusal(
        FakeConfig(), _transport(core_age=None), NOW,
    )
    assert "核心未健康接管" in _disarm_refusal(
        FakeConfig(), _transport(core_age=1, timed_out=True), NOW,
    )
