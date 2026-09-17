"""每日增量只该下缺的那部分。"""

from datetime import date, datetime

import polars as pl
import pyarrow as pa

from qsys.data import schema
from qsys.data.pipeline.minutes import missing_stock_days

D = [date(2024, 1, d) for d in (2, 3, 4)]


def candidates(pairs) -> pl.DataFrame:
    return pl.DataFrame(
        {"symbol": [p[0] for p in pairs], "dt": [p[1] for p in pairs]},
        schema={"symbol": pl.Utf8, "dt": pl.Date},
    )


def seed_minutes(warehouse, pairs) -> None:
    """每个 (symbol, day) 放一根 09:30 的 bar。"""
    if not pairs:
        return
    warehouse.write(
        "minute_bars",
        pa.table(
            {
                "symbol": [p[0] for p in pairs],
                "dt": [datetime.combine(p[1], datetime.min.time()).replace(hour=9, minute=30) for p in pairs],
                "open": [10.0] * len(pairs),
                "high": [10.0] * len(pairs),
                "low": [10.0] * len(pairs),
                "close": [10.0] * len(pairs),
                "volume": [100] * len(pairs),
                "amount": [0.0] * len(pairs),
            },
            schema=schema.MINUTE_BARS,
        ),
        year=2024,
    )


def test_everything_is_missing_on_an_empty_warehouse(warehouse):
    want = candidates([("600001.SH", D[0]), ("600002.SH", D[1])])
    assert missing_stock_days(warehouse, want).height == 2


def test_already_downloaded_pairs_are_excluded(warehouse):
    seed_minutes(warehouse, [("600001.SH", D[0])])
    want = candidates([("600001.SH", D[0]), ("600002.SH", D[1])])
    out = missing_stock_days(warehouse, want)
    assert out.rows() == [("600002.SH", D[1])]


def test_same_symbol_different_day_still_counts_as_missing(warehouse):
    """有 1/2 的数据不代表 1/3 也有。"""
    seed_minutes(warehouse, [("600001.SH", D[0])])
    want = candidates([("600001.SH", D[1])])
    assert missing_stock_days(warehouse, want).rows() == [("600001.SH", D[1])]


def test_nothing_missing_when_all_present(warehouse):
    pairs = [("600001.SH", D[0]), ("600002.SH", D[1])]
    seed_minutes(warehouse, pairs)
    assert missing_stock_days(warehouse, candidates(pairs)).height == 0


def test_empty_candidates_yields_empty(warehouse):
    empty = pl.DataFrame(schema={"symbol": pl.Utf8, "dt": pl.Date})
    assert missing_stock_days(warehouse, empty).height == 0


def test_result_keeps_the_manifest_shape(warehouse):
    want = candidates([("600001.SH", D[0])])
    out = missing_stock_days(warehouse, want)
    assert out.columns == ["symbol", "dt"]


# --- D-20：全市场清单 -------------------------------------------------------

from datetime import date as _date  # noqa: E402

from qsys.data.pipeline.candidates import full_market_stock_days  # noqa: E402


def test_full_market_manifest_crosses_symbols_with_sessions_since_the_cutover():
    sessions = [_date(2026, 9, 16), _date(2026, 9, 17), _date(2026, 9, 18)]
    got = full_market_stock_days(["600000.SH", "000001.SZ"], sessions, since=_date(2026, 9, 17))
    assert got.sort(["dt", "symbol"]).rows() == [
        ("000001.SZ", _date(2026, 9, 17)), ("600000.SH", _date(2026, 9, 17)),
        ("000001.SZ", _date(2026, 9, 18)), ("600000.SH", _date(2026, 9, 18)),
    ]


def test_full_market_manifest_is_empty_before_the_cutover():
    assert full_market_stock_days(["600000.SH"], [_date(2026, 9, 16)], since=_date(2026, 9, 17)).height == 0
