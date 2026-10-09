from datetime import datetime, timedelta

from qsys.live.heartbeat import CoreHeartbeatPublisher, HeartbeatMonitor
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


def test_beat_can_advertise_operational_cadence_without_changing_safety_api():
    m = monitor()
    m.beat("market", now=NOW, expected_interval_s=30, phase="off_hours")
    beat = m.read("market")
    assert beat is not None
    assert beat.expected_interval_s == 30 and beat.phase == "off_hours"
    assert m.is_alive("market", max_age_seconds=60, now=NOW) is True


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


def test_core_publisher_separates_liveness_from_event_progress():
    transport = MemoryTransport()
    elapsed = [100.0]
    timed_out = []
    publisher = CoreHeartbeatPublisher(
        transport, interval_s=1, event_timeout_s=30,
        now=lambda: NOW + timedelta(seconds=elapsed[0] - 100),
        monotonic=lambda: elapsed[0], on_event_timeout=timed_out.append,
    )

    publisher.begin_event("2024-01-03:bar:15:00:00", "bar")
    elapsed[0] += 20
    publisher.pulse_once()
    beat = HeartbeatMonitor(transport).read("core")

    assert beat is not None
    assert beat.activity == "event" and beat.event_phase == "bar"
    assert beat.event_timed_out is False
    assert HeartbeatMonitor(transport).is_alive("core", now=beat.at) is True
    assert timed_out == []


def test_timed_out_event_stays_live_but_fires_fail_closed_callback_once():
    transport = MemoryTransport()
    elapsed = [0.0]
    timed_out = []
    publisher = CoreHeartbeatPublisher(
        transport, event_timeout_s=30, monotonic=lambda: elapsed[0],
        on_event_timeout=timed_out.append,
    )
    key = "2024-01-03:bar:15:00:00"
    publisher.begin_event(key, "bar")

    elapsed[0] = 31.0
    publisher.pulse_once()
    publisher.pulse_once()
    beat = HeartbeatMonitor(transport).read("core")

    assert beat is not None and beat.event_timed_out is True
    assert timed_out == [key]


def test_background_publisher_refreshes_while_caller_is_blocked():
    import time

    transport = MemoryTransport()
    publisher = CoreHeartbeatPublisher(
        transport, interval_s=0.01, event_timeout_s=1,
    )
    with publisher:
        publisher.begin_event("event", "bar")
        first = HeartbeatMonitor(transport).read("core")
        time.sleep(0.04)
        later = HeartbeatMonitor(transport).read("core")

    assert first is not None and later is not None
    assert later.at > first.at and later.event_key == "event"
