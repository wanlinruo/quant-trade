"""The heartbeat exemption may only be removed after a safe handoff."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from qsys.live.config import StreamConfig
from qsys.live.messages import Heartbeat
from qsys.live.transport import MemoryTransport
from scripts.commissioning_lease import _disarm_refusal

NOW = datetime(2026, 10, 12, 10)


@dataclass
class FakeConfig:
    streams: StreamConfig = field(default_factory=StreamConfig)


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
