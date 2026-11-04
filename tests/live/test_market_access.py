"""#589：穿过真实订单入口验证拒单；任何报价/券商下单探针均不得被调用。"""
from datetime import datetime, timedelta
from types import SimpleNamespace
import sys

import pytest

from qsys.agent.adapter import FakeAdapter
from qsys.agent.execution_agent import ExecutionAgent
from qsys.agent.xtquant_adapter import XtQuantAdapter
from qsys.agent.report_ledger import MemoryReportLedger
from qsys.backtest.fees import ZeroFeeModel
from qsys.backtest.slippage import NoSlippage
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Order, OrderType, Side
from qsys.core.types import Symbol
from qsys.live.config import StreamConfig
from qsys.live.live_broker import LiveBroker
from qsys.live.idempotency import IdempotencyGuard
from qsys.live.messages import ControlCommand, MarketQuote, OrderCommand, RejectEvent
from qsys.live.risk import RiskGate
from qsys.live.safety import check_order_market
from qsys.live.shadow_broker import ShadowBroker
from qsys.live.transport import MemoryTransport
from qsys.markets.ashare import ashare_pipeline
from qsys.paper.broker import PaperBroker

NOW = datetime(2026, 11, 3, 10)
STREAMS = StreamConfig()


def forbidden(*args, **kwargs):
    pytest.fail("denied opening order reached quote/risk/broker side effects")


@pytest.mark.parametrize("symbol", [
    "430001.BJ", "830001.BJ", "920026.BJ", "920026.SH", "920026.SZ",
    "600036", "600036.sh", "600036.XX", "T600018.SH", " 600036.SH",
])
def test_unknown_or_bse_opening_identity_is_rejected(symbol):
    assert not check_order_market(symbol, "buy").allowed


@pytest.mark.parametrize("symbol", ["000920.SZ", "600036.SH", "300001.SZ"])
def test_normal_shenzhen_920_digits_are_not_a_beijing_identity(symbol):
    assert check_order_market(symbol, "buy").allowed


@pytest.mark.parametrize("symbol", ["430001.BJ", "830001.BJ", "920026.BJ"])
def test_market_policy_preserves_valid_reducing_orders(symbol):
    assert check_order_market(symbol, "sell").allowed


@pytest.mark.parametrize("broker_type", [LiveBroker, ShadowBroker])
@pytest.mark.parametrize("symbol", ["430001.BJ", "920026.BJ", "920026.SZ"])
def test_core_refuses_before_risk_quote_context_or_stream_write(broker_type, symbol):
    transport = MemoryTransport()
    broker = broker_type(transport, STREAMS, risk_gate=RiskGate([]),
                         risk_context_factory=forbidden)
    broker.submit(Order("denied", Symbol.parse(symbol), Side.BUY, 100,
                        OrderType.MARKET, None, NOW))
    assert transport.all_messages(STREAMS.orders) == []
    assert transport.all_messages("shadow:intents") == []
    rejects = broker.poll_rejects()
    assert len(rejects) == 1 and "market_access" in rejects[0].reason


@pytest.mark.parametrize("symbol", ["920026.BJ", "920026.SZ", "600036.XX"])
@pytest.mark.parametrize("strategy_id", ["structural", "commissioning"])
def test_agent_refuses_before_idempotency_lookup_or_broker_call(symbol, strategy_id):
    transport, adapter = MemoryTransport(), FakeAdapter()
    adapter.find_order = forbidden
    adapter.place_order = forbidden
    agent = ExecutionAgent(transport, adapter, STREAMS)
    command = OrderCommand(client_order_id="bad", symbol=symbol, side="buy", qty=100,
                           order_type="market", created_at=NOW, strategy_id=strategy_id)
    transport.append(STREAMS.orders, command.to_payload())
    transport.append(STREAMS.control, ControlCommand(action="cancel_all", at=NOW).to_payload())
    agent.run_once()
    rejects = transport.all_messages(STREAMS.rejects)
    assert len(rejects) == 1
    assert "market_access" in RejectEvent.from_payload(rejects[0].payload).reason
    assert adapter.cancelled is True
    assert adapter.placed == []


