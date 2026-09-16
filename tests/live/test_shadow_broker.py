from datetime import date, datetime

from qsys.core.calendar import AShareCalendar
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Order, OrderType, Side
from qsys.core.types import Market, Symbol
from qsys.live.config import StreamConfig
from qsys.live.kill_switch import KillSwitch
from qsys.live.messages import OrderCommand
from qsys.live.risk import RiskContext, default_ashare_gate
from qsys.live.shadow_broker import ShadowBroker
from qsys.live.transport import MemoryTransport

CMB = Symbol("600036", Market.SH)
CAL = AShareCalendar([date(2024, 1, 3)])
T = datetime(2024, 1, 3, 10, 0)
STREAMS = StreamConfig()


def order(oid="o1", qty=100) -> Order:
    return Order(oid, CMB, Side.BUY, qty, OrderType.MARKET, None, T)


def make(tmp_path):
    transport = MemoryTransport()
    ks = KillSwitch(transport, local_path=tmp_path / "K")
    gate = default_ashare_gate(ks, CAL)

    def factory() -> RiskContext:
        return RiskContext(portfolio=Portfolio(1_000_000.0), now=T,
                           reference_price=10.0, day_start_value=1_000_000.0)

    return ShadowBroker(transport, STREAMS, risk_gate=gate,
                        risk_context_factory=factory), transport


def test_shadow_never_writes_to_the_order_stream(tmp_path):
    """影子模式最重要的性质：真钱一分不动。"""
    b, t = make(tmp_path)
    b.submit(order())
    assert t.all_messages(STREAMS.orders) == []


def test_shadow_records_the_intent(tmp_path):
    b, _ = make(tmp_path)
    b.submit(order())
    assert len(b.intents) == 1
    assert b.intents[0].order_id == "o1"


def test_shadow_journals_to_its_own_stream(tmp_path):
    b, t = make(tmp_path)
    b.submit(order())
    journaled = t.all_messages("shadow:intents")
    assert len(journaled) == 1
    assert OrderCommand.from_payload(journaled[0].payload).client_order_id == "o1"


def test_shadow_still_applies_the_risk_gate(tmp_path):
    """影子期也要验证风控是否会拦对东西。"""
    transport = MemoryTransport()
    ks = KillSwitch(transport, local_path=tmp_path / "K")
    ks.engage("halt")
    gate = default_ashare_gate(ks, CAL)

    def factory() -> RiskContext:
        return RiskContext(portfolio=Portfolio(1_000_000.0), now=T,
                           reference_price=10.0, day_start_value=1_000_000.0)

    b = ShadowBroker(transport, STREAMS, risk_gate=gate, risk_context_factory=factory)
    b.submit(order())
    assert b.intents == ()
    assert len(b.poll_rejects()) == 1


def test_shadow_never_reports_fills(tmp_path):
    b, _ = make(tmp_path)
    b.submit(order())
    assert b.poll_fills() == ()


def test_shadow_pending_reflects_recorded_intents(tmp_path):
    b, _ = make(tmp_path)
    b.submit(order("a"))
    b.submit(order("b"))
    assert len(b.pending()) == 2


def test_shadow_cancel_all_clears_pending(tmp_path):
    b, _ = make(tmp_path)
    b.submit(order())
    b.cancel_all()
    assert b.pending() == ()
    statuses = b.poll_statuses()
    assert len(statuses) == 1
    assert statuses[0].status == "cancelled"


def test_shadow_supports_restore_and_remaining(tmp_path):
    b, _ = make(tmp_path)
    b.restore(order(), 50)
    assert b.remaining("o1") == 50
    assert b.remaining("missing") == 0
    assert b.poll_statuses() == ()


def test_shadow_on_market_data_is_a_no_op(tmp_path):
    b, _ = make(tmp_path)
    b.on_market_data(None)
    assert b.poll_fills() == ()


def test_shadow_is_not_a_live_broker(tmp_path):
    """类型上就不可能把影子当实盘用。"""
    from qsys.live.live_broker import LiveBroker

    b, _ = make(tmp_path)
    assert not isinstance(b, LiveBroker)
