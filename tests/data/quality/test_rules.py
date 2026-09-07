from datetime import date

import polars as pl

from qsys.core.calendar import AShareCalendar
from qsys.data.quality.rules import (
    Severity,
    check_completeness,
    check_internal_consistency,
    check_price_limits,
)

SESSIONS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
CAL = AShareCalendar(SESSIONS)

BAR_SCHEMA = {
    "symbol": pl.Utf8,
    "dt": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Int64,
    "amount": pl.Float64,
}


def _bars(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=BAR_SCHEMA)


def _row(dt, close, *, symbol="600036.SH", open_=None, high=None, low=None, volume=1000):
    o = close if open_ is None else open_
    return {
        "symbol": symbol,
        "dt": dt,
        "open": o,
        "high": close if high is None else high,
        "low": close if low is None else low,
        "close": close,
        "volume": volume,
        "amount": close * volume,
    }


def test_completeness_flags_missing_session():
    bars = _bars([_row(date(2024, 1, 2), 10.0), _row(date(2024, 1, 4), 10.0)])
    v = check_completeness(bars, CAL, ["600036.SH"], date(2024, 1, 2), date(2024, 1, 4))
    assert [x.dt for x in v] == [date(2024, 1, 3)]
    assert v[0].severity is Severity.BLOCKING
    assert v[0].rule == "completeness"


def test_completeness_passes_when_all_sessions_present():
    bars = _bars([_row(d, 10.0) for d in SESSIONS])
    assert check_completeness(bars, CAL, ["600036.SH"], date(2024, 1, 2), date(2024, 1, 4)) == []


def test_completeness_checks_each_expected_symbol():
    bars = _bars([_row(d, 10.0) for d in SESSIONS])
    v = check_completeness(
        bars, CAL, ["600036.SH", "000001.SZ"], date(2024, 1, 2), date(2024, 1, 4)
    )
    assert {x.symbol for x in v} == {"000001.SZ"}
    assert len(v) == 3


def test_price_limit_flags_main_board_over_ten_percent():
    bars = _bars([_row(date(2024, 1, 2), 10.0), _row(date(2024, 1, 3), 11.5)])
    v = check_price_limits(bars, {"600036.SH": "main"})
    assert [x.dt for x in v] == [date(2024, 1, 3)]
    assert v[0].severity is Severity.WARNING
    assert v[0].rule == "price_limit"


def test_price_limit_allows_main_board_exactly_ten_percent():
    bars = _bars([_row(date(2024, 1, 2), 10.0), _row(date(2024, 1, 3), 11.0)])
    assert check_price_limits(bars, {"600036.SH": "main"}) == []


def test_price_limit_allows_twenty_percent_on_gem():
    bars = _bars(
        [
            _row(date(2024, 1, 2), 10.0, symbol="300750.SZ"),
            _row(date(2024, 1, 3), 11.5, symbol="300750.SZ"),
        ]
    )
    assert check_price_limits(bars, {"300750.SZ": "gem"}) == []


def test_price_limit_allows_thirty_percent_on_bse():
    bars = _bars(
        [
            _row(date(2024, 1, 2), 10.0, symbol="830799.BJ"),
            _row(date(2024, 1, 3), 12.9, symbol="830799.BJ"),
        ]
    )
    assert check_price_limits(bars, {"830799.BJ": "bse"}) == []


def test_price_limit_skips_first_bar_of_each_symbol():
    bars = _bars([_row(date(2024, 1, 2), 10.0)])
    assert check_price_limits(bars, {"600036.SH": "main"}) == []


def test_consistency_flags_high_below_close():
    bars = _bars([_row(date(2024, 1, 2), 10.0, high=9.0, low=8.0)])
    v = check_internal_consistency(bars)
    assert [x.rule for x in v] == ["ohlc_order"]
    assert v[0].severity is Severity.BLOCKING


def test_consistency_flags_low_above_open():
    bars = _bars([_row(date(2024, 1, 2), 10.0, open_=9.0, high=11.0, low=9.5)])
    assert [x.rule for x in check_internal_consistency(bars)] == ["ohlc_order"]


def test_consistency_flags_nonpositive_price():
    bars = _bars([_row(date(2024, 1, 2), 0.0, high=0.0, low=0.0)])
    assert "non_positive_price" in {x.rule for x in check_internal_consistency(bars)}


def test_consistency_flags_negative_volume():
    bars = _bars([_row(date(2024, 1, 2), 10.0, volume=-1)])
    assert "negative_volume" in {x.rule for x in check_internal_consistency(bars)}


def test_consistency_passes_on_clean_bar():
    bars = _bars([_row(date(2024, 1, 2), 10.0, open_=9.8, high=10.2, low=9.7)])
    assert check_internal_consistency(bars) == []
