import sqlite3
from datetime import datetime, timedelta

import pytest

from qsys.agent.async_dispatch import PreDispatchRejection, open_validation_dispatcher
from qsys.agent.dispatch_store import DispatchStore
from qsys.agent.qmt import protocol
from qsys.agent.qmt.memory import SharedMemory
from qsys.agent.qmt.port import QmtIpcPort
from qsys.live.config import StreamConfig
from qsys.live.messages import DispatchStatusEvent, OrderCommand, RejectEvent
from qsys.live.transport import MemoryTransport
from qsys.live.validation_transport import ValidationTransport
from qmt_fake import FakeQmtApi, load_executor

RUN = 'a' * 32
KEY = bytes(range(32))
T = datetime(2026, 11, 9, 9, 25, 10)
S = StreamConfig()
NS = 'component-validation:qmt-port'


class Rig:
    def __init__(self, tmp_path, *, market=None, deliver=True):
        self.now = T + timedelta(seconds=1)
        clock_ms = lambda: int(self.now.timestamp() * 1000)
        self.buf = bytearray(protocol.MAP_SIZE)
        self.mem = SharedMemory(self.buf)
        self.mem.beat(clock_ms())
        self.api = FakeQmtApi()
        mod = load_executor(ACCOUNT_ID='100000001', PRICE_TYPE_MARKET=market)
        self.ex = mod.Executor(RUN, KEY, self.api.as_api(), self.buf, clock_ms)
        self.deliver = deliver

        def sleep(_):
            if self.deliver:
                self.ex.tick(None)
        self.port = QmtIpcPort(self.mem, run_id=RUN, key=KEY, clock_ms=clock_ms, sleep=sleep,
                               receipt_timeout_s=0.2)
        self.transport = ValidationTransport(MemoryTransport(), NS)
        self.root = tmp_path / 'qsys-async-validation-qmt'
        self.root.mkdir(mode=0o700)
        (self.root / '.qsys-async-validation').write_text(NS)

    def open(self):
        return open_validation_dispatcher(
            enabled=True, root=self.root, transport=self.transport, streams=S,
            account_scope='validation:account', strategy_id='s6', session_id=T.date().isoformat(),
            adapter_factory=lambda: self.port, clock=lambda: self.now)

    def rejects(self):
        return [RejectEvent.from_payload(m.payload)
                for m in self.transport.inner.all_messages(NS + ':' + S.rejects)]

    def statuses(self):
        return [DispatchStatusEvent.from_payload(m.payload)
                for m in self.transport.inner.all_messages(NS + ':' + S.dispatch_status)]

    def passorders(self):
        return [c for c in self.api.calls if c[0] == 'passorder']


def cmd(**changes):
    values = dict(schema_version=2, client_order_id='q261109001abcdef012345', session_id=T.date().isoformat(),
                  strategy_id='s6', symbol='510300.SH', side='buy', qty=100, order_type='limit',
                  limit_price=4.0, created_at=T, expires_at=T + timedelta(seconds=60))
    values.update(changes)
    return OrderCommand(**values)


def test_accepted_receipt_becomes_transport_received(tmp_path):
    rig = Rig(tmp_path)
    with rig.open() as dispatcher:
        dispatcher.dispatch(cmd())
    assert [e.state for e in rig.statuses()] == ['dispatching', 'transport_received']
    assert len(rig.passorders()) == 1


def test_executor_rejection_becomes_reject_event_not_unknown(tmp_path):
    rig = Rig(tmp_path)
    with rig.open() as dispatcher:
        dispatcher.dispatch(cmd(limit_price=200.0))
        assert not dispatcher.store.has_unresolved()
    assert [r.reason for r in rig.rejects()] == ['executor:order_notional']
    assert rig.passorders() == []


def test_missing_receipt_becomes_unknown_and_not_resent(tmp_path):
    rig = Rig(tmp_path, deliver=False)
    with rig.open() as dispatcher:
        dispatcher.dispatch(cmd())
        dispatcher.dispatch(cmd())
    assert [e.state for e in rig.statuses()] == ['dispatching', 'unknown', 'unknown']
    assert rig.rejects() == []


def test_call_exception_receipt_becomes_unknown(tmp_path):
    rig = Rig(tmp_path)

    def boom(*args):
        raise RuntimeError('native')
    rig.ex.api['passorder'] = boom
    with rig.open() as dispatcher:
        dispatcher.dispatch(cmd())
    assert rig.statuses()[-1].state == 'unknown'
    assert rig.statuses()[-1].reason == 'adapter_error:ExecutorCallUnknown'


def test_market_sell_dispatched_market_buy_rejected(tmp_path):
    rig = Rig(tmp_path, market=5)
    with rig.open() as dispatcher:
        dispatcher.dispatch(cmd(side='sell', order_type='market', limit_price=None))
        with pytest.raises(PreDispatchRejection):
            dispatcher.dispatch(cmd(client_order_id='q261109002abcdef012345', order_type='market',
                                    limit_price=None))
    assert len(rig.passorders()) == 1 and rig.passorders()[0][5] == 5


def test_expired_at_executor_is_rejected_not_unknown(tmp_path):
    rig = Rig(tmp_path)
    with rig.open() as dispatcher:
        # The gateway clock still sees it as valid, the executor's clock has moved past expiry.
        rig.ex.clock = lambda: int((T + timedelta(seconds=61)).timestamp() * 1000)
        rig.mem.beat(rig.ex.clock())
        dispatcher.dispatch(cmd())
    assert [r.reason for r in rig.rejects()] == ['executor:transport_expired']


def test_redelivered_command_after_restart_replays_without_send(tmp_path):
    rig = Rig(tmp_path)
    with rig.open() as dispatcher:
        dispatcher.dispatch(cmd())
    with rig.open() as dispatcher:                       # gateway restart: recover() → unknown
        dispatcher.dispatch(cmd())
    assert len(rig.passorders()) == 1
    assert rig.statuses()[-1].state == 'unknown'


def test_v1_store_is_refused(tmp_path):
    path = tmp_path / 'dispatch.sqlite3'
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE dispatch_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    db.execute("""INSERT INTO dispatch_metadata VALUES ('binding','{"schema_version": 1, "scope": "x"}')""")
    db.commit()
    db.close()
    with pytest.raises(ValueError, match='scope/schema'):
        DispatchStore(path, scope='x')


def test_resolve_only_from_unknown(tmp_path):
    rig = Rig(tmp_path, deliver=False)
    with rig.open() as dispatcher:
        dispatcher.dispatch(cmd())
        dispatcher.resolve('q261109001abcdef012345', 'absent', 'two_empty_queries')
        assert not dispatcher.store.has_unresolved()
        with pytest.raises(RuntimeError):
            dispatcher.resolve('q261109001abcdef012345', 'present', 'again')
    assert rig.statuses()[-1].state == 'resolved_absent'
    assert [r.reason for r in rig.rejects()] == ['reconciled_absent:two_empty_queries']


def test_resolve_present_publishes_status_only(tmp_path):
    rig = Rig(tmp_path, deliver=False)
    with rig.open() as dispatcher:
        dispatcher.dispatch(cmd())
        dispatcher.resolve('q261109001abcdef012345', 'present', 'order_found')
    assert rig.statuses()[-1].state == 'resolved_present'
    assert rig.rejects() == []
