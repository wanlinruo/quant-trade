from datetime import datetime, timedelta

from qsys.live.heartbeat import HeartbeatMonitor
from qsys.live.transport import MemoryTransport

NOW = datetime(2024, 1, 3, 10, 0)


def monitor() -> HeartbeatMonitor:
    return HeartbeatMonitor(MemoryTransport())


def test_no_beat_means_no_last_beat():
    assert monitor().last_beat("agent") is None


def test_beat_is_recorded():
    m = monitor()
    m.beat("agent", now=NOW)
    assert m.last_beat("agent") == NOW


def test_fresh_beat_is_alive():
    m = monitor()
    m.beat("agent", now=NOW)
    assert m.is_alive("agent", max_age_seconds=60, now=NOW + timedelta(seconds=30)) is True


def test_stale_beat_is_dead():
    m = monitor()
    m.beat("agent", now=NOW)
    assert m.is_alive("agent", max_age_seconds=60, now=NOW + timedelta(seconds=90)) is False


def test_never_beaten_source_is_dead():
    assert monitor().is_alive("agent", now=NOW) is False


def test_sources_are_independent():
    m = monitor()
    m.beat("agent", now=NOW)
    assert m.is_alive("agent", now=NOW) is True
    assert m.is_alive("core", now=NOW) is False


def test_dead_sources_lists_the_silent_ones():
    m = monitor()
    m.beat("agent", now=NOW)
    dead = m.dead_sources(["agent", "core"], max_age_seconds=60, now=NOW)
    assert dead == ("core",)


def test_dead_sources_is_empty_when_all_alive():
    m = monitor()
    m.beat("agent", now=NOW)
    m.beat("core", now=NOW)
    assert m.dead_sources(["agent", "core"], now=NOW) == ()


def test_later_beat_overwrites_the_earlier_one():
    m = monitor()
    m.beat("agent", now=NOW)
    later = NOW + timedelta(minutes=5)
    m.beat("agent", now=later)
    assert m.last_beat("agent") == later
