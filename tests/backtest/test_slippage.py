import pytest

from qsys.backtest.slippage import FixedRateSlippage, FixedTickSlippage, NoSlippage
from qsys.core.trading import Side


def test_no_slippage_returns_price_unchanged():
    assert NoSlippage().adjust(Side.BUY, 10.0) == 10.0
    assert NoSlippage().adjust(Side.SELL, 10.0) == 10.0


def test_fixed_rate_buy_pays_more():
    assert FixedRateSlippage(0.001).adjust(Side.BUY, 10.0) == 10.01


def test_fixed_rate_sell_receives_less():
    assert FixedRateSlippage(0.001).adjust(Side.SELL, 10.0) == 9.99


def test_fixed_rate_is_rounded_to_cents():
    assert FixedRateSlippage(0.0003).adjust(Side.BUY, 27.58) == 27.59


def test_fixed_tick_buy_pays_more():
    assert FixedTickSlippage(2).adjust(Side.BUY, 10.0) == 10.02


def test_fixed_tick_sell_receives_less():
    assert FixedTickSlippage(2).adjust(Side.SELL, 10.0) == 9.98


def test_tick_size_is_configurable():
    assert FixedTickSlippage(1, tick_size=0.001).adjust(Side.BUY, 10.0) == 10.0


def test_slippage_never_pushes_price_below_zero():
    assert FixedRateSlippage(2.0).adjust(Side.SELL, 10.0) == 0.01


def test_negative_rate_rejected():
    with pytest.raises(ValueError, match="rate must be >= 0"):
        FixedRateSlippage(-0.001)


def test_negative_ticks_rejected():
    with pytest.raises(ValueError, match="ticks must be >= 0"):
        FixedTickSlippage(-1)
