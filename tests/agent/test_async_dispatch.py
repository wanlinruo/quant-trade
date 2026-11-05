from datetime import datetime, timedelta

import pytest

from qsys.agent.adapter import FakeAdapter
from qsys.agent.async_dispatch import (
    DispatchAcknowledgement, open_validation_dispatcher,
)
from qsys.agent.dispatch_store import DispatchConflictError
from qsys.agent.execution_agent import ExecutionAgent
from qsys.agent.locking import AlreadyRunning, single_instance
from qsys.live.config import StreamConfig
from qsys.live.messages import DispatchStatusEvent, OrderCommand
from qsys.live.transport import MemoryTransport
from qsys.live.validation_transport import ValidationTransport

T = datetime(2026, 11, 5, 9, 25, 10)
S = StreamConfig()
NS = 'component-validation:async-dispatch'


@pytest.fixture(autouse=True)
def agent_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return T + timedelta(seconds=1)
    monkeypatch.setattr('qsys.agent.execution_agent.datetime', Clock)


class Port:
    def __init__(self, outcome='ack'):
        self.outcome = outcome
        self.sent = []
        self.closed = False

    def send(self, envelope):
        self.sent.append(envelope)
        if self.outcome == 'timeout':
            raise TimeoutError('private account or token must not appear')
        if self.outcome == 'missing':
            return None
        return DispatchAcknowledgement(
            command_sha256=envelope.command_sha256, attempt_id=envelope.attempt_id,
            session_nonce=('f' * 32 if self.outcome == 'wrong_binding' else envelope.session_nonce),
            receiver_instance='test-receiver', received_at=T,
        )

    def close(self):
        self.closed = True


@pytest.fixture
def root(tmp_path):
    value = tmp_path / 'qsys-async-validation-dispatch'
    value.mkdir(mode=0o700)
    (value / '.qsys-async-validation').write_text(NS)
    return value


def command(**changes):
    values = dict(client_order_id='async-1', session_id=T.date().isoformat(), strategy_id='s6',
                  symbol='600036.SH', side='buy', qty=100, order_type='limit', limit_price=10.0,
                  created_at=T, expires_at=T + timedelta(seconds=60))
    values.update(changes)
    return OrderCommand(**values)


def options(root, transport, port, clock=lambda: T + timedelta(seconds=1)):
    return dict(enabled=True, root=root, transport=transport, streams=S,
                account_scope='validation:account', strategy_id='s6', session_id=T.date().isoformat(),
                adapter_factory=lambda: port, clock=clock)


def events(transport):
    return [DispatchStatusEvent.from_payload(x.payload) for x in transport.inner.all_messages(
        NS + ':' + S.dispatch_status)]


def test_disabled_and_real_transport_reject_before_initialization(root):
    called = []
    values = options(root, ValidationTransport(MemoryTransport(), NS), Port())
    values['adapter_factory'] = lambda: called.append(True)
    with pytest.raises(ValueError, match='disabled'):
        with open_validation_dispatcher(**dict(values, enabled=False)):
            pass
    with pytest.raises(ValueError, match='isolated'):
        with open_validation_dispatcher(**dict(values, transport=MemoryTransport())):
            pass
    assert called == []
    assert not (root / 'dispatch.sqlite3').exists()


def test_lock_is_held_before_database_or_port_initialization(root, monkeypatch):
    import qsys.agent.async_dispatch as module
    called = []
    monkeypatch.setattr(module, 'DispatchStore', lambda *a, **k: called.append('store'))
    with single_instance(root / 'dispatch.lock'):
        with pytest.raises(AlreadyRunning):
            with open_validation_dispatcher(**options(root, ValidationTransport(MemoryTransport(), NS), Port())):
                pass
    assert called == []
    assert not (root / 'dispatch.sqlite3').exists()


@pytest.mark.parametrize('outcome,expected', [('ack', 'transport_received'), ('timeout', 'unknown'),
                                             ('missing', 'unknown'), ('wrong_binding', 'unknown')])
