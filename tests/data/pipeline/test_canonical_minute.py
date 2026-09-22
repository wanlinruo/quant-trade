from datetime import date, datetime

import polars as pl
import pyarrow as pa

from qsys.data import schema
from qsys.data.pipeline.canonical_minute import (
    build_bar_minute_1m,
    canonical_minute_preflight,
    minute_reconciliation,
)
from qsys.data.query import WarehouseQuery


DAY = date(2024, 1, 2)


def _row(symbol: str, minute: int, price: float, volume: int) -> dict:
    return {
        "symbol": symbol, "dt": datetime(2024, 1, 2, 9, minute),
        "open": price, "high": price, "low": price, "close": price,
        "volume": volume, "amount": price * volume,
    }


def _seed(warehouse) -> None:
    qmt = pl.DataFrame([
        _row("600000.SH", 30, 10.0, 10),
        _row("600001.SH", 30, 20.0, 20),
    ])
    warehouse.write_day(
        "minute_bars", qmt.to_arrow().cast(schema.MINUTE_BARS), day=DAY
    )
    paid = pl.DataFrame([
        {**_row("600000.SH", 30, 11.0, 1000), "factor": 1.0},
        {**_row("600000.SH", 31, 11.1, 2000), "factor": 1.0},
    ])
    target = warehouse.root / "paid_minute_bars/year=2024/date=20240102"
    target.mkdir(parents=True)
    paid.write_parquet(target / "part.parquet")


def test_build_selects_one_source_per_symbol_day_and_normalizes_volume(warehouse):
    _seed(warehouse)
    result = build_bar_minute_1m(warehouse, DAY, DAY)
    assert result.rows == 3
    got = WarehouseQuery(warehouse).bar_minute_1m(
        None, datetime(2024, 1, 2), datetime(2024, 1, 2, 23, 59)
    )
    paid = got.filter(pl.col("symbol") == "600000.SH")
    assert paid["source"].unique().to_list() == ["paid"]
    assert paid["close"].to_list() == [11.0, 11.1]
    assert paid["volume"].to_list() == [10.0, 20.0]
    assert paid["visible_at"].to_list()[0] == datetime(2024, 1, 2, 9, 31)
    assert got.filter(pl.col("symbol") == "600001.SH")["source"].to_list() == ["qmt"]
    assert minute_reconciliation(warehouse, DAY).matches


def test_canonical_query_does_not_fall_back_to_legacy_sources(warehouse):
    _seed(warehouse)
    got = WarehouseQuery(warehouse).bar_minute_1m(
        None, datetime(2024, 1, 2), datetime(2024, 1, 2, 23, 59)
    )
    assert got.height == 0
    assert got.columns == list(schema.BAR_MINUTE_1M.names)


def test_build_quarantines_a_whole_invalid_symbol_day(warehouse):
    valid = pl.DataFrame([_row("600000.SH", 30, 10.0, 10)])
    invalid = pl.DataFrame([
        _row("920305.BJ", 30, 0.0, 10),
        _row("920305.BJ", 31, 10.0, 10),
    ])
    source = pl.concat([valid, invalid])
    warehouse.write_day(
        "minute_bars", source.to_arrow().cast(schema.MINUTE_BARS), day=DAY
    )

    result = build_bar_minute_1m(warehouse, DAY, DAY)
    got = WarehouseQuery(warehouse).bar_minute_1m(
        None, datetime(2024, 1, 2), datetime(2024, 1, 2, 23, 59)
    )

    assert result.rows == 1
    assert got["symbol"].to_list() == ["600000.SH"]


def _daily(warehouse, symbols) -> None:
    rows = pl.DataFrame({"symbol": symbols, "dt": [DAY] * len(symbols)})
    table = pa.Table.from_pylist(
        [{name: row.get(name) for name in schema.TUSHARE_DAILY.names} for row in rows.to_dicts()],
        schema=schema.TUSHARE_DAILY,
    )
    warehouse.write("tushare_daily", table, year=2024)


def test_build_drops_qmt_suspension_placeholders_without_a_daily_bar(warehouse):
    source = pl.DataFrame([
        _row("600000.SH", 30, 10.0, 10),
        _row("600002.SH", 30, 8.0, 0),      # 停牌占位：零成交、平价、无日线
        _row("600002.SH", 31, 8.0, 0),
        _row("600003.SH", 30, 9.0, 0),      # 零成交但有日线：保留
        _row("600003.SH", 31, 9.0, 0),
    ])
    warehouse.write_day(
        "minute_bars", source.to_arrow().cast(schema.MINUTE_BARS), day=DAY
    )
    _daily(warehouse, ["600000.SH", "600003.SH"])

    build_bar_minute_1m(warehouse, DAY, DAY)
    got = WarehouseQuery(warehouse).bar_minute_1m(
        None, datetime(2024, 1, 2), datetime(2024, 1, 2, 23, 59)
    )

    assert sorted(got["symbol"].unique().to_list()) == ["600000.SH", "600003.SH"]


def test_preflight_rejects_an_empty_source_environment(warehouse):
    assert canonical_minute_preflight(warehouse, DAY, DAY) == (
        "没有任何已发布分钟来源",
    )
