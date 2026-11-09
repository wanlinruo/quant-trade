"""Linux LiveBroker → Redis → ExecutionAgent → QmtIpcPort → 执行器 → 回报 → Core（#601）。"""
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from qsys.agent.qmt import protocol
from qsys.agent.qmt.memory import SharedMemory
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Order, OrderType, Side
from qsys.core.types import Market, Symbol
from qsys.live.config import DeployConfig, SafetyConfig
from qsys.live.live_broker import LiveBroker
from qsys.live.messages import DispatchStatusEvent
from qsys.live.risk import RiskContext, RiskGate
from qsys.live.transport import MemoryTransport
from qmt_fake import ACCOUNT, FakeQmtApi, load_executor

ROOT = Path(__file__).resolve().parents[3]
RUN = 'a' * 32
KEY = bytes(range(32))
T = datetime(2026, 11, 9, 9, 25, 10)


def cli():
    spec = importlib.util.spec_from_file_location('run_qmt_agent', ROOT / 'scripts' / 'run_qmt_agent.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class World:
    def __init__(self, tmp_path):
        self.now = T + timedelta(seconds=1)
        self.ms = lambda: int(self.now.timestamp() * 1000)
        self.buf = bytearray(protocol.MAP_SIZE)
        self.mem = SharedMemory(self.buf)
        self.api = FakeQmtApi()
        self.api.accounts.append(SimpleNamespace(m_strAccountID=ACCOUNT, m_dAvailable=10_000.0,
                                                 m_dBalance=10_000.0, m_dStockValue=0.0))
        self.ex = load_executor(ACCOUNT_ID=ACCOUNT).Executor(RUN, KEY, self.api.as_api(), self.buf,
                                                            self.ms)
        self.transport = MemoryTransport()
        self.cfg = DeployConfig(safety=SafetyConfig(mode='live', orders_enabled=True,
                                                    protected_account_ids=(ACCOUNT,),
                                                    local_kill_path=str(tmp_path / 'AGENT_KILL'),
                                                    dispatch_tracking=True))
        self.root = tmp_path / 'qsys-qmt-dispatch-e2e'
        self.root.mkdir(mode=0o700)
        self.scope = cli().account_scope(ACCOUNT, validation=False)
        (self.root / '.qsys-qmt-dispatch').write_text(self.scope)
        self.ledger = tmp_path / 'ledger.sqlite3'
        self.portfolio = Portfolio(10_000.0)
        self.broker = LiveBroker(
            self.transport, self.cfg.streams, strategy_id='structural', command_ttl_seconds=60,
            risk_gate=RiskGate([]), receive_dispatch_status=True,
            risk_context_factory=lambda: RiskContext(portfolio=self.portfolio, now=T,
                                                     reference_price=4.0, day_start_value=10_000.0))

    def sleep(self, _):
        self.mem.beat(self.ms())
        self.ex.tick(None)

    def assemble(self):
        return cli().assemble(self.cfg, self.transport, self.mem, run_id=RUN, key=KEY,
                              account=ACCOUNT, state_root=self.root, ledger=self.ledger,
                              strategy_id='structural', sleep=self.sleep,
                              clock=lambda: self.now, clock_ms=self.ms)


def order(oid='q261109001abcdef012345'):
    return Order(oid, Symbol('510300', Market.SH), Side.BUY, 100, OrderType.LIMIT, 4.0, T)


def test_signal_reaches_qmt_and_fill_returns_to_core(tmp_path, monkeypatch):
    w = World(tmp_path)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return w.now
    monkeypatch.setattr('qsys.agent.execution_agent.datetime', Clock)
    w.broker.submit(order())
    with w.assemble() as (agent, adapter):
        assert agent.run_once() == 1
        placed = [c for c in w.api.calls if c[0] == 'passorder']
        assert [(c[1], c[4], c[5], c[6], c[7], c[10]) for c in placed] == [
            (23, '510300.SH', 11, 4.0, 100, 'q261109001abcdef012345')]
        # broker reports the fill through the native callbacks
        w.api.orders[0].m_nOrderStatus, w.api.orders[0].m_nVolumeTraded = 56, 100
        w.ex.record_order('callback', w.api.orders[0])
        w.ex.record_deal('callback', SimpleNamespace(
            m_strRemark='q261109001abcdef012345', m_strTradeID='T1', m_strOrderSysID='S1',
            m_dPrice=4.0, m_nVolume=100, m_nOffsetFlag=48, m_strInstrumentID='510300',
            m_strExchangeID='SH', m_strTradeDate='20261109', m_strTradeTime='092530',
            m_strAccountID=ACCOUNT))
        agent.poll_reports()
    statuses = [DispatchStatusEvent.from_payload(m.payload).state
                for m in w.transport.all_messages(w.cfg.streams.dispatch_status)]
    assert statuses == ['dispatching', 'transport_received']
    fills = w.broker.poll_fills()
    assert [(f.qty, f.price) for f in fills] == [(100, 4.0)]
    w.broker.poll_statuses()
    assert w.broker.pending() == ()


def test_star_buy_rejected_back_to_core_without_native_call(tmp_path, monkeypatch):
    w = World(tmp_path)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return w.now
    monkeypatch.setattr('qsys.agent.execution_agent.datetime', Clock)
    w.broker.submit(Order('q261109002abcdef012345', Symbol('688001', Market.SH), Side.BUY, 100,
                          OrderType.LIMIT, 40.0, T))
    with w.assemble() as (agent, adapter):
        agent.run_once()
    assert [c for c in w.api.calls if c[0] == 'passorder'] == []
    rejects = w.broker.poll_rejects()
    assert [r.reason for r in rejects] == ['executor:market_blocked']
