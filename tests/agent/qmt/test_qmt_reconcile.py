from datetime import datetime, timedelta
from types import SimpleNamespace

from qsys.agent.async_dispatch import open_qmt_dispatcher
from qsys.agent.qmt import protocol
from qsys.agent.qmt.adapter import QmtIpcAdapter
from qsys.agent.qmt.memory import SharedMemory
from qsys.agent.qmt.port import QmtIpcPort
from qsys.agent.qmt.reconcile import UnknownReconciler
from qsys.live.config import StreamConfig
from qsys.live.messages import OrderCommand, RejectEvent
from qsys.live.transport import MemoryTransport
from qmt_fake import ACCOUNT, FakeQmtApi, load_executor

RUN = 'a' * 32
KEY = bytes(range(32))
S = StreamConfig()
T = datetime(2026, 11, 9, 9, 25, 10)
SCOPE = 'qmt:0123456789abcdef'
CID = 'q261109001abcdef012345'


class Rig:
    def __init__(self, tmp_path, *, executor_started=T - timedelta(minutes=5)):
        self.now = T + timedelta(seconds=1)
        self.buf = bytearray(protocol.MAP_SIZE)
        self.mem = SharedMemory(self.buf)
        self.api = FakeQmtApi()
        self.api.accounts.append(SimpleNamespace(m_strAccountID=ACCOUNT, m_dAvailable=10_000.0,
                                                 m_dBalance=10_000.0, m_dStockValue=0.0))
        self.mod = load_executor(ACCOUNT_ID=ACCOUNT)
        self.start_executor(executor_started)
        self.deliver = False
        self.port = QmtIpcPort(self.mem, run_id=RUN, key=KEY, clock_ms=self.ms,
                               sleep=self._sleep, receipt_timeout_s=0.1)
        self.adapter = QmtIpcAdapter(self.mem, self.port)
        self.transport = MemoryTransport()
        self.root = tmp_path / 'qsys-qmt-dispatch-t'
        self.root.mkdir(mode=0o700)
        (self.root / '.qsys-qmt-dispatch').write_text(SCOPE)

    def ms(self, value=None):
        return int((value or self.now).timestamp() * 1000)

    def start_executor(self, at):
        self.ex = self.mod.Executor(RUN, KEY, self.api.as_api(), self.buf, lambda: self.ms(at))
        self.ex.clock = lambda: self.ms()

    def _sleep(self, seconds):
        if getattr(self, 'time_flows', False):
            self.now += timedelta(seconds=max(seconds, 0.3))   # real clocks move while waiting
        self.mem.beat(self.ms())
        if self.deliver:
            self.ex.tick(None)

    def open(self):
        return open_qmt_dispatcher(enabled=True, root=self.root, transport=self.transport,
                                   streams=S, port=self.port, account_scope=SCOPE,
                                   strategy_id='s6', session_of=lambda: T.date().isoformat(),
                                   clock=lambda: self.now)

    def reconciler(self, dispatcher):
        return UnknownReconciler(dispatcher, self.adapter, self.mem, grace_s=60.0,
                                 clock=lambda: self.now)

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)
        self.mem.beat(self.ms())
        self.ex.tick(None)

    def rejects(self):
        return [RejectEvent.from_payload(m.payload) for m in self.transport.all_messages(S.rejects)]


def cmd():
    return OrderCommand(schema_version=2, client_order_id=CID, session_id=T.date().isoformat(),
                        strategy_id='s6', symbol='510300.SH', side='buy', qty=100,
                        order_type='limit', limit_price=4.0, created_at=T,
                        expires_at=T + timedelta(seconds=60))


def lost_dispatch(rig, dispatcher):
    rig.ex._beat()                        # executor alive, but this receipt is lost
    rig.deliver = False
    dispatcher.dispatch(cmd())            # executor never ticks → unknown
    rig.deliver = True
    assert dispatcher.store.has_unresolved()


