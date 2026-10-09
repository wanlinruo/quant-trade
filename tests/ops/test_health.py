from datetime import date, datetime, timedelta, timezone

from qsys.ops.health import (
    AFTERNOON,
    CALENDAR_UNKNOWN,
    DEGRADED,
    DOWN,
    HEALTHY,
    LUNCH,
    MORNING,
    OFF_HOURS,
    PREPARE,
    UNKNOWN,
    HeartbeatCadence,
    HealthObservation,
    HealthStore,
    assess_heartbeat,
    heartbeat_policy,
    trading_phase,
)

DAY = date(2026, 9, 17)
TRADING = {DAY}


def at(hour, minute=0, second=0):
    return datetime(2026, 9, 17, hour, minute, second)


def test_trading_phase_boundaries_and_calendar_failure():
    assert trading_phase(at(8, 44), TRADING) == OFF_HOURS
    assert trading_phase(at(8, 45), TRADING) == PREPARE
    assert trading_phase(at(9), TRADING) == MORNING
    assert trading_phase(at(11, 30), TRADING) == LUNCH
    assert trading_phase(at(13), TRADING) == AFTERNOON
    assert trading_phase(at(15, 40), TRADING) == OFF_HOURS
    assert trading_phase(at(10), None) == CALENDAR_UNKNOWN


def test_market_policy_is_adaptive_but_unknown_calendar_is_conservative():
    assert heartbeat_policy("market", MORNING).expected_interval_s == 1
    assert heartbeat_policy("market", LUNCH).expected_interval_s == 2
    assert heartbeat_policy("market", OFF_HOURS).expected_interval_s == 30
    assert heartbeat_policy("market", OFF_HOURS, active=True).expected_interval_s == 1
    assert heartbeat_policy("market", CALENDAR_UNKNOWN).expected_interval_s == 1
    assert heartbeat_policy("paper", CALENDAR_UNKNOWN).expected_interval_s == 2


def test_cadence_changes_write_frequency_without_sleeping_business_loop():
    cadence = HeartbeatCadence()
    assert cadence.due(100, 30) is True
    assert cadence.due(101, 30) is False
    assert cadence.due(102, 1) is True  # 变为活跃状态时不等待原 30 秒截止点
    assert cadence.due(102.5, 1) is False


def test_heartbeat_assessment_uses_warn_down_and_clock_skew_boundaries():
    assert assess_heartbeat("market", at(10), at(10, 0, 3), TRADING).state == HEALTHY
    assert assess_heartbeat("market", at(10), at(10, 0, 4), TRADING).state == DEGRADED
    assert assess_heartbeat("market", at(10), at(10, 0, 11), TRADING).state == DOWN
    assert assess_heartbeat("market", at(10, 0, 5), at(10), TRADING).state == UNKNOWN
    assert assess_heartbeat("market", at(10), at(10, 0, 1), None).state == UNKNOWN


def test_market_calendar_cadence_mismatch_degrades_before_true_timeout():
    mismatch = assess_heartbeat(
        "market", at(8, 44, 38), at(8, 45, 3), TRADING,
        advertised_interval_s=30, advertised_phase=OFF_HOURS,
    )
    assert mismatch.state == DEGRADED
    assert mismatch.reason_code == "heartbeat_cadence_mismatch"
    assert mismatch.policy.down_after_s == 90

    stopped = assess_heartbeat(
        "market", at(8, 44, 38), at(8, 46, 9), TRADING,
        advertised_interval_s=30, advertised_phase=OFF_HOURS,
    )
    assert stopped.state == DOWN
    assert stopped.reason_code == "heartbeat_stale"


def observation(moment, state=HEALTHY):
    return HealthObservation(
        service_id="market-gateway", observed_at_utc=moment,
        phase=MORNING, expected_interval_s=1, warn_after_s=3, down_after_s=10,
        state=state, process_state=HEALTHY, business_state=state,
        reason_code="heartbeat_fresh" if state == HEALTHY else "heartbeat_stale",
        source="redis", last_beat_at_utc=moment, details={"host": "Windows"},
    )


def test_store_writes_changes_and_five_minute_checkpoints_only(tmp_path):
    store = HealthStore(tmp_path / "health.sqlite3")
    start = datetime(2026, 9, 17, 2, tzinfo=timezone.utc)
    assert store.record(observation(start)) is True
    assert store.record(observation(start + timedelta(minutes=1))) is False
    assert store.record(observation(start + timedelta(minutes=5))) is True
    assert store.record(observation(start + timedelta(minutes=6), DOWN)) is True
    rows = store.history(start, start + timedelta(minutes=10))
    assert [row["checkpoint"] for row in rows] == [False, True, False]
    assert store.latest("market-gateway")[0]["state"] == DOWN


def test_timeline_marks_unobserved_gap_unknown_instead_of_filling_green(tmp_path):
    store = HealthStore(tmp_path / "health.sqlite3")
    start = datetime(2026, 9, 17, 2, tzinfo=timezone.utc)
    store.record(observation(start))
    store.record(observation(start + timedelta(minutes=20), DOWN))
    segments = store.timeline(start, start + timedelta(minutes=30))["market-gateway"]
    assert [(s["state"], s["duration_s"]) for s in segments] == [
        (HEALTHY, 600), (UNKNOWN, 600), (DOWN, 600),
    ]


def test_recovery_requires_two_consecutive_healthy_observations(tmp_path):
    store = HealthStore(tmp_path / "health.sqlite3")
    start = datetime(2026, 9, 17, 2, tzinfo=timezone.utc)
    store.record(observation(start, DOWN))
    assert store.record(observation(start + timedelta(seconds=5), HEALTHY)) is False
    assert store.latest("market-gateway")[0]["state"] == DOWN
    assert store.record(observation(start + timedelta(seconds=10), HEALTHY)) is True
    assert store.latest("market-gateway")[0]["state"] == HEALTHY