def test_transport_outcome_is_not_a_broker_order_or_rejection(root, outcome, expected):
    t = ValidationTransport(MemoryTransport(), NS)
    port, adapter = Port(outcome), FakeAdapter()
    with open_validation_dispatcher(**options(root, t, port)) as dispatcher:
        agent = ExecutionAgent(t, adapter, S, async_dispatcher=dispatcher)
        t.append(S.orders, command().to_payload())
        agent.run_once()
        assert events(t)[-1].state == expected
        assert 'broker_order_id' not in events(t)[-1].model_dump()
        assert 'private account' not in events(t)[-1].reason
        assert adapter.placed == []
        assert t.inner.all_messages(NS + ':' + S.rejects) == []
        assert len(port.sent) == 1
        t.append(S.orders, command().to_payload())
        agent.run_once()
        assert len(port.sent) == 1
    assert port.closed
    with pytest.raises(RuntimeError, match='closed'):
        dispatcher.dispatch(command())


def test_restart_preserves_ack_and_never_resends_even_after_kill_and_expiry(root):
    t = ValidationTransport(MemoryTransport(), NS)
    first = Port()
    with open_validation_dispatcher(**options(root, t, first)) as dispatcher:
        agent = ExecutionAgent(t, FakeAdapter(), S, async_dispatcher=dispatcher)
        t.append(S.orders, command().to_payload())
        agent.run_once()
        old = dispatcher.store.read('async-1')
    second = Port()
    later = lambda: T + timedelta(seconds=120)
    with open_validation_dispatcher(**options(root, t, second, later)) as dispatcher:
        row = dispatcher.store.read('async-1')
        assert row['state'] == 'unknown'
        assert row['receipt_json'] == old['receipt_json']
        assert row['attempt_id'] == old['attempt_id']
        assert row['session_nonce'] == old['session_nonce']
        assert dispatcher.session_nonce != row['session_nonce']
        t.set_key('control:kill', 'test stop')
        agent = ExecutionAgent(t, FakeAdapter(), S, async_dispatcher=dispatcher)
        t.append(S.orders, command().to_payload())
        agent.run_once()
        assert second.sent == []
        assert events(t)[-1].state == 'unknown'
        assert events(t)[-1].transport_ack_sha256 is not None
        assert t.inner.all_messages(NS + ':' + S.rejects) == []


def test_changed_payload_retains_original_fact_and_unacked_command(root):
    t = ValidationTransport(MemoryTransport(), NS)
    port = Port('timeout')
    with open_validation_dispatcher(**options(root, t, port)) as dispatcher:
        agent = ExecutionAgent(t, FakeAdapter(), S, async_dispatcher=dispatcher)
        t.append(S.orders, command().to_payload())
        agent.run_once()
        before = dispatcher.store.read('async-1')
        t.append(S.orders, command(qty=200).to_payload())
        with pytest.raises(DispatchConflictError):
            agent.run_once()
        assert len(t.pending(S.orders, 'agent')) == 1
        assert dispatcher.store.read('async-1') == before
        assert len(port.sent) == 1
        assert t.inner.all_messages(NS + ':' + S.rejects) == []


def test_status_publish_failure_keeps_pel_and_replays_only_evidence(root, monkeypatch):
    t = ValidationTransport(MemoryTransport(), NS)
    port = Port()
    with open_validation_dispatcher(**options(root, t, port)) as dispatcher:
        agent = ExecutionAgent(t, FakeAdapter(), S, async_dispatcher=dispatcher)
        original = MemoryTransport.append
        fail = [True]

        def append(inner, stream, payload):
            if stream.endswith(S.dispatch_status) and fail[0]:
                fail[0] = False
                raise OSError('test unavailable stream')
            return original(inner, stream, payload)

        monkeypatch.setattr(MemoryTransport, 'append', append)
        t.append(S.orders, command().to_payload())
        with pytest.raises(OSError):
            agent.run_once()
        assert len(t.pending(S.orders, 'agent')) == 1
        assert port.sent == []
        agent.run_once()
        assert t.pending(S.orders, 'agent') == []
        assert events(t)[-1].state == 'unknown'
        assert port.sent == []


