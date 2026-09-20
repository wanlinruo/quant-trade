from datetime import date, datetime, timedelta

import polars as pl

from qsys.data import schema
from qsys.data.access import StableMarketData, facade_reconciliation
from qsys.data.query import WarehouseQuery
from qsys.data.realtime import RealtimeMarketState
from qsys.live.messages import MarketQuote


DAY = date(2026, 9, 21)
SYMBOL = "600036.SH"


def _quote(at: datetime, price: float, volume: int, amount: float) -> MarketQuote:
    return MarketQuote(
        symbol=SYMBOL, at=at, price=price, open=10.0,
        high=max(10.0, price), low=min(10.0, price), prev_close=9.8,
        volume=volume, amount=amount,
    )


def _seed_canonical_daily(warehouse, day: date = DAY, close: float = 10.0):
    frame = pl.DataFrame([{
        "symbol": SYMBOL, "dt": day, "pre_close": 9.8,
        "open": 9.9, "high": close, "low": 9.8, "close": close,
        "volume": 100.0, "amount": 100_000.0,
    }])
    warehouse.write(
        "bar_daily", frame.to_arrow().cast(schema.BAR_DAILY), year=day.year
    )


def test_historical_facade_reads_only_canonical_tables(warehouse):
    _seed_canonical_daily(warehouse)
    stable = StableMarketData(WarehouseQuery(warehouse))
    got = stable.historical_daily_bars([SYMBOL], DAY, DAY)
    assert got["close"].to_list() == [10.0]
    assert got.columns == list(schema.BAR_DAILY.names)


def test_new_query_methods_never_fall_back_to_legacy_tables(warehouse):
    old_daily = {name: None for name in schema.TUSHARE_DAILY.names}
    old_daily.update({"symbol": SYMBOL, "dt": DAY, "close": 10.0})
    warehouse.write(
        "tushare_daily",
        pl.DataFrame([old_daily]).select(schema.TUSHARE_DAILY.names).to_arrow().cast(
            schema.TUSHARE_DAILY
        ), year=DAY.year,
    )
    query = WarehouseQuery(warehouse)
    assert query.daily_bars([SYMBOL], DAY, DAY).height == 1  # 旧兼容门面不变
    assert query.historical_daily_bars([SYMBOL], DAY, DAY).height == 0
    assert query.historical_minute_bars(
        [SYMBOL], datetime(2026, 9, 21), datetime(2026, 9, 21, 23, 59)
    ).height == 0


def test_data_page_read_only_entry_uses_the_stable_facade(warehouse):
    from qsys.web.data_views import DataService
    _seed_canonical_daily(warehouse)
    got = DataService(warehouse).historical_daily_bars([SYMBOL], DAY, DAY)
    assert got["close"].to_list() == [10.0]


def test_realtime_is_invisible_by_default_and_requires_explicit_as_of(warehouse):
    _seed_canonical_daily(warehouse, DAY - timedelta(days=1), 9.9)
    state = RealtimeMarketState()
    at = datetime(2026, 9, 21, 10, 0)
    state.ingest({SYMBOL: _quote(at, 10.2, 12_000, 120_000)}, received_at=at)
    stable = StableMarketData(WarehouseQuery(warehouse), state)
    historical = stable.daily_bars([SYMBOL], DAY - timedelta(days=1), DAY)
    assert historical["dt"].to_list() == [DAY - timedelta(days=1)]
    try:
        stable.daily_bars(
            [SYMBOL], DAY - timedelta(days=1), DAY, include_live=True
        )
    except ValueError as exc:
        assert "as_of" in str(exc)
    else:
        raise AssertionError("include_live=True 未要求 as_of")
    combined = stable.daily_bars(
        [SYMBOL], DAY - timedelta(days=1), DAY,
        include_live=True, as_of=at, max_age=timedelta(seconds=1),
    )
    assert combined["dt"].to_list() == [DAY - timedelta(days=1), DAY]
    assert combined["close"].to_list() == [9.9, 10.2]


def test_current_realtime_minute_requires_both_opt_ins(warehouse):
    state = RealtimeMarketState()
    at = datetime(2026, 9, 21, 10, 0, 10)
    state.ingest({SYMBOL: _quote(at, 10.0, 100, 1_000)}, received_at=at)
    stable = StableMarketData(WarehouseQuery(warehouse), state)
    start, end = datetime(2026, 9, 21, 9, 30), datetime(2026, 9, 21, 15)
    assert stable.minute_bars([SYMBOL], start, end).height == 0
    assert stable.minute_bars(
        [SYMBOL], start, end, include_live=True, as_of=at
    ).height == 0
    current = stable.minute_bars(
        [SYMBOL], start, end, include_live=True, as_of=at,
        include_current=True,
    )
    assert current.height == 1 and current["source"].to_list() == ["qmt"]
    assert current.select("finalized", "partial").row(0) == (False, True)


def test_old_and_new_facades_have_exact_migration_evidence(warehouse):
    old_daily = {name: None for name in schema.TUSHARE_DAILY.names}
    old_daily.update({
        "symbol": SYMBOL, "dt": DAY, "open": 9.9, "high": 10.1,
        "low": 9.8, "close": 10.1, "volume": 100.0, "amount": 100_000.0,
    })
    warehouse.write(
        "tushare_daily",
        pl.DataFrame([old_daily]).select(schema.TUSHARE_DAILY.names).to_arrow().cast(
            schema.TUSHARE_DAILY
        ), year=DAY.year,
    )
    _seed_canonical_daily(warehouse, close=10.1)
    old_minute = pl.DataFrame([{
        "symbol": SYMBOL, "dt": datetime(2026, 9, 21, 9, 31),
        "open": 10.0, "high": 10.1, "low": 10.0, "close": 10.1,
        "volume": 10, "amount": 10_100.0,
    }])
    warehouse.write_day(
        "minute_bars", old_minute.to_arrow().cast(schema.MINUTE_BARS), day=DAY
    )
    canonical = old_minute.rename({"dt": "ts"}).with_columns(
        pl.col("volume").cast(pl.Float64), pl.lit("qmt").alias("source"),
        pl.col("ts").alias("visible_at"),
    ).select(schema.BAR_MINUTE_1M.names)
    warehouse.write_day(
        "bar_minute_1m", canonical.to_arrow().cast(schema.BAR_MINUTE_1M), day=DAY
    )
    results = facade_reconciliation(WarehouseQuery(warehouse), DAY, DAY)
    assert all(result.matches for result in results.values())


def test_facade_reconciliation_reads_minute_data_one_day_at_a_time(warehouse):
    class DayBoundedQuery(WarehouseQuery):
        def minute_bars(self, symbols, start, end):
            assert start.date() == end.date()
            return super().minute_bars(symbols, start, end)

        def bar_minute_1m(self, symbols, start, end, *, columns=None):
            assert start.date() == end.date()
            return super().bar_minute_1m(
                symbols, start, end, columns=columns
            )

    results = facade_reconciliation(
        DayBoundedQuery(warehouse), DAY, DAY + timedelta(days=2)
    )
    assert results["minute"].matches
