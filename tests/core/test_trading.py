from datetime import datetime

import pytest

from qsys.core.trading import Fill, Order, OrderIntent, OrderType, Side, to_cents
from qsys.core.types import Market, Symbol

CMB = Symbol("600036", Market.SH)
NOW = datetime(2024, 1, 2, 15, 0)


def test_to_cents_rounds_half_up():
    assert to_cents(1.005) == 1.01
    assert to_cents(1.004) == 1.00


def test_to_cents_rounds_negative_away_from_zero():
    assert to_cents(-1.005) == -1.01


def test_to_cents_is_idempotent():
    assert to_cents(to_cents(10.126)) == 10.13


def test_to_cents_handles_large_amounts():
    assert to_cents(1_234_567.891) == 1_234_567.89


def test_order_intent_rejects_non_positive_qty():
    with pytest.raises(ValueError, match="qty must be positive"):
        OrderIntent(CMB, Side.BUY, 0)


def test_limit_order_requires_limit_price():
    with pytest.raises(ValueError, match="limit order requires limit_price"):
        OrderIntent(CMB, Side.BUY, 100, OrderType.LIMIT)


def test_market_order_needs_no_limit_price():
    intent = OrderIntent(CMB, Side.BUY, 100)
    assert intent.order_type is OrderType.MARKET
    assert intent.limit_price is None


def test_with_qty_returns_new_intent():
    intent = OrderIntent(CMB, Side.BUY, 150)
    clamped = intent.with_qty(100)
    assert clamped.qty == 100
    assert intent.qty == 150
    assert clamped.symbol == intent.symbol
    assert clamped.side is intent.side


def test_intent_is_frozen():
    intent = OrderIntent(CMB, Side.BUY, 100)
    with pytest.raises(Exception):
        intent.qty = 200


def test_fill_gross_is_price_times_qty_in_cents():
    fill = Fill("o1", CMB, Side.BUY, 100, 27.583, 5.0, NOW)
    assert fill.gross == 2758.30


def test_buy_cash_delta_is_negative_and_includes_fee():
    fill = Fill("o1", CMB, Side.BUY, 100, 27.58, 5.0, NOW)
    assert fill.cash_delta == -2763.00


def test_sell_cash_delta_is_positive_and_net_of_fee():
    fill = Fill("o1", CMB, Side.SELL, 100, 27.58, 5.0, NOW)
    assert fill.cash_delta == 2753.00


def test_order_carries_creation_time():
    order = Order("o1", CMB, Side.BUY, 100, OrderType.MARKET, None, NOW)
    assert order.created_at == NOW
    assert order.order_id == "o1"
