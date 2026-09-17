from datetime import datetime, timedelta

import pytest

from qsys.core.trading import Side
from qsys.live.messages import MarketQuote
from qsys.live.pricing import QuoteError, protected_limit_price

T = datetime(2026, 9, 16, 10, 0)
# 当日直取涨跌停价（stk_limit）。QMT get_full_tick 不给，quote.upper_limit 恒为空（D-19）
LIMITS = (11.0, 9.0)


def quote(**changes):
    data = dict(
        symbol="600036.SH", at=T, price=10.0, open=10.0, high=10.1, low=9.9,
        prev_close=10.0, bid1=9.99, ask1=10.0, tick_size=0.01,
    )
    data.update(changes)
    return MarketQuote(**data)


def test_buy_uses_ask_with_protection():
    assert protected_limit_price(quote(), Side.BUY, limits=LIMITS, now=T) == 10.02


def test_sell_uses_bid_with_protection():
    assert protected_limit_price(quote(), Side.SELL, limits=LIMITS, now=T) == 9.98


def test_stale_quote_is_rejected():
    with pytest.raises(QuoteError, match="STALE_QUOTE"):
        protected_limit_price(quote(), Side.BUY, limits=LIMITS, now=T + timedelta(seconds=4))


def test_missing_opponent_is_rejected():
    with pytest.raises(QuoteError, match="NO_OPPONENT"):
        protected_limit_price(quote(ask1=0), Side.BUY, limits=LIMITS, now=T)


def test_limit_up_buy_is_rejected():
    with pytest.raises(QuoteError, match="LIMIT_UP"):
        protected_limit_price(quote(ask1=11.0), Side.BUY, limits=LIMITS, now=T)


def test_the_quote_limit_fields_are_not_trusted():
    """即便 quote 带了 upper_limit，也只认传入的直取价。"""
    with pytest.raises(QuoteError, match="LIMIT_UP"):
        protected_limit_price(quote(ask1=10.5, upper_limit=99.0), Side.BUY,
                              limits=(10.5, 9.5), now=T)


def test_buy_price_is_capped_at_the_direct_limit_up():
    assert protected_limit_price(quote(ask1=10.49), Side.BUY, limits=(10.5, 9.5), now=T) == 10.5


def test_buy_without_a_direct_limit_price_is_refused():
    with pytest.raises(QuoteError, match="NO_PRICE_LIMIT"):
        protected_limit_price(quote(), Side.BUY, limits=None, now=T)


def test_sell_without_a_direct_limit_price_is_priced_without_a_floor():
    assert protected_limit_price(quote(), Side.SELL, limits=None, now=T) == 9.98
