from datetime import date, datetime

from qsys.core.calendar import AShareCalendar
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Order, OrderType, Side
from qsys.core.types import Market, Symbol
from qsys.live.config import StreamConfig
from qsys.live.kill_switch import KillSwitch
from qsys.live.live_broker import LiveBroker
from qsys.live.messages import (
    AccountSnapshot,
    ControlCommand,
    FillEvent,
    OrderCommand,
    PositionSnapshot,
    RejectEvent,
)
from qsys.live.risk import RiskContext, default_ashare_gate
from qsys.live.transport import MemoryTransport

CMB = Symbol("600036", Market.SH)
CAL = AShareCalendar([date(2024, 1, 3)])
T = datetime(2024, 1, 3, 10, 0)
STREAMS = StreamConfig()


def order(side=Side.BUY, qty=100, oid="o1") -> Order:
    return Order(oid, CMB, side, qty, OrderType.MARKET, None, T)


def make(tmp_path, **kw):
    transport = MemoryTransport()
    ks = KillSwitch(transport, local_path=tmp_path / "K")
    gate = default_ashare_gate(ks, CAL, **kw)

    def factory() -> RiskContext:
        return RiskContext(portfolio=Portfolio(1_000_000.0), now=T,
                           reference_price=10.0, day_start_value=1_000_000.0)

    broker = LiveBroker(transport, STREAMS, risk_gate=gate, risk_context_factory=factory)
    return broker, transport, ks


def test_submit_appends_an_order_command(tmp_path):
    b, t, _ = make(tmp_path)
    b.submit(order())
    msgs = t.all_messages(STREAMS.orders)
    assert len(msgs) == 1
    cmd = OrderCommand.from_payload(msgs[0].payload)
    assert cmd.symbol == "600036.SH"
    assert cmd.qty == 100
    assert cmd.client_order_id == "o1"


def test_order_id_is_used_as_client_order_id(tmp_path):
    """client_order_id 由核心侧生成——幂等的基础。"""
    b, t, _ = make(tmp_path)
    b.submit(order(oid="abc-123"))
    cmd = OrderCommand.from_payload(t.all_messages(STREAMS.orders)[0].payload)
    assert cmd.client_order_id == "abc-123"


def test_risk_gate_blocks_the_order(tmp_path):
    b, t, ks = make(tmp_path)
    ks.engage("halt")
    b.submit(order())
    assert t.all_messages(STREAMS.orders) == []


def test_blocked_order_produces_a_reject(tmp_path):
    b, _, ks = make(tmp_path)
    ks.engage("halt")
    b.submit(order())
    rejects = b.poll_rejects()
    assert len(rejects) == 1
    assert "kill_switch" in rejects[0].reason


def test_pending_tracks_unfilled_orders(tmp_path):
    b, _, _ = make(tmp_path)
    b.submit(order())
    assert len(b.pending()) == 1


def test_poll_fills_reads_the_fill_stream(tmp_path):
    b, t, _ = make(tmp_path)
    b.submit(order())
    t.append(STREAMS.fills, FillEvent(
        client_order_id="o1", symbol="600036.SH", side="buy", qty=100,
        price=10.5, fee=5.01, filled_at=T, broker_order_id="B1",
    ).to_payload())
    fills = b.poll_fills()
    assert len(fills) == 1
    assert fills[0].qty == 100
    assert fills[0].price == 10.5
    assert fills[0].order_id == "o1"


def test_fill_clears_the_pending_order(tmp_path):
    b, t, _ = make(tmp_path)
    b.submit(order())
    t.append(STREAMS.fills, FillEvent(
        client_order_id="o1", symbol="600036.SH", side="buy", qty=100,
        price=10.5, fee=5.01, filled_at=T,
    ).to_payload())
    b.poll_fills()
    assert b.pending() == ()


def test_poll_fills_drains(tmp_path):
    b, t, _ = make(tmp_path)
    b.submit(order())
    t.append(STREAMS.fills, FillEvent(
        client_order_id="o1", symbol="600036.SH", side="buy", qty=100,
        price=10.5, fee=0.0, filled_at=T,
    ).to_payload())
    assert len(b.poll_fills()) == 1
    assert b.poll_fills() == ()


def test_poll_rejects_reads_the_reject_stream(tmp_path):
    b, t, _ = make(tmp_path)
    b.submit(order())
    t.append(STREAMS.rejects, RejectEvent(
        client_order_id="o1", symbol="600036.SH", reason="broker rejected", at=T,
    ).to_payload())
    rejects = b.poll_rejects()
    assert len(rejects) == 1
    assert rejects[0].reason == "broker rejected"


def test_on_market_data_is_a_no_op(tmp_path):
    """真实市场自己撮合——实盘 Broker 忽略行情推送。"""
    b, _, _ = make(tmp_path)
    b.on_market_data(None)
    assert b.poll_fills() == ()


def test_cancel_all_sends_a_control_command(tmp_path):
    b, t, _ = make(tmp_path)
    b.submit(order())
    b.cancel_all()
    controls = t.all_messages(STREAMS.control)
    assert len(controls) == 1
    assert ControlCommand.from_payload(controls[0].payload).action == "cancel_all"
    assert b.pending() == ()


def test_account_snapshot_reads_the_latest_state(tmp_path):
    b, t, _ = make(tmp_path)
    t.set_key(STREAMS.account, AccountSnapshot(
        at=T, cash=98_765.43,
        positions={"600036.SH": PositionSnapshot(total_qty=500, available_qty=500,
                                                 avg_cost=27.63)},
    ).to_payload()["data"])
    snap = b.account_snapshot()
    assert snap is not None
    assert snap.cash == 98_765.43
    assert snap.positions["600036.SH"].total_qty == 500


def test_account_snapshot_is_none_when_absent(tmp_path):
    b, _, _ = make(tmp_path)
    assert b.account_snapshot() is None


def test_unknown_fill_is_ignored_not_fatal(tmp_path):
    """回报里出现未知 client_order_id 时不应崩溃——记录告警即可。"""
    b, t, _ = make(tmp_path)
    t.append(STREAMS.fills, FillEvent(
        client_order_id="ghost", symbol="600036.SH", side="buy", qty=1,
        price=1.0, fee=0.0, filled_at=T,
    ).to_payload())
    fills = b.poll_fills()
    assert len(fills) == 1
