from datetime import date, timedelta
from types import SimpleNamespace

import scripts.run_paper as runner


class _DescendingCalendarClient:
    def call(self, *args, **kwargs):
        today = date.today()
        return [
            {"cal_date": (today + timedelta(days=60)).strftime("%Y%m%d"), "is_open": 1},
            {"cal_date": today.strftime("%Y%m%d"), "is_open": 1},
            {"cal_date": (today - timedelta(days=60)).strftime("%Y%m%d"), "is_open": 1},
        ]


def test_trading_calendar_accepts_descending_tushare_rows(monkeypatch):
    monkeypatch.setattr(
        runner, "load_tushare_config", lambda path: SimpleNamespace(token="test")
    )
    monkeypatch.setattr(runner, "TushareClient", lambda token: _DescendingCalendarClient())

    calendar = runner.trading_calendar(None)

    assert calendar.is_trading_day(date.today() + timedelta(days=60))
