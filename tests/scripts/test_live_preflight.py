from datetime import datetime, timedelta

from qsys.live.messages import Heartbeat
from scripts.live_preflight import agent_live_posture, heartbeat_freshness


NOW = datetime(2026, 9, 20, 12)


def test_off_hours_market_heartbeat_uses_declared_cadence():
    beat = Heartbeat(
        source="market", at=NOW - timedelta(seconds=29),
        expected_interval_s=30, phase="off_hours",
    )

    assert heartbeat_freshness(beat, NOW) == (True, 29.0, 90.0)
    assert heartbeat_freshness(
        beat.model_copy(update={"at": NOW - timedelta(seconds=91)}), NOW
    )[0] is False


def test_active_heartbeat_keeps_strict_ten_second_threshold():
    beat = Heartbeat(
        source="market", at=NOW - timedelta(seconds=10),
        expected_interval_s=1, phase="morning",
    )

    assert heartbeat_freshness(beat, NOW) == (True, 10.0, 10.0)
    assert heartbeat_freshness(
        beat.model_copy(update={"at": NOW - timedelta(seconds=10.1)}), NOW
    )[0] is False


def test_heartbeat_rejects_missing_or_future_timestamp():
    assert heartbeat_freshness(None, NOW)[0] is False
    future = Heartbeat(source="agent", at=NOW + timedelta(seconds=1.1))
    assert heartbeat_freshness(future, NOW)[0] is False


def test_agent_live_posture_requires_windows_to_announce_every_guard():
    live = Heartbeat(
        source="agent", at=NOW, trading_mode="live", orders_enabled=True,
        account_protected=True, local_kill_engaged=False,
    )
    assert all(ok for ok, _ in agent_live_posture(live))

    shadow = live.model_copy(update={"trading_mode": "shadow"})
    results = agent_live_posture(shadow)
    assert results[0][0] is False
    assert all(ok for ok, _ in results[1:])
    assert not any(ok for ok, _ in agent_live_posture(None))


def test_agent_live_posture_accepts_reported_engaged_kill_for_safe_service_start():
    engaged = Heartbeat(
        source="agent", at=NOW, trading_mode="live", orders_enabled=True,
        account_protected=True, local_kill_engaged=True,
    )

    checks = agent_live_posture(engaged)

    assert all(ok for ok, _ in checks)
    assert "已生效" in checks[-1][1]
    strict_checks = agent_live_posture(engaged, require_kill_released=True)
    assert strict_checks[-1] == (False, "Windows agent 本地停机闸未生效")


def test_agent_live_posture_rejects_unknown_kill_state():
    unknown = Heartbeat(
        source="agent", at=NOW, trading_mode="live", orders_enabled=True,
        account_protected=True, local_kill_engaged=None,
    )

    assert agent_live_posture(unknown)[-1][0] is False
