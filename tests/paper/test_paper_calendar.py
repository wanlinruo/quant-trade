"""C：模拟账户生效日按真实交易日历算，不按「周一到周五」猜。"""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

from qsys.core.calendar import AShareCalendar
from qsys.paper import calendar as paper_calendar
from qsys.paper.calendar import first_complete_session

# 2026 国庆：09-30（周三）是节前最后一个交易日，10-01 ~ 10-07 休市，10-08（周四）开市
SESSIONS = AShareCalendar([date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 8),
                           date(2026, 10, 9)])


def test_before_the_open_on_a_trading_day_is_today():
    assert first_complete_session(datetime(2026, 9, 30, 9, 14), SESSIONS) == date(2026, 9, 30)


def test_after_the_open_skips_the_holiday():
    assert first_complete_session(datetime(2026, 9, 30, 9, 15), SESSIONS) == date(2026, 10, 8)


def test_a_weekday_holiday_is_not_an_activation_date():
    assert first_complete_session(datetime(2026, 10, 2, 8, 0), SESSIONS) == date(2026, 10, 8)


class _DescendingCalendarClient:
    def call(self, *args, **kwargs):
        today = date.today()
        return [
            {"cal_date": (today + timedelta(days=60)).strftime("%Y%m%d"), "is_open": 1},
            {"cal_date": today.strftime("%Y%m%d"), "is_open": 1},
            {"cal_date": (today - timedelta(days=60)).strftime("%Y%m%d"), "is_open": 1},
        ]


def test_trading_calendar_accepts_descending_tushare_rows(monkeypatch):
    monkeypatch.setattr(paper_calendar, "load_tushare_config",
                        lambda path: SimpleNamespace(token="test"))
    monkeypatch.setattr(paper_calendar, "TushareClient", lambda token: _DescendingCalendarClient())

    calendar = paper_calendar.load_trading_calendar(None)

    assert calendar.is_trading_day(date.today() + timedelta(days=60))
