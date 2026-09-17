"""D-20：QMT 回补 2026 全市场分钟线的纯逻辑。"""

from datetime import date, datetime

import polars as pl

from qsys.data.pipeline.qmt_backfill import in_quiet_hours, verify_day

DAY = date(2026, 5, 15)


def _bars(symbol, n=241, volume=10):
    times = [datetime(2026, 5, 15, 9, 30)] + \
        [datetime(2026, 5, 15, 9, 31) + (datetime(2026, 5, 15, 9, 32) - datetime(2026, 5, 15, 9, 31)) * i for i in range(120)] + \
        [datetime(2026, 5, 15, 13, 1) + (datetime(2026, 5, 15, 13, 2) - datetime(2026, 5, 15, 13, 1)) * i for i in range(120)]
    times = times[:n]
    return pl.DataFrame({"symbol": [symbol] * len(times), "dt": times, "volume": [volume] * len(times)})


def test_trading_hours_on_a_session_day_are_not_quiet():
    sessions = {DAY}
    assert not in_quiet_hours(datetime(2026, 5, 15, 10, 0), sessions)
    assert not in_quiet_hours(datetime(2026, 5, 15, 8, 50), sessions)
    assert in_quiet_hours(datetime(2026, 5, 15, 15, 50), sessions)
    assert in_quiet_hours(datetime(2026, 5, 15, 8, 49), sessions)
    assert in_quiet_hours(datetime(2026, 5, 16, 11, 0), sessions)  # 周六


def test_verify_day_passes_a_complete_day():
    bars = pl.concat([_bars("600000.SH"), _bars("000001.SZ")])
    daily = pl.DataFrame({"symbol": ["600000.SH", "000001.SZ"], "volume": [2410.0, 2410.0]})
    report = verify_day(DAY, bars, daily)
    assert report["ok"] is True
    assert report["symbols_expected"] == 2 and report["symbols_with_bars"] == 2
    assert report["non_241"] == 0 and report["volume_mismatch"] == 0


def test_verify_day_reports_missing_symbols_short_days_and_volume_gaps():
    bars = pl.concat([_bars("600000.SH", n=240), _bars("000001.SZ", volume=9)])
    daily = pl.DataFrame({"symbol": ["600000.SH", "000001.SZ", "300750.SZ"],
                          "volume": [2400.0, 2410.0, 100.0]})
    report = verify_day(DAY, bars, daily)
    assert report["ok"] is False
    assert report["missing_symbols"] == ["300750.SZ"]
    assert report["non_241"] == 1
    assert report["volume_mismatch"] == 1
