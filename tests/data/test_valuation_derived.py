"""Tushare 每日指标到策略估值表的无反算投影。"""

from datetime import date

import polars as pl
import pytest

from qsys.data.pipeline.valuation import build_valuation
from qsys.data.query import WarehouseQuery

D = [date(2024, 1, d) for d in (2, 3, 4, 5)]


def seed(warehouse, write_daily_bars, rows):
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": [r[0] for r in rows], "dt": [r[1] for r in rows],
        "open": [r[2] for r in rows], "high": [r[2] for r in rows],
        "low": [r[2] for r in rows], "close": [r[2] for r in rows],
        "volume": [1000.0] * len(rows), "amount": [0.0] * len(rows),
        "market_cap": [r[3] for r in rows],
        "circulating_market_cap": [r[4] for r in rows],
        "turnover_ratio": [r[5] for r in rows],
    }))


def values(warehouse):
    return WarehouseQuery(warehouse).valuation().sort(["symbol", "dt"])


def test_direct_market_caps_are_only_converted_from_yuan_to_yi(
    warehouse, write_daily_bars
):
    seed(warehouse, write_daily_bars, [
        ("600036.SH", D[0], 10.0, 100_000_000_000.0, 80_000_000_000.0, 1.25)
    ])
    assert build_valuation(warehouse, D[0], D[0]) == 1
    row = values(warehouse).row(0, named=True)
    assert row["market_cap"] == pytest.approx(1000.0)
    assert row["circulating_market_cap"] == pytest.approx(800.0)
    assert row["turnover_ratio"] == pytest.approx(1.25)
    assert row["close"] == pytest.approx(10.0)


def test_missing_direct_fields_are_not_reconstructed(warehouse, write_daily_bars):
    seed(warehouse, write_daily_bars, [
        ("600036.SH", D[0], 10.0, None, 80_000_000_000.0, 1.25)
    ])
    assert build_valuation(warehouse, D[0], D[0]) == 0
    assert values(warehouse).is_empty()


def test_rebuild_is_idempotent_and_partial_rebuild_keeps_other_days(
    warehouse, write_daily_bars
):
    seed(warehouse, write_daily_bars, [
        ("600036.SH", day, 10.0, 100_000_000_000.0,
         80_000_000_000.0, 1.0) for day in D
    ])
    assert build_valuation(warehouse, D[0], D[-1]) == 4
    assert build_valuation(warehouse, D[-1], D[-1]) == 1
    assert values(warehouse).height == 4


def test_rebuild_removes_stale_rows_inside_the_window(warehouse, write_daily_bars):
    seed(warehouse, write_daily_bars, [
        ("600036.SH", D[0], 10.0, 100_000_000_000.0,
         80_000_000_000.0, 1.0)
    ])
    build_valuation(warehouse, D[0], D[0])
    seed(warehouse, write_daily_bars, [
        ("600036.SH", D[0], 10.0, None, None, None)
    ])
    assert build_valuation(warehouse, D[0], D[0]) == 0
    assert values(warehouse).is_empty()


def test_empty_warehouse_yields_zero(warehouse):
    assert build_valuation(warehouse, D[0], D[-1]) == 0