def test_receipt_store_failure_never_becomes_a_rejection_or_a_second_send(root, monkeypatch):
    t = ValidationTransport(MemoryTransport(), NS)
    port = Port()
    with open_validation_dispatcher(**options(root, t, port)) as dispatcher:
        agent = ExecutionAgent(t, FakeAdapter(), S, async_dispatcher=dispatcher)
        update = dispatcher.store.update
        once = [True]

        def fail(*a, **k):
            if once[0]:
                once[0] = False
                raise OSError('test failed commit')
            return update(*a, **k)

        monkeypatch.setattr(dispatcher.store, 'update', fail)
        t.append(S.orders, command().to_payload())
        with pytest.raises(OSError):
            agent.run_once()
        assert len(t.pending(S.orders, 'agent')) == 1
        agent.run_once()
        assert len(port.sent) == 1
        assert events(t)[-1].state == 'unknown'
        assert t.inner.all_messages(NS + ':' + S.rejects) == []


@pytest.mark.parametrize('changes', [dict(strategy_id=''), dict(session_id='old'),
    dict(expires_at=None), dict(expires_at=T), dict(symbol='920000.BJ'),
    dict(order_type='market')])
def test_fresh_invalid_intent_is_rejected_before_any_port_call(root, changes):
    t = ValidationTransport(MemoryTransport(), NS)
    port = Port()
    with open_validation_dispatcher(**options(root, t, port)) as dispatcher:
        agent = ExecutionAgent(t, FakeAdapter(), S, async_dispatcher=dispatcher)
        t.append(S.orders, command(**changes).to_payload())
        agent.run_once()
        assert port.sent == []
        assert len(t.inner.all_messages(NS + ':' + S.rejects)) == 1
        assert dispatcher.store.read('async-1')['state'] == 'rejected_before_send'


@pytest.mark.parametrize('price', [float('nan'), float('inf'), -1.0])
def test_nonfinite_or_negative_limit_is_rejected_before_serialization(root, price):
    from qsys.agent.async_dispatch import PreDispatchRejection
    t = ValidationTransport(MemoryTransport(), NS)
    port = Port()
    with open_validation_dispatcher(**options(root, t, port)) as dispatcher:
        with pytest.raises(PreDispatchRejection):
            dispatcher.dispatch(command(limit_price=price))
        assert port.sent == []
        assert dispatcher.store.read('async-1') is None



def test_unknown_blocks_fresh_opening_and_local_rejection_is_durable(root):
    t = ValidationTransport(MemoryTransport(), NS)
    port = Port('timeout')
    with open_validation_dispatcher(**options(root, t, port)) as dispatcher:
        agent = ExecutionAgent(t, FakeAdapter(), S, async_dispatcher=dispatcher)
        for value in (command(), command(client_order_id='async-2')):
            t.append(S.orders, value.to_payload())
            agent.run_once()
        assert len(port.sent) == 1
        assert dispatcher.store.read('async-2')['state'] == 'rejected_before_send'
    restarted = Port()
    with open_validation_dispatcher(**options(root, t, restarted)) as dispatcher:
        agent = ExecutionAgent(t, FakeAdapter(), S, async_dispatcher=dispatcher)
        t.append(S.orders, command(client_order_id='async-2').to_payload())
        agent.run_once()
        assert restarted.sent == []
        assert dispatcher.store.read('async-1')['state'] == 'unknown'


def test_expired_local_rejection_never_retries_when_gate_later_changes(root, monkeypatch):
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return T + timedelta(seconds=120)
    monkeypatch.setattr('qsys.agent.execution_agent.datetime', Later)
    t = ValidationTransport(MemoryTransport(), NS)
    port = Port()
    with open_validation_dispatcher(**options(root, t, port)) as dispatcher:
        agent = ExecutionAgent(t, FakeAdapter(), S, async_dispatcher=dispatcher)
        t.append(S.orders, command().to_payload())
        agent.run_once()
        assert dispatcher.store.read('async-1')['state'] == 'rejected_before_send'
        # Even an earlier injected validation clock cannot reopen this ID.
        monkeypatch.setattr(Later, 'now', classmethod(lambda cls, tz=None: T))
        t.append(S.orders, command().to_payload())
        agent.run_once()
        assert port.sent == []


