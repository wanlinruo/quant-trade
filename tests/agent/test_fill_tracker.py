from datetime import datetime

from qsys.agent.fill_tracker import BrokerOrder, BrokerTrade, FillTracker
from qsys.backtest.fees import AShareFeeModel

T = datetime(2026, 9, 16, 10, 0)


def trade(tid, *, qty=100, price=10.0):
    return BrokerTrade(tid, "c-1", "B1", "600036.SH", "buy", price, qty, T)


def order(*, ordered=100, traded=100, status=56):
    return BrokerOrder("B1", "c-1", "600036.SH", "buy", ordered, traded, status)


def observe(trades, orders, seen=frozenset(), closed=frozenset()):
    return FillTracker(AShareFeeModel()).observe(
        trades, orders, seen_trade_ids=seen, closed_order_ids=closed, now=T,
    )


def test_real_trade_becomes_fill_with_broker_trade_id():
    out = observe([trade("T1")], [order()])
    assert out.fills[0].broker_trade_id == "T1"
    assert out.fills[0].price == 10.0


def test_seen_trade_is_not_published_again():
    assert observe([trade("T1")], [order()], frozenset({"T1"})).fills == ()


def test_minimum_commission_is_once_for_partial_fills():
    out = observe(
        [trade("T1"), trade("T2"), trade("T3")],
        [order(ordered=300, traded=300)],
    )
    assert round(sum(item.fee for item in out.fills), 2) == 5.03


def test_partial_cancel_rejects_only_remainder():
    out = observe([trade("T1", qty=300)], [order(ordered=1000, traded=300, status=52)])
    assert out.rejects[0].unfilled_qty == 700
    assert out.statuses[0].status == "cancelled"


def test_working_partial_order_is_not_closed():
    out = observe([trade("T1", qty=300)], [order(ordered=1000, traded=300, status=55)])
    assert out.rejects == ()
    assert out.closed_order_ids == ()
    assert out.statuses[0].status == "partially_filled"


def test_fully_filled_order_closes_without_reject():
    out = observe([trade("T1")], [order()])
    assert out.rejects == ()
    assert out.closed_order_ids == ("c-1",)
