"""从股本与收盘价派生历史估值。"""

from datetime import date

import polars as pl
import pyarrow as pa
import pytest

from qsys.data import schema
from qsys.data.pipeline.valuation import build_valuation
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

D = [date(2024, 1, d) for d in (2, 3, 4, 5)]


def seed(warehouse: Warehouse, write_daily_bars, bars, capital):
    """bars: [(symbol, dt, close, volume)]；capital: [(symbol, dt, total, circ)]"""
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": [b[0] for b in bars],
        "dt": [b[1] for b in bars],
        "open": [b[2] for b in bars],
        "high": [b[2] for b in bars],
        "low": [b[2] for b in bars],
        "close": [b[2] for b in bars],
        "volume": [float(b[3]) for b in bars],
        "amount": [0.0] * len(bars),
    }))
    warehouse.write(
        "share_capital",
        pa.table(
            {
                "symbol": [c[0] for c in capital],
                "dt": [c[1] for c in capital],
                "ann_date": [c[1] for c in capital],
                "total": [c[2] for c in capital],
                "circulating": [c[3] for c in capital],
            },
            schema=schema.SHARE_CAPITAL,
        ),
    )


def value_of(warehouse, symbol, dt):
    df = WarehouseQuery(warehouse).valuation(dt, dt)
    return df.filter(df["symbol"] == symbol).row(0, named=True)


def test_market_cap_is_close_times_total_shares_in_yi(warehouse, write_daily_bars):
    """市值单位为亿元，与聚宽 get_valuation 一致。"""
    seed(
        warehouse, write_daily_bars,
        [("600036.SH", D[0], 10.0, 1000)],
        [("600036.SH", date(2023, 12, 31), 1.0e10, 0.8e10)],
    )
    build_valuation(warehouse, D[0], D[0])
    row = value_of(warehouse, "600036.SH", D[0])
    assert row["market_cap"] == pytest.approx(1000.0)  # 10 x 1e10 / 1e8
    assert row["circulating_market_cap"] == pytest.approx(800.0)


def test_uses_the_latest_capital_effective_on_or_before_the_day(warehouse, write_daily_bars):
    """股本按生效日向前找最近一条，不能用未来的股本。"""
    seed(
        warehouse, write_daily_bars,
        [("600036.SH", D[0], 10.0, 1000), ("600036.SH", D[3], 10.0, 1000)],
        [
            ("600036.SH", date(2023, 12, 31), 1.0e10, 0.8e10),
            ("600036.SH", D[2], 2.0e10, 1.6e10),
        ],
    )
    build_valuation(warehouse, D[0], D[3])
    assert value_of(warehouse, "600036.SH", D[0])["market_cap"] == pytest.approx(1000.0)
    assert value_of(warehouse, "600036.SH", D[3])["market_cap"] == pytest.approx(2000.0)


def test_a_day_before_any_capital_record_is_skipped(warehouse, write_daily_bars):
    """没有可用股本就不该编造市值。"""
    seed(
        warehouse, write_daily_bars,
        [("600036.SH", D[0], 10.0, 1000)],
        [("600036.SH", D[3], 1.0e10, 0.8e10)],
    )
    assert build_valuation(warehouse, D[0], D[0]) == 0


def test_turnover_ratio_accounts_for_the_lot_unit(warehouse, write_daily_bars):
    """日线 volume 单位是手，换手率要乘 100 换成股。"""
    seed(
        warehouse, write_daily_bars,
        [("600036.SH", D[0], 10.0, 1_000_000)],
        [("600036.SH", date(2023, 12, 31), 1.0e10, 1.0e10)],
    )
    build_valuation(warehouse, D[0], D[0])
    row = value_of(warehouse, "600036.SH", D[0])
    # 100 万手 = 1 亿股；流通 100 亿股 -> 换手率 1%
    assert row["turnover_ratio"] == pytest.approx(1.0)


def test_close_is_carried_through(warehouse, write_daily_bars):
    seed(
        warehouse, write_daily_bars,
        [("600036.SH", D[0], 12.5, 1000)],
        [("600036.SH", date(2023, 12, 31), 1.0e10, 0.8e10)],
    )
    build_valuation(warehouse, D[0], D[0])
    assert value_of(warehouse, "600036.SH", D[0])["close"] == pytest.approx(12.5)


def test_symbols_are_independent(warehouse, write_daily_bars):
    seed(
        warehouse, write_daily_bars,
        [("600036.SH", D[0], 10.0, 1000), ("000001.SZ", D[0], 20.0, 1000)],
        [
            ("600036.SH", date(2023, 12, 31), 1.0e10, 0.8e10),
            ("000001.SZ", date(2023, 12, 31), 2.0e10, 2.0e10),
        ],
    )
    build_valuation(warehouse, D[0], D[0])
    assert value_of(warehouse, "600036.SH", D[0])["market_cap"] == pytest.approx(1000.0)
    assert value_of(warehouse, "000001.SZ", D[0])["market_cap"] == pytest.approx(4000.0)


def test_one_symbols_capital_does_not_leak_into_another(warehouse, write_daily_bars):
    """as-of join 必须按标的分组，否则会串到别人的股本。"""
    seed(
        warehouse, write_daily_bars,
        [("000001.SZ", D[3], 10.0, 1000)],
        [
            ("600036.SH", D[2], 9.9e10, 9.9e10),
            ("000001.SZ", date(2023, 12, 31), 1.0e10, 1.0e10),
        ],
    )
    build_valuation(warehouse, D[3], D[3])
    assert value_of(warehouse, "000001.SZ", D[3])["market_cap"] == pytest.approx(1000.0)


def test_rebuild_is_idempotent(warehouse, write_daily_bars):
    seed(
        warehouse, write_daily_bars,
        [("600036.SH", D[0], 10.0, 1000), ("600036.SH", D[1], 11.0, 1000)],
        [("600036.SH", date(2023, 12, 31), 1.0e10, 0.8e10)],
    )
    assert build_valuation(warehouse, D[0], D[1]) == 2
    assert build_valuation(warehouse, D[0], D[1]) == 2
    assert WarehouseQuery(warehouse).valuation().height == 2


def test_partial_rebuild_keeps_the_rest_of_the_year(warehouse, write_daily_bars):
    """与 flags/regime 同一个坑：分区整年覆盖写，只重算一段不能抹掉其余。"""
    seed(
        warehouse, write_daily_bars,
        [("600036.SH", d, 10.0, 1000) for d in D],
        [("600036.SH", date(2023, 12, 31), 1.0e10, 0.8e10)],
    )
    assert build_valuation(warehouse, D[0], D[-1]) == 4
    build_valuation(warehouse, D[-1], D[-1])
    assert WarehouseQuery(warehouse).valuation().height == 4


def test_empty_warehouse_yields_zero(warehouse, write_daily_bars):
    assert build_valuation(warehouse, D[0], D[-1]) == 0
