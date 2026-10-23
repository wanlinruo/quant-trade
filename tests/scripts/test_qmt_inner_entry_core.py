"""#533 只验证同一已确认规范输入的入场算术，不模拟QMT字段映射。"""

import importlib.util
import itertools
from pathlib import Path

import polars as pl
import pytest

from qsys.strategy.structural import entry
from qsys.strategy.structural.selection import _stock_factors


@pytest.fixture(scope="module")
def core():
    path = Path(__file__).resolve().parents[2] / "docs/reference/qmt-inner-decision-core.py"
    spec = importlib.util.spec_from_file_location("qmt_inner_entry_core", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def low_columns(length):
    values = {"open": [1200.0] * length, "close": [1200.0] * length,
              "high": [1400.0] * length, "low": [950.0] * length,
              "volume": [1000.0] * length, "amount": [2e8] * length}
    if length:
        values["close"][-1] = 1000.0
    return values


def high_columns(length):
    values = {"open": [10.0] * length, "close": [10.0] * length,
              "high": [10.0] * length, "low": [9.5] * length,
              "volume": [5e5] * length, "amount": [5e8] * length}
    if length:
        for name, value in {"close": 11.0, "high": 11.5, "low": 10.5,
                            "volume": 1e6, "amount": 11e8}.items():
            values[name][-1] = value
    return values


@pytest.mark.parametrize("length", [0, 1, 2, 59, 60, 61, 101, 150])
def test_low_entry_matches_reference_at_window_and_price_boundaries(core, length):
    for price, amount in itertools.product(
        (None, 954.0, 955.0, 960.0, 970.0, 971.0), (1e8 - 1, 1e8, 2e8),
    ):
        columns = low_columns(length)
        if length:
            columns["amount"][-1] = amount
        portable, reference = core.History(columns), pl.DataFrame(columns)
        assert core.diagnose_low_open(portable, price) == entry.diagnose_low_open(reference, price)
        assert core.check_low_open(portable, price) is entry.check_low_open(reference, price)


def test_low_uses_last_60_and_relative_position_is_inclusive(core):
    columns = low_columns(101)
    columns["low"][:41] = [0.0] * 41
    columns["high"] = [1050.0] * 101
    for close in (999.0, 1000.0, 1001.0):
        columns["close"][-1] = close
        portable, reference = core.History(columns), pl.DataFrame(columns)
        actual = core.diagnose_low_open(portable, close * 0.96)
        assert actual == entry.diagnose_low_open(reference, close * 0.96)
        assert actual["eligible"] is (close <= 1000.0)


@pytest.mark.parametrize("length", [0, 1, 2, 101])
def test_high_entry_matches_reference_including_required_auction_volume(core, length):
    valuations = (None, {"market_cap": 69.9, "circulating_market_cap": 300.0},
                  {"market_cap": 70.0, "circulating_market_cap": 520.0},
                  {"market_cap": 100.0, "circulating_market_cap": 520.1})
    columns = high_columns(length)
    portable, reference = core.History(columns), pl.DataFrame(columns)
    for price, volume, valuation, required in itertools.product(
        (None, 0.0, 11.0, 11.01, 11.33, 11.66),
        (None, 0.0, 29999.0, 30000.0, 30001.0), valuations, (False, True),
    ):
        kwargs = dict(auction_price=price, auction_volume=volume,
                      valuation=valuation, require_auction_volume=required)
        assert core.diagnose_high_open(portable, **kwargs) == entry.diagnose_high_open(reference, **kwargs)
        assert core.check_high_open(portable, **kwargs) is entry.check_high_open(reference, **kwargs)


@pytest.mark.parametrize("amount", [5.5e8 - 1, 5.5e8, 11e8, 20e8, 20e8 + 1])
def test_high_money_and_vwap_use_existing_canonical_units(core, amount):
    columns = high_columns(2)
    columns["amount"][-1] = amount
    kwargs = dict(auction_price=11.33, auction_volume=30000.0,
                  valuation={"market_cap": 100.0, "circulating_market_cap": 300.0})
    actual = core.diagnose_high_open(core.History(columns), **kwargs)
    assert actual == entry.diagnose_high_open(pl.DataFrame(columns), **kwargs)
    assert actual["high_avg_inc"] == amount / (1e6 * 100) / 10.0 - 1
    # This fixture proves canonical arithmetic, not QMT's actual volume unit.


@pytest.mark.parametrize("marker", [-2, -3])
def test_left_pressure_starts_at_third_last_day(core, marker):
    columns = high_columns(10)
    columns["high"] = [10.0] * 10
    columns["high"][-1], columns["high"][marker] = 20.0, 30.0
    columns["volume"] = [9000.0] * 3 + [1000.0] * 6 + [1500.0]
    actual = core.check_left_pressure(core.History(columns))
    assert actual is entry.check_left_pressure(pl.DataFrame(columns))
    assert actual is (marker == -3)


@pytest.mark.parametrize("length", [0, 1, 2, 3, 4, 60, 101])
@pytest.mark.parametrize("last_volume", [0.0, 899.9, 900.0, 900.1, 2000.0])
def test_left_pressure_short_history_and_strict_volume_edge(core, length, last_volume):
    columns = high_columns(length)
    columns["volume"] = [1000.0] * length
    if length:
        columns["volume"][-1] = last_volume
    assert core.check_left_pressure(core.History(columns)) is entry.check_left_pressure(pl.DataFrame(columns))


@pytest.mark.parametrize("invalid", [None, float("nan"), float("inf"), -float("inf"), True, "10"])
def test_history_rejects_incomplete_or_nonfinite_input_without_filling(core, invalid):
    columns = high_columns(2)
    columns["close"][-1] = invalid
    with pytest.raises(ValueError, match="explicit finite"):
        core.History(columns)


def test_history_shape_and_aliasing_are_explicit(core):
    columns = low_columns(60)
    portable = core.History(columns)
    columns["close"][-1] = 9999
    assert portable["close"][-1] == 1000
    assert portable.tail(0).height == 0
    assert portable.tail(101).height == 60
    with pytest.raises(ValueError, match="nonnegative"):
        portable.tail(-1)
    with pytest.raises(ValueError, match="missing"):
        core.History({"close": [10.0]})
    columns["amount"] = []
    with pytest.raises(ValueError, match="unequal"):
        core.History(columns)


@pytest.mark.parametrize("length", [0, 1, 10, 11, 59, 60, 61, 101])
@pytest.mark.parametrize("zero", [False, True])
def test_low_stock_factors_preserve_10_and_60_day_windows(core, length, zero):
    columns = low_columns(length)
    if zero:
        columns["high"] = [0.0] * length
        columns["close"] = [0.0] * length
    assert core._stock_factors(core.History(columns)) == _stock_factors(pl.DataFrame(columns))


def test_stock_factors_ignore_highs_outside_the_last_60(core):
    columns = low_columns(101)
    columns["high"][0] = 99999.0
    columns["close"][-11] = 800.0
    actual = core._stock_factors(core.History(columns))
    assert actual == _stock_factors(pl.DataFrame(columns))
    assert actual["stock_return_10d"] == 0.25
    assert actual["distance_to_60d_high"] == 1000.0 / 1400.0 - 1
