from datetime import date, datetime

import pytest

from qsys.core.calendar import AShareCalendar
from qsys.core.clock import BacktestClock, Phase

SESSIONS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
CAL = AShareCalendar(SESSIONS)


@pytest.fixture
def clock():
    return BacktestClock(CAL, SESSIONS[0], SESSIONS[-1])


def test_ticks_yield_three_phases_per_session(clock):
    ticks = list(clock.ticks())
    assert len(ticks) == 9


def test_phase_order_within_a_session(clock):
    phases = [phase for _, phase in clock.ticks()][:3]
    assert phases == [Phase.SESSION_START, Phase.BAR, Phase.SESSION_END]


def test_tick_times_match_phase(clock):
    ticks = list(clock.ticks())[:3]
    assert ticks[0][0] == datetime(2024, 1, 2, 9, 15)
    assert ticks[1][0] == datetime(2024, 1, 2, 15, 0)
    assert ticks[2][0] == datetime(2024, 1, 2, 15, 30)


def test_bar_phase_time_equals_calendar_close(clock):
    bar_times = [t for t, phase in clock.ticks() if phase is Phase.BAR]
    assert bar_times[0] == CAL.close_time(SESSIONS[0])


def test_now_advances_with_ticks(clock):
    seen = []
    for tick_time, _ in clock.ticks():
        seen.append((tick_time, clock.now))
    assert all(t == n for t, n in seen)


def test_now_before_first_tick_raises():
    clock = BacktestClock(CAL, SESSIONS[0], SESSIONS[-1])
    with pytest.raises(RuntimeError, match="clock has not started"):
        _ = clock.now


def test_session_tracks_current_trading_day(clock):
    sessions = []
    for _, phase in clock.ticks():
        if phase is Phase.BAR:
            sessions.append(clock.session)
    assert sessions == SESSIONS


def test_range_is_clipped_to_calendar(clock):
    narrow = BacktestClock(CAL, date(2024, 1, 3), date(2024, 1, 3))
    assert len({t.date() for t, _ in narrow.ticks()}) == 1


def test_empty_range_yields_nothing():
    empty = BacktestClock(CAL, date(2023, 1, 1), date(2023, 12, 31))
    assert list(empty.ticks()) == []


def test_ticks_are_monotonic(clock):
    times = [t for t, _ in clock.ticks()]
    assert times == sorted(times)
