from datetime import date, datetime

import pytest

from qsys.core.calendar import AShareCalendar

SESSIONS = [
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 1, 8),
]


@pytest.fixture
def cal():
    return AShareCalendar(SESSIONS)


def test_is_trading_day(cal):
    assert cal.is_trading_day(date(2024, 1, 3)) is True
    assert cal.is_trading_day(date(2024, 1, 6)) is False


def test_sessions_in_range_is_inclusive_on_both_ends(cal):
    assert cal.sessions_in_range(date(2024, 1, 3), date(2024, 1, 5)) == [
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
    ]


def test_sessions_in_range_clips_to_known_sessions(cal):
    assert cal.sessions_in_range(date(2023, 1, 1), date(2024, 1, 2)) == [date(2024, 1, 2)]


def test_close_time_is_fifteen_hundred(cal):
    assert cal.close_time(date(2024, 1, 3)) == datetime(2024, 1, 3, 15, 0)


def test_close_time_rejects_non_trading_day(cal):
    with pytest.raises(ValueError, match="not a trading day"):
        cal.close_time(date(2024, 1, 6))


def test_next_session_skips_weekend(cal):
    assert cal.next_session(date(2024, 1, 5)) == date(2024, 1, 8)


def test_next_session_from_non_trading_day(cal):
    assert cal.next_session(date(2024, 1, 6)) == date(2024, 1, 8)


def test_prev_session(cal):
    assert cal.prev_session(date(2024, 1, 8)) == date(2024, 1, 5)


def test_next_session_beyond_known_range_raises(cal):
    with pytest.raises(ValueError, match="beyond calendar range"):
        cal.next_session(date(2024, 1, 8))


def test_sessions_are_deduplicated_and_sorted():
    cal = AShareCalendar([date(2024, 1, 3), date(2024, 1, 2), date(2024, 1, 3)])
    assert cal.sessions_in_range(date(2024, 1, 1), date(2024, 1, 31)) == [
        date(2024, 1, 2),
        date(2024, 1, 3),
    ]
