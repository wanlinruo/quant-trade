from datetime import datetime, timedelta

import pytest

from qsys.core.trading import Side
from qsys.live.messages import MarketQuote
from qsys.live.pricing import QuoteError, protected_limit_price

T = datetime(2026, 9, 16, 10, 0)


def quote(**changes):
    data = dict(
        symbol="600036.SH", at=T, price=10.0, open=10.0, high=10.1, low=9.9,
        prev_close=10.0, bid1=9.99, ask1=10.0, upper_limit=11.0,
        lower_limit=9.0, tick_size=0.01,
    )
    data.update(changes)
    return MarketQuote(**data)


def test_buy_uses_ask_with_protection():
    assert protected_limit_price(quote(), Side.BUY, now=T) == 10.02


def test_sell_uses_bid_with_protection():
    assert protected_limit_price(quote(), Side.SELL, now=T) == 9.98


def test_stale_quote_is_rejected():
    with pytest.raises(QuoteError, match="STALE_QUOTE"):
        protected_limit_price(quote(), Side.BUY, now=T + timedelta(seconds=4))


def test_missing_opponent_is_rejected():
    with pytest.raises(QuoteError, match="NO_OPPONENT"):
        protected_limit_price(quote(ask1=0), Side.BUY, now=T)


def test_limit_up_buy_is_rejected():
    with pytest.raises(QuoteError, match="LIMIT_UP"):
        protected_limit_price(quote(ask1=11.0), Side.BUY, now=T)
