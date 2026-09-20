from datetime import datetime, timedelta

from qsys.live.messages import Heartbeat
from scripts.live_preflight import heartbeat_freshness


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
