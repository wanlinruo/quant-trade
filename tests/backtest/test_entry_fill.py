"""竞价入场撮合口径：09:25 撮合后才下单时，委托只能进 09:30 连续竞价。"""

from datetime import datetime, time

import pytest

from qsys.backtest.entry_fill import EntryFill, EntryFillMode, MinuteBar, decide
from qsys.backtest.slippage import FixedRateSlippage, NoSlippage
from qsys.core.trading import Order, OrderType, Side
from qsys.core.types import Symbol

SYM = Symbol.parse("600001.SH")
AT = datetime(2024, 1, 2, 9, 30)


def _buy(limit=10.05):
    kind = OrderType.MARKET if limit is None else OrderType.LIMIT
    return Order("o1", SYM, Side.BUY, 1000, kind, limit, AT)


def test_open_mode_is_the_existing_open_plus_generic_slippage():
    got = decide(EntryFill(EntryFillMode.OPEN), _buy(), 20.0, None, FixedRateSlippage(0.0005))
    assert (got.price, got.outcome) == (pytest.approx(20.01), "open")


def test_slippage_mode_replaces_the_generic_slippage():
    rule = EntryFill(EntryFillMode.SLIPPAGE, slippage=0.005)
    got = decide(rule, _buy(), 10.0, None, FixedRateSlippage(0.0005))
    assert (got.price, got.outcome) == (pytest.approx(10.05), "slippage")


def test_continuous_fills_at_the_first_minute_open_when_it_is_under_the_limit():
    rule = EntryFill(EntryFillMode.CONTINUOUS, slippage=0.005)
    got = decide(rule, _buy(10.10), 10.0, MinuteBar(open=10.03, low=9.98), NoSlippage())
    assert (got.price, got.outcome) == (pytest.approx(10.03), "continuous")


def test_continuous_pays_the_limit_when_the_minute_opens_above_it_but_trades_down_to_it():
    rule = EntryFill(EntryFillMode.CONTINUOUS)
    got = decide(rule, _buy(10.05), 10.0, MinuteBar(open=10.20, low=10.01),
                 FixedRateSlippage(0.0005))
    assert got.price == pytest.approx(10.05)


def test_continuous_misses_when_the_minute_never_trades_at_the_limit():
    rule = EntryFill(EntryFillMode.CONTINUOUS)
    got = decide(rule, _buy(10.05), 10.0, MinuteBar(open=10.30, low=10.10), NoSlippage())
    assert got.price is None
    assert got.outcome == "missed"
    assert "09:31 low 10.10 > limit 10.05" in got.reason


def test_continuous_without_the_minute_bar_falls_back_and_says_so():
    rule = EntryFill(EntryFillMode.CONTINUOUS, slippage=0.005)
    got = decide(rule, _buy(10.03), 10.0, None, NoSlippage())
    # 退回开盘价上浮 0.5%，仍不超过限价
    assert (got.price, got.outcome) == (pytest.approx(10.03), "fallback_no_minute")


def test_continuous_market_order_has_no_limit_to_miss():
    got = decide(EntryFill(EntryFillMode.CONTINUOUS), _buy(None), 10.0,
                 MinuteBar(open=10.4, low=10.3), NoSlippage())
    assert got.price == pytest.approx(10.4)


def test_negative_entry_slippage_is_rejected():
    with pytest.raises(ValueError):
        EntryFill(EntryFillMode.SLIPPAGE, slippage=-0.01)
