"""月度重启演练的执行日：当月最后一个休市日。

重启会让整条交易链断 5 分钟左右，所以只能排在不开盘的日子。
"""

from datetime import date, timedelta

import pytest

from qsys.core.calendar import AShareCalendar, last_closed_day_of_month


def _trading(year, month, days):
    return AShareCalendar([date(year, month, d) for d in days])


def test_a_month_ending_on_a_weekend_uses_the_last_day():
    """2026-10-31 是周六，当天就是答案。"""
    cal = _trading(2026, 10, [29, 30])

    assert last_closed_day_of_month(cal, 2026, 10) == date(2026, 10, 31)


def test_a_month_ending_on_a_trading_day_walks_back():
    """2026-11-30 是周一且开盘，往回退到 11-29 周日。"""
    cal = _trading(2026, 11, [26, 27, 30])

    assert last_closed_day_of_month(cal, 2026, 11) == date(2026, 11, 29)


def test_a_holiday_weekday_at_month_end_counts_as_closed():
    """长假里的工作日不开盘，照样算休市日——不能只看星期几。"""
    cal = _trading(2026, 1, [28, 29])        # 1/30、1/31 当作春节休市

    assert last_closed_day_of_month(cal, 2026, 1) == date(2026, 1, 31)


def test_the_answer_is_never_a_trading_day():
    """这是这个函数唯一不能错的事。"""
    cal = _trading(2026, 11, [26, 27, 30])
    got = last_closed_day_of_month(cal, 2026, 11)

    assert not cal.is_trading_day(got)


def test_the_answer_stays_inside_the_asked_month():
    for month, days in ((10, [29, 30]), (11, [26, 27, 30]), (12, [29, 30, 31])):
        got = last_closed_day_of_month(_trading(2026, month, days), 2026, month)
        assert (got.year, got.month) == (2026, month)


def test_a_month_with_no_closed_day_is_refused_rather_than_guessed():
    """A 股每周必有周末，真发生就是日历坏了——宁可报错，不要猜一天去重启生产。"""
    every_day = AShareCalendar([
        date(2026, 2, 1) + timedelta(days=i) for i in range(28)
    ])

    with pytest.raises(ValueError):
        last_closed_day_of_month(every_day, 2026, 2)
