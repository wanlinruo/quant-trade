"""#538 用实际HistoricalFeed作参考，验证规范输入算术；不验证QMT映射。"""

import importlib.util
import math
from datetime import date, datetime
from pathlib import Path

import polars as pl
import pytest

from qsys.core.calendar import AShareCalendar
from qsys.data.feed import Adjustment, HistoricalFeed


DAYS = [date(2024, 1, day) for day in (2, 3, 4, 5)]
CALENDAR = AShareCalendar(DAYS)
AS_OFS = [datetime(2024, 1, 1, 15), datetime(2024, 1, 2, 14, 59, 59),
          datetime(2024, 1, 2, 15), datetime(2024, 1, 3, 9, 25, 10),
          datetime(2024, 1, 3, 15), datetime(2024, 1, 4, 14, 59, 59),
          datetime(2024, 1, 4, 15), datetime(2024, 1, 5, 15),
          datetime(2024, 1, 6, 12)]


def timestamp(moment):
    # A single explicit clock basis for this offline fixture, independent of TZ.
    return int((moment - datetime(1970, 1, 1)).total_seconds() * 1000)


@pytest.fixture(scope="module")
def core():
    path = Path(__file__).resolve().parents[2] / "docs/reference/qmt-inner-decision-core.py"
    spec = importlib.util.spec_from_file_location("qmt_inner_adjustment_core", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def columns():
    return {"open": [19.0, 19.5, 10.0, 10.1],
            "high": [20.0, 20.5, 10.5, 10.6],
            "low": [18.5, 19.0, 9.8, 9.9],
            "close": [19.5, 20.0, 10.0, 10.2],
            "volume": [1001.0, 1003.0, 2001.0, 2003.0],
            "amount": [19500.0, 20000.0, 20000.0, 20400.0]}


class MemoryInputs:
    """Only the reference feed's retrieval boundary is replaced, not its math."""

    def __init__(self, raw, price, share):
        self.bars = pl.DataFrame({"symbol": ["fixture.SH"] * 4, "dt": DAYS, **raw},
                                 schema_overrides={k: pl.Float64 for k in raw})
        self.factors = pl.DataFrame({
            "symbol": ["fixture.SH"] * 4, "dt": DAYS,
            "cum_factor": price, "cum_share_factor": share,
        }, schema_overrides={"cum_factor": pl.Float64, "cum_share_factor": pl.Float64})

    def daily_bars(self, symbols, start, end):
        return self.bars.filter(pl.col("symbol").is_in(symbols)
                                & pl.col("dt").is_between(start, end))

    def adjustment_factors(self, symbols, start, end):
        return self.factors.filter(pl.col("symbol").is_in(symbols)
                                   & pl.col("dt").is_between(start, end))


def reference(raw, price, share, as_of, end=DAYS[-1]):
    feed = HistoricalFeed(object(), CALENDAR, MemoryInputs(raw, price, share))
    result = feed.get(["fixture.SH"], DAYS[0], end, as_of=as_of,
                      adjustment=Adjustment.FORWARD)
    return {k: result[k].to_list() for k in raw}


def portable(core, raw, price, share, as_of):
    return core.forward_adjusted_columns(
        raw, [timestamp(CALENDAR.close_time(d)) for d in DAYS[:len(price)]],
        as_of=timestamp(as_of), cum_price_factors=price, cum_share_factors=share,
    )


@pytest.mark.parametrize("as_of", AS_OFS)
@pytest.mark.parametrize("price,share", [
    ([1.0] * 4, [1.0] * 4),  # Explicit observed factors in synthetic fixtures.
    ([1.0, 1.0, 1.1, 1.1], [1.0] * 4),  # Cash changes price, not shares.
    ([1.0, 1.0, 2.0, 2.0], [1.0, 1.0, 2.0, 2.0]),
    ([1.0, 1.03, 1.442, 1.54], [1.0, 1.0, 1.4, 1.5]),
    ([1.0, None, 2.0, 2.0], [1.0, 1.0, 2.0, 2.0]),
    ([1.0, 1.0, 2.0, None], [1.0, 1.0, 2.0, 2.0]),
    ([1.0, 1.0, 2.0, 2.0], [1.0, None, 2.0, 2.0]),
    ([1.0, 1.0, 2.0, 2.0], [1.0, 1.0, 2.0, None]),
    ([None] * 4, [None] * 4),
])
def test_same_canonical_inputs_match_actual_feed(core, as_of, price, share):
    raw = columns()
    actual = portable(core, raw, price, share, as_of)
    assert actual == reference(raw, price, share, as_of)
    visible = len(actual["amount"])
    assert actual["amount"] == raw["amount"][:visible]
    if all(v is not None for values in actual.values() for v in values):
        assert core.History(actual).height == visible
    else:
        with pytest.raises(ValueError, match="explicit finite"):
            core.History(actual)


def test_requested_end_before_visible_session_uses_returned_last_bar(core):
    raw = columns()
    price, share = [1.0, 1.03, 2.0, 2.0], [1.0, 1.0, 2.0, 2.0]
    actual = portable(core, {k: v[:2] for k, v in raw.items()}, price[:2], share[:2], AS_OFS[-1])
    assert actual == reference(raw, price, share, AS_OFS[-1], end=DAYS[1])
    assert actual["close"][-1] == raw["close"][1]


@pytest.mark.parametrize("field", ["close", "volume", "amount"])
def test_raw_missing_values_stay_null(core, field):
    raw = columns()
    raw[field][1] = None
    price, share = [1.0, 1.0, 1.1, 1.1], [1.0] * 4
    assert portable(core, raw, price, share, AS_OFS[-1]) == reference(
        raw, price, share, AS_OFS[-1],
    )


def test_cash_does_not_change_volume_and_input_is_not_mutated(core):
    raw = columns()
    prices = [1.0, 1.0, 1.1, 1.1]
    result = portable(core, raw, prices, [1.0] * 4, AS_OFS[-1])
    assert result["volume"] == raw["volume"]
    assert result["close"][1] != raw["close"][1]
    result["amount"][0] = -1
    assert raw["amount"][0] == 19500.0
    assert prices == [1.0, 1.0, 1.1, 1.1]


@pytest.mark.parametrize("volumes", [
    [0.5, 1.5, 2.5, 3.5],
    [math.nextafter(2.5, -math.inf), 2.5, math.nextafter(2.5, math.inf), 3.5],
    [1.0, 3.0, 5.0, 3.5],
])
def test_volume_ties_and_adjacent_values_match_locked_polars(core, volumes):
    raw = columns()
    raw["volume"] = volumes
    price, share = [1.0] * 4, [1.0, 1.0, 1.0, 1.5]
    assert portable(core, raw, price, share, AS_OFS[-1]) == reference(
        raw, price, share, AS_OFS[-1],
    )
    if volumes == [1.0, 3.0, 5.0, 3.5]:
        assert portable(core, raw, price, share, AS_OFS[-1])["volume"] == [2, 4, 8, 4]


def test_unseen_future_values_cannot_change_anchor_or_cause_fill(core):
    raw = columns()
    price, share = [1.0, 1.0, 2.0, 2.0], [1.0, 1.0, 2.0, 2.0]
    before = portable(core, raw, price, share, AS_OFS[3])
    for values in raw.values():
        values[2:] = [float("nan"), None]
    price[2:], share[2:] = [None, "future"], [float("inf"), 0]
    assert portable(core, raw, price, share, AS_OFS[3]) == before
    assert before["close"] == [19.5]


@pytest.mark.parametrize("kind", ["duplicate", "reversed", "timestamp_type", "shape", "column"])
def test_rejects_ambiguous_alignment_without_sorting_or_padding(core, kind):
    raw = columns()
    times = [timestamp(CALENDAR.close_time(d)) for d in DAYS]
    if kind == "duplicate":
        times[1] = times[0]
    elif kind == "reversed":
        times.reverse()
    elif kind == "timestamp_type":
        times[1] = True
    elif kind == "shape":
        raw["volume"].pop()
    else:
        del raw["amount"]
    with pytest.raises(ValueError):
        core.forward_adjusted_columns(raw, times, as_of=times[-1],
                                      cum_price_factors=[1.0] * 4,
                                      cum_share_factors=[1.0] * 4)


@pytest.mark.parametrize("invalid", [0, -1, True, "1", float("nan"), float("inf")])
@pytest.mark.parametrize("factor_kind", ["price", "share"])
def test_invalid_visible_factors_do_not_become_identity(core, invalid, factor_kind):
    price, share = [1.0] * 4, [1.0] * 4
    (price if factor_kind == "price" else share)[1] = invalid
    with pytest.raises(ValueError):
        portable(core, columns(), price, share, AS_OFS[-1])


def test_factor_lengths_must_align_even_when_no_bar_is_visible(core):
    with pytest.raises(ValueError, match="align"):
        portable(core, columns(), [], [], AS_OFS[0])


@pytest.mark.parametrize("invalid", [True, None, 1.5, "20240105"])
def test_as_of_cannot_be_implicitly_coerced(core, invalid):
    with pytest.raises(ValueError, match="as_of"):
        core.forward_adjusted_columns(columns(), [1, 2, 3, 4], as_of=invalid,
                                      cum_price_factors=[1.0] * 4,
                                      cum_share_factors=[1.0] * 4)


def test_nonrepresentable_calculation_is_reported(core):
    raw = columns()
    raw["volume"][0] = float(2 ** 63)
    with pytest.raises(ValueError, match="Int64"):
        portable(core, raw, [1.0] * 4, [1.0] * 4, AS_OFS[-1])
    with pytest.raises(ValueError, match="finite"):
        portable(core, columns(), [1e308, 1.0, 1.0, 1e-308], [1.0] * 4, AS_OFS[-1])
    with pytest.raises(ValueError, match="underflow"):
        portable(core, columns(), [1e-308, 1.0, 1.0, 1e308], [1.0] * 4, AS_OFS[-1])
