"""隔离传输+真实临时SQLite，验证异步发送不会伪造终态或释放预留。"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from qsys.agent.adapter import FakeAdapter
from qsys.agent.async_dispatch import open_validation_dispatcher
from qsys.agent.execution_agent import ExecutionAgent
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Order, OrderType, Side
from qsys.core.types import Symbol
from qsys.live.config import StreamConfig
from qsys.live.live_broker import LiveBroker
from qsys.live.messages import AccountSnapshot, DispatchStatusEvent, OrderStatusEvent
from qsys.live.risk import RiskContext, RiskGate
from qsys.live.runtime import LiveRuntime
from qsys.live.store import LiveStore
from qsys.live.transport import MemoryTransport
from qsys.live.validation_transport import ValidationTransport

T = datetime(2026, 11, 5, 9, 25, 10)
S = StreamConfig()
NS = 'component-validation:async-flow'


class TimeoutPort:
    def __init__(self):
        self.calls = 0

    def send(self, envelope):
        self.calls += 1
        raise TimeoutError('diagnostic only')

    def close(self):
        pass


def make_broker(t, portfolio, *, enabled=True):
    return LiveBroker(t, S, strategy_id='s6', command_ttl_seconds=60,
        risk_gate=RiskGate([]), risk_context_factory=lambda: RiskContext(
            portfolio=portfolio, now=T, reference_price=10.0, day_start_value=100000.0),
        receive_dispatch_status=enabled)


def runtime(broker, store):
    obj = LiveRuntime.__new__(LiveRuntime)
    obj.broker, obj.store = broker, store
    obj.portfolio, obj.state, obj.last_prices, _ = store.load()
    obj._event_fills, obj._today_fills, obj._event_rejects = [], [], []
    obj.journal = SimpleNamespace(record_fills=lambda *a: None, record_rejects=lambda *a: None)
    return obj


@pytest.fixture
def flow(tmp_path, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return T + timedelta(seconds=1)
    monkeypatch.setattr('qsys.agent.execution_agent.datetime', Clock)
    t = ValidationTransport(MemoryTransport(), NS)
    root = tmp_path / 'qsys-async-validation-flow'
    root.mkdir(mode=0o700)
    (root / '.qsys-async-validation').write_text(NS)
    store = LiveStore(tmp_path / 'live.sqlite3')
    store.initialize(AccountSnapshot(at=T, cash=100000.0, total_asset=100000.0, positions={}))
    portfolio, _, _, _ = store.load()
    broker = make_broker(t, portfolio)
    order = Order('flow-1', Symbol.parse('600036.SH'), Side.BUY, 100, OrderType.LIMIT, 10.0, T)
    broker.submit(order)
    store.record_orders('fixture', [order])
    port = TimeoutPort()
    with open_validation_dispatcher(enabled=True, root=root, transport=t, streams=S,
            account_scope='validation:account', strategy_id='s6', session_id=T.date().isoformat(),
            adapter_factory=lambda: port, clock=lambda: T + timedelta(seconds=1)) as dispatcher:
        agent = ExecutionAgent(t, FakeAdapter(), S, async_dispatcher=dispatcher)
        agent.run_once()
        yield t, broker, store, port, order
    store.close()


def test_unknown_is_committed_without_releasing_pending_and_cold_restore_keeps_reservation(flow):
    t, broker, store, port, order = flow
    reserved = broker._pending[order.order_id].reserved_cash
    assert reserved > 0
    assert runtime(broker, store)._record_results() == 0
    assert t.pending(S.dispatch_status, 'core') == []
    assert broker.remaining(order.order_id) == 100
    assert broker._pending[order.order_id].reserved_cash == reserved
    assert broker._pending[order.order_id].broker_order_id is None
    assert 'dispatch:unknown' in store.order_transitions(order.order_id)
    assert len(store.pending_orders()) == 1
    restored = make_broker(t, Portfolio(100000.0))
    cold = LiveStore(store.path)
    try:
        for pending, remaining in cold.pending_orders():
            restored.restore(pending, remaining)
        assert restored.remaining(order.order_id) == 100
        assert restored._pending[order.order_id].reserved_cash > 0
    finally:
        cold.close()
    assert port.calls == 1
    assert t.inner.all_messages(NS + ':' + S.rejects) == []


def test_failed_core_commit_redelivers_dispatch_evidence_without_ack(flow, monkeypatch):
    t, broker, store, _, order = flow
    obj = runtime(broker, store)
    commit = store.commit_reports
    calls = [0]

    def fail_once(*args, **kwargs):
        calls[0] += 1
        if calls[0] == 1:
            raise OSError('fixture commit failure')
        return commit(*args, **kwargs)

    monkeypatch.setattr(store, 'commit_reports', fail_once)
    with pytest.raises(OSError):
        obj._record_results()
    assert len(t.pending(S.dispatch_status, 'core')) == 2
    obj._record_results()
    assert t.pending(S.dispatch_status, 'core') == []
    assert store.order_transitions(order.order_id).count('dispatch:unknown') == 1
    assert broker.remaining(order.order_id) == 100


def test_ack_failure_keeps_cached_evidence_and_store_deduplicates(flow, monkeypatch):
    t, broker, store, _, order = flow
    obj = runtime(broker, store)
    ack = MemoryTransport.ack
    once = [True]

    def fail(inner, stream, group, message_id):
        if stream.endswith(S.dispatch_status) and once[0]:
            once[0] = False
            raise OSError('fixture ACK failure')
        return ack(inner, stream, group, message_id)

    monkeypatch.setattr(MemoryTransport, 'ack', fail)
    with pytest.raises(OSError):
        obj._record_results()
    assert len(t.pending(S.dispatch_status, 'core')) == 2
    obj._record_results()
    assert t.pending(S.dispatch_status, 'core') == []
    assert store.order_transitions(order.order_id).count('dispatch:unknown') == 1


def test_dispatch_transition_and_portfolio_commit_are_one_transaction(flow):
    _, broker, store, _, order = flow
    values = broker.poll_dispatch_statuses()
    portfolio, state, prices, _ = store.load()
    portfolio.cash = 1.0
    missing = values[-1].model_copy(update={'client_order_id': 'missing'})
    with pytest.raises(ValueError, match='no persisted order'):
        store.commit_reports(portfolio, state, prices, (), (), (), {}, at=T,
                             dispatches=(values[-1], missing))
    assert store.load()[0].cash == 100000.0
    assert 'dispatch:unknown' not in store.order_transitions(order.order_id)


def test_cancel_request_keeps_pending_until_real_broker_terminal_status(flow):
    t, broker, _, _, order = flow
    broker.poll_dispatch_statuses()
    broker.cancel_all()
    assert broker.remaining(order.order_id) == 100
    t.append(S.order_status, OrderStatusEvent(client_order_id=order.order_id,
        broker_order_id='broker-fixture-1', symbol=str(order.symbol), side='buy',
        ordered_qty=100, filled_qty=0, remaining_qty=100, status='cancelled', at=T).to_payload())
    assert broker.poll_statuses()[0].status == 'cancelled'
    assert broker.pending() == ()


def test_invalid_dispatch_event_does_not_get_silently_acked(flow):
    t, broker, _, _, _ = flow
    t.append(S.dispatch_status, {'type': 'DispatchStatusEvent', 'data': '{}'})
    for _ in range(2):
        with pytest.raises(ValidationError):
            broker.poll_dispatch_statuses()
    assert len(t.pending(S.dispatch_status, 'core')) == 3


def test_dispatch_event_cannot_smuggle_a_fake_broker_order_id():
    with pytest.raises(ValidationError):
        DispatchStatusEvent(client_order_id='fixture', strategy_id='s6', session_id='2026-11-05',
            attempt_id='a' * 32, session_nonce='b' * 32, command_sha256='c' * 64,
            state='unknown', at=T, broker_order_id='fake')


def test_default_core_does_not_consume_new_stream():
    t = ValidationTransport(MemoryTransport(), NS)
    broker = make_broker(t, Portfolio(100000.0), enabled=False)
    assert broker.poll_dispatch_statuses() == ()
    assert S.dispatch_status not in broker._delivered
    # #601: production transport may consume dispatch status only when explicitly enabled.
    assert make_broker(MemoryTransport(), Portfolio(100000.0))._receive_dispatch_status is True
    assert make_broker(MemoryTransport(), Portfolio(100000.0), enabled=False).poll_dispatch_statuses() == ()