def test_direct_adapter_refuses_even_without_sdk_or_connected_trader():
    adapter = XtQuantAdapter.__new__(XtQuantAdapter)
    with pytest.raises(ValueError, match="market_access"):
        adapter.place_order("920026.BJ", "buy", 100, "limit", 10)


@pytest.mark.parametrize("known_by", ["redis", "local_order", "closed_order"])
def test_agent_does_not_reclassify_accepted_history_as_rejected(known_by):
    transport, adapter, ledger = MemoryTransport(), FakeAdapter(), MemoryReportLedger()
    guard = IdempotencyGuard(transport)
    if known_by == "redis":
        guard.remember("historical", "broker-original")
    elif known_by == "local_order":
        ledger.remember_order("historical", "broker-original")
    else:
        ledger.remember_closed("historical")
    adapter.find_order = forbidden
    adapter.place_order = forbidden
    agent = ExecutionAgent(transport, adapter, STREAMS, guard=guard, ledger=ledger)
    command = OrderCommand(client_order_id="historical", symbol="920026.BJ", side="buy",
                           qty=100, order_type="market", created_at=NOW)
    transport.append(STREAMS.orders, command.to_payload())
    agent.run_once()
    assert transport.all_messages(STREAMS.rejects) == []
    assert adapter.placed == []
    assert (ledger.broker_order_id("historical") == "broker-original") == (known_by == "local_order")


def test_live_retains_restored_broker_pending_order_without_new_command_or_reject():
    transport = MemoryTransport()
    broker = LiveBroker(transport, STREAMS, risk_gate=RiskGate([]), risk_context_factory=forbidden)
    order = Order("historical", Symbol.parse("920026.BJ"), Side.BUY, 1000,
                  OrderType.MARKET, None, NOW)
    broker.restore(order, 400)
    broker.submit(order)
    assert broker.remaining(order.order_id) == 400
    assert broker.poll_rejects() == ()
    assert transport.all_messages(STREAMS.orders) == []


def test_paper_rejects_only_unfilled_remainder_of_restored_order():
    portfolio = Portfolio(10000)
    broker = PaperBroker(portfolio, ashare_pipeline(), ZeroFeeModel(), NoSlippage())
    order = Order("historical", Symbol.parse("920026.BJ"), Side.BUY, 1000,
                  OrderType.MARKET, None, NOW)
    broker.restore(order, 400)
    broker.submit(order)
    assert broker.poll_rejects()[0].qty == 400
    assert broker.pending() == ()
    assert portfolio.cash == 10000


def test_adapter_keeps_legacy_bse_sell_path(monkeypatch):
    monkeypatch.setitem(sys.modules, "xtquant", SimpleNamespace(
        xtconstant=SimpleNamespace(FIX_PRICE=11, LATEST_PRICE=5)))
    calls = []
    adapter = XtQuantAdapter.__new__(XtQuantAdapter)
    adapter._account = object()
    adapter._trader = SimpleNamespace(order_stock=lambda *args: calls.append(args) or 123)
    assert adapter.place_order("920026.BJ", "sell", 7, "limit", 10) == "123"
    assert calls[0][1:4] == ("920026.BJ", 24, 7)


@pytest.mark.parametrize("restored", [False, True])
def test_paper_cannot_fill_a_direct_or_restored_bse_buy(restored):
    portfolio = Portfolio(10000)
    broker = PaperBroker(portfolio, ashare_pipeline(), ZeroFeeModel(), NoSlippage())
    order = Order("bad", Symbol.parse("920026.BJ"), Side.BUY, 100,
                  OrderType.MARKET, None, NOW)
    if restored:
        broker.restore(order, 100)
        broker.on_quote(MarketQuote(
            symbol="920026.BJ", at=NOW + timedelta(seconds=1), price=10,
            open=10, high=10, low=10, prev_close=9, volume=10000, amount=100000,
        ), limits=(12, 8), board="bse")
    else:
        broker.submit(order)
    assert broker.pending() == ()
    assert broker.poll_fills() == ()
    assert portfolio.cash == 10000
    rejects = broker.poll_rejects()
    assert len(rejects) == 1 and "market_access" in rejects[0].reason