def test_missing_wire_schema_is_not_filled_in_to_enable_a_new_send(root):
    from qsys.agent.async_dispatch import PreDispatchRejection
    t = ValidationTransport(MemoryTransport(), NS)
    port = Port()
    value = command().model_dump()
    del value['schema_version']
    with open_validation_dispatcher(**options(root, t, port)) as dispatcher:
        with pytest.raises(PreDispatchRejection):
            dispatcher.dispatch(OrderCommand.model_validate(value))
        assert port.sent == []


def test_stream_collision_and_store_scope_change_fail_closed(root):
    from dataclasses import replace
    t = ValidationTransport(MemoryTransport(), NS)
    port = Port()
    with pytest.raises(ValueError, match='distinct'):
        with open_validation_dispatcher(**dict(options(root, t, port),
                streams=replace(S, dispatch_status=S.fills))):
            pass
    with open_validation_dispatcher(**options(root, t, port)):
        pass
    with pytest.raises(ValueError, match='scope/schema'):
        with open_validation_dispatcher(**dict(options(root, t, Port()), account_scope='validation:other')):
            pass


@pytest.mark.parametrize('crash_point', ['after_claim', 'after_ack_commit'])
def test_real_child_exit_recovers_original_attempt_without_another_send(root, tmp_path, crash_point):
    import os
    from pathlib import Path
    import sqlite3
    import subprocess
    import sys
    script = tmp_path / 'crash_fixture.py'
    script.write_text('''
import json, os, sys
from datetime import datetime, timedelta
from pathlib import Path
from qsys.agent.async_dispatch import DispatchAcknowledgement, open_validation_dispatcher
from qsys.live.config import StreamConfig
from qsys.live.messages import OrderCommand
from qsys.live.transport import MemoryTransport
from qsys.live.validation_transport import ValidationTransport
point, root = sys.argv[1], Path(sys.argv[2])
now = datetime(2026, 11, 5, 9, 25, 10)
class Sink(MemoryTransport):
    def append(self, stream, payload):
        result = super().append(stream, payload)
        if payload.get('type') == 'DispatchStatusEvent' and json.loads(payload['data'])['state'] == 'transport_received':
            os._exit(17)
        return result
class Port:
    def send(self, envelope):
        if point == 'after_claim':
            os._exit(17)
        return DispatchAcknowledgement(command_sha256=envelope.command_sha256,
            attempt_id=envelope.attempt_id, session_nonce=envelope.session_nonce,
            receiver_instance='crash-fixture', received_at=now)
    def close(self):
        pass
t = ValidationTransport(Sink(), 'component-validation:async-dispatch')
with open_validation_dispatcher(enabled=True, root=root, transport=t, streams=StreamConfig(),
        account_scope='validation:account', strategy_id='s6', session_id=now.date().isoformat(),
        adapter_factory=Port, clock=lambda: now + timedelta(seconds=1)) as dispatcher:
    dispatcher.dispatch(OrderCommand.model_validate_json(sys.argv[3]))
''', encoding='utf-8')
    value = command()
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'src'),
               PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-B', str(script), crash_point, str(root),
                             value.model_dump_json()], env=env, capture_output=True, timeout=15)
    assert result.returncode == 17, result.stderr.decode()
    with sqlite3.connect(root / 'dispatch.sqlite3') as db:
        db.row_factory = sqlite3.Row
        before = dict(db.execute('SELECT * FROM dispatch_attempts').fetchone())
    assert before['state'] == ('dispatching' if crash_point == 'after_claim' else 'transport_received')
    assert (before['receipt_json'] is not None) == (crash_point == 'after_ack_commit')
    t, port = ValidationTransport(MemoryTransport(), NS), Port()
    with open_validation_dispatcher(**options(root, t, port)) as dispatcher:
        agent = ExecutionAgent(t, FakeAdapter(), S, async_dispatcher=dispatcher)
        t.append(S.orders, value.to_payload())
        agent.run_once()
        after = dispatcher.store.read(value.client_order_id)
        assert after['state'] == 'unknown'
        for field in ('command_json', 'command_sha256', 'attempt_id', 'session_nonce', 'receipt_json'):
            assert after[field] == before[field]
        assert port.sent == []
        assert t.inner.all_messages(NS + ':' + S.rejects) == []
