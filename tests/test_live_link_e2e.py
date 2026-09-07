"""实盘链路端到端：假 Transport + 假券商，跑一个完整交易日。

验证核心侧与执行代理之间的完整往返，以及故障处理矩阵中的关键分支。
"""

from datetime import date, datetime, timedelta

from qsys.agent.adapter import FakeAdapter
from qsys.agent.execution_agent import ExecutionAgent
from qsys.core.calendar import AShareCalendar
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, Order, OrderType, Side
from qsys.core.types import Market, Symbol
from qsys.live.config import StreamConfig
from qsys.live.heartbeat import HeartbeatMonitor
from qsys.live.kill_switch import KillSwitch
from qsys.live.live_broker import LiveBroker
from qsys.live.messages import AccountSnapshot
from qsys.live.reconcile import reconcile_positions
from qsys.live.risk import RiskContext, default_ashare_gate
from qsys.live.shadow_broker import ShadowBroker
from qsys.live.transport import MemoryTransport

CMB = Symbol("600036", Market.SH)
CAL = AShareCalendar([date(2024, 1, 3)])
T = datetime(2024, 1, 3, 10, 0)
STREAMS = StreamConfig()


def build(tmp_path, *, shadow: bool = False, adapter=None):
    transport = MemoryTransport()
    portfolio = Portfolio(1_000_000.0)
    ks = KillSwitch(transport, local_path=tmp_path / "KILL")
    gate = default_ashare_gate(ks, CAL)

    def factory() -> RiskContext:
        return RiskContext(portfolio=portfolio, now=T, reference_price=10.0,
                           day_start_value=1_000_000.0)

    cls = ShadowBroker if shadow else LiveBroker
    broker = cls(transport, STREAMS, risk_gate=gate, risk_context_factory=factory)
    agent = ExecutionAgent(transport, adapter or FakeAdapter(cash=1_000_000.0), STREAMS)
    return broker, agent, transport, portfolio, ks


def order(oid="o1", qty=100) -> Order:
    return Order(oid, CMB, Side.BUY, qty, OrderType.MARKET, None, T)


def test_full_round_trip_core_to_agent_to_core(tmp_path):
    broker, agent, _, portfolio, _ = build(tmp_path)

    broker.submit(order())
    assert len(broker.pending()) == 1

    agent.run_once()

    fills = broker.poll_fills()
    assert len(fills) == 1
    assert fills[0].order_id == "o1"
    assert broker.pending() == ()

    portfolio.apply_fill(fills[0])
    assert portfolio.position(CMB).total_qty == 100


def test_duplicate_delivery_does_not_double_fill(tmp_path):
    """网络重传：同一指令投递两次，只能成交一次。"""
    broker, agent, transport, _, _ = build(tmp_path)
    broker.submit(order())
    original = transport.all_messages(STREAMS.orders)[0]
    transport.append(STREAMS.orders, original.payload)

    agent.run_once()
    assert len(broker.poll_fills()) == 1


def test_kill_switch_stops_the_link(tmp_path):
    broker, agent, _, _, ks = build(tmp_path)
    ks.engage("emergency")
    broker.submit(order())
    agent.run_once()
    assert broker.poll_fills() == ()
    assert len(broker.poll_rejects()) == 1


def test_kill_switch_via_local_file_alone(tmp_path):
    """不依赖 Redis 的独立停机路径。"""
    broker, agent, _, _, _ = build(tmp_path)
    (tmp_path / "KILL").write_text("filesystem stop")
    broker.submit(order())
    agent.run_once()
    assert broker.poll_fills() == ()


def test_shadow_mode_never_reaches_the_agent(tmp_path):
    """影子模式：策略与风控全跑，真钱一分不动。"""
    broker, agent, _, _, _ = build(tmp_path, shadow=True)
    broker.submit(order())
    assert agent.run_once() == 0
    assert len(broker.intents) == 1


def test_account_snapshot_flows_back_for_reconciliation(tmp_path):
    broker, agent, _, portfolio, _ = build(tmp_path)
    broker.submit(order())
    agent.run_once()
    for f in broker.poll_fills():
        portfolio.apply_fill(f)

    agent.publish_account()
    snapshot = broker.account_snapshot()
    assert snapshot is not None

    result = reconcile_positions(portfolio, snapshot, cash_tolerance=1e9)
    # FakeAdapter 不跟踪持仓，故本地有仓、远端无仓 -> 必须被发现
    assert result.is_consistent is False
    assert result.mismatches[0].local_qty == 100


def test_position_mismatch_is_detected(tmp_path):
    _, _, _, portfolio, _ = build(tmp_path)
    portfolio.apply_fill(Fill("x", CMB, Side.BUY, 500, 10.0, 0.0, T))
    remote = AccountSnapshot(at=T, cash=portfolio.cash, positions={})
    assert reconcile_positions(portfolio, remote).is_consistent is False


def test_heartbeat_detects_a_silent_agent(tmp_path):
    _, agent, transport, _, _ = build(tmp_path)
    monitor = HeartbeatMonitor(transport)
    agent.beat()
    assert monitor.is_alive("agent", max_age_seconds=60) is True

    future = datetime.now() + timedelta(seconds=300)
    assert monitor.is_alive("agent", max_age_seconds=60, now=future) is False


def test_agent_failure_produces_a_reject_not_a_hang(tmp_path):
    broker, agent, _, _, _ = build(tmp_path, adapter=FakeAdapter(fail_with="broker offline"))
    broker.submit(order())
    agent.run_once()
    rejects = broker.poll_rejects()
    assert len(rejects) == 1
    assert "broker offline" in rejects[0].reason