def test_present_order_resolves_without_resend(tmp_path):
    rig = Rig(tmp_path)
    with rig.open() as d:
        lost_dispatch(rig, d)
        rig.api.order(CID)                # it did reach the broker after all
        assert rig.reconciler(d).run_once() == [(CID, 'present')]
        assert not d.store.has_unresolved()
    assert [c for c in rig.api.calls if c[0] == 'passorder'] == [] and rig.rejects() == []


def test_absent_requires_expiry_plus_grace_and_two_consistent_queries(tmp_path):
    rig = Rig(tmp_path)
    with rig.open() as d:
        lost_dispatch(rig, d)
        r = rig.reconciler(d)
        rig.advance(60)                   # expired, but grace not over
        assert r.run_once() == []
        rig.advance(61)                   # past expiry + grace: first empty query
        assert r.run_once() == []
        rig.advance(3)                    # second query too soon
        assert r.run_once() == []
        rig.advance(3)
        assert r.run_once() == [(CID, 'absent')]
    assert [x.reason.split(':')[0] for x in rig.rejects()] == ['reconciled_absent']


def test_executor_restart_since_command_keeps_unknown(tmp_path):
    rig = Rig(tmp_path)
    with rig.open() as d:
        lost_dispatch(rig, d)
        r = rig.reconciler(d)
        rig.start_executor(T + timedelta(seconds=30))       # QMT rebuilt after the command
        for _ in range(4):
            rig.advance(61)
            assert r.run_once() == []
        assert d.store.has_unresolved()


def test_epoch_change_between_queries_restarts_observation(tmp_path):
    rig = Rig(tmp_path)
    with rig.open() as d:
        lost_dispatch(rig, d)
        r = rig.reconciler(d)
        rig.advance(121)
        assert r.run_once() == []
        rig.start_executor(T - timedelta(minutes=1))       # different epoch (fake start time)
        rig.advance(6)
        assert r.run_once() == []                          # first query of the new epoch
        assert d.store.has_unresolved()


def test_heartbeat_gap_keeps_unknown(tmp_path):
    rig = Rig(tmp_path)
    with rig.open() as d:
        lost_dispatch(rig, d)
        r = rig.reconciler(d)
        rig.now += timedelta(seconds=121)                  # executor did not beat
        rig.mem.beat(rig.ms())
        rig.deliver = False
        assert r.run_once() == []
        rig.now += timedelta(seconds=6)
        assert r.run_once() == []
        assert d.store.has_unresolved()


def test_query_failure_keeps_unknown(tmp_path):
    rig = Rig(tmp_path)
    with rig.open() as d:
        lost_dispatch(rig, d)
        r = rig.reconciler(d)
        rig.advance(121)
        rig.deliver = False                                # snapshot receipt times out
        assert r.run_once() == []
        assert d.store.has_unresolved()


def test_unresolved_blocks_new_buys_until_resolved(tmp_path):
    from qsys.agent.async_dispatch import PreDispatchRejection
    import pytest
    rig = Rig(tmp_path)
    with rig.open() as d:
        lost_dispatch(rig, d)
        with pytest.raises(PreDispatchRejection, match='unresolved'):
            d.dispatch(cmd().model_copy(update={'client_order_id': 'q261109002abcdef012345'}))


def test_absent_resolves_when_heartbeat_is_newer_than_query_start(tmp_path):
    """2026-11-10 F4 field finding: the executor answers the snapshot on its next tick and beats
    in that tick, so its heartbeat is always newer than a ``now`` taken before the query.
    Freshness must be judged after the query, or no unknown is ever released."""
    rig = Rig(tmp_path)
    with rig.open() as d:
        lost_dispatch(rig, d)
        r = rig.reconciler(d)
        rig.time_flows = True
        rig.advance(121)
        assert r.run_once() == []
        rig.advance(6)
        assert r.run_once() == [(CID, 'absent')]
