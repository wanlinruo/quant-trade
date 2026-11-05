from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import hmac
import importlib.util
import io
import json
from pathlib import Path
import time

import pytest

from qsys.agent.async_dispatch import DispatchEnvelope, open_validation_dispatcher
from qsys.agent.dispatch_store import canonical_command
from qsys.live.config import StreamConfig
from qsys.live.messages import OrderCommand
from qsys.live.transport import MemoryTransport
from qsys.live.validation_transport import ValidationTransport

ROOT = Path(__file__).resolve().parents[2] / 'docs/reference'


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / name)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


P = load('qmt-async-diagnostic-port.py')
Q = load('qmt-external-queue-probe.py')
RUN = 'a' * 32


def command(**changes):
    now = datetime.now()
    values = dict(schema_version=2, session_id='synthetic-session', strategy_id='diagnostic-only',
        client_order_id='synthetic-qsys601-one', symbol='600036.SH', side='buy', qty=100,
        order_type='limit', limit_price=10.0, created_at=now, expires_at=now + timedelta(minutes=10))
    values.update(changes)
    return OrderCommand(**values)


def envelope(cmd=None):
    raw = canonical_command(cmd or command())
    return DispatchEnvelope(raw, hashlib.sha256(raw.encode()).hexdigest(), 'b' * 32, 'c' * 32)


class Memory(io.BytesIO):
    def __init__(self, run=RUN, mutate=None, absent=False):
        super().__init__(bytes(8192))
        self.run, self.mutate, self.absent = run, mutate, absent
        self.receiver = Q.Receiver(run, P.KEY, json, hmac)
        self.packets = []
        self.seek(6144)
        super().write(b'QSYS599_RECOVERY_ONLY:' + run.encode())

    def write(self, raw):
        position = self.tell()
        result = super().write(raw)
        if position == 0 and raw != bytes(4):
            generation = int.from_bytes(raw, 'little')
            value = self.getvalue()
            request = value[8:8+int.from_bytes(value[4:8], 'little')]
            self.packets.append(json.loads(request))
            if not self.absent:
                response = self.receiver.receive(request, int(time.time()*1000))
                if self.mutate:
                    data = json.loads(response)
                    self.mutate(data)
                    response = P.canonical(data)
                self.seek(4096)
                super().write(generation.to_bytes(4,'little') + len(response).to_bytes(4,'little') + response)
        return result


def test_ping_ack_is_bound_but_no_order_or_nonce_is_in_qmt_packet():
    memory, env = Memory(), envelope()
    port = P.DiagnosticPingPort(memory, RUN, enabled=True)
    ack = port.send(env)
    assert (ack.attempt_id, ack.session_nonce, ack.command_sha256) == (
        env.attempt_id, env.session_nonce, env.command_sha256)
    assert len(port.receipts) == 1 and len(memory.packets) == 1
    packet = memory.packets[0]
    assert packet['payload'] == 'QSYS599_DIAGNOSTIC_ONLY'
    assert packet['request_id'] == env.attempt_id
    assert not {'symbol','qty','limit_price','account','session_nonce','command_json'} & set(packet)
    with pytest.raises(ValueError, match='duplicate'):
        port.send(env)
    port.close()


@pytest.mark.parametrize('changes', [dict(strategy_id='s6'), dict(qty=200),
    dict(symbol='920000.BJ'), dict(side='sell'), dict(client_order_id='real-order')])
def test_real_or_nonfixture_intents_fail_before_any_write(changes):
    memory = Memory()
    port = P.DiagnosticPingPort(memory, RUN, enabled=True)
    with pytest.raises(ValueError, match='synthetic'):
        port.send(envelope(command(**changes)))
    assert memory.packets == []


@pytest.mark.parametrize('field,value', [('run_id','d'*32),('request_id','d'*32),
    ('seq',True),('authorizes_trading',True),('accepted_count',2),('status','rejected')])
def test_signed_stale_or_wrong_scope_echo_cannot_be_an_ack(field, value):
    def mutate(data):
        data[field] = value
        data.pop('mac')
        data['mac'] = P.signature(data)
    port = P.DiagnosticPingPort(Memory(mutate=mutate), RUN, enabled=True)
    with pytest.raises(ValueError, match='binding/scope'):
        port.send(envelope())
    assert port.receipts == []


def test_disabled_bad_marker_bad_hash_and_timeout_never_retry():
    with pytest.raises(ValueError):
        P.DiagnosticPingPort(Memory(), RUN)
    with pytest.raises(ValueError, match='marker'):
        P.DiagnosticPingPort(io.BytesIO(bytes(8192)), RUN, enabled=True)
    memory = Memory(absent=True)
    port = P.DiagnosticPingPort(memory, RUN, enabled=True, timeout=0.02)
    env = envelope()
    with pytest.raises(ValueError, match='SHA'):
        port.send(replace(env, command_sha256='f'*64))
    assert memory.packets == []
    with pytest.raises(TimeoutError):
        port.send(env)
    assert len(memory.packets) == 1
    with pytest.raises(ValueError, match='duplicate'):
        port.send(env)


def test_bad_mac_and_duplicate_reply_keys_are_rejected():
    port = P.DiagnosticPingPort(Memory(), RUN, enabled=True)
    env = envelope()
    port.send(env)
    value = dict(protocol=P.PROTOCOL, run_id=RUN, request_id=env.attempt_id, seq=1,
                 status='echo', reason='ok', accepted_count=1, authorizes_trading=False)
    value['mac'] = '0'*64
    with pytest.raises(ValueError, match='signature'):
        port._validate(P.canonical(value), env)
    raw = b'{"seq":1,' + P.canonical(value)[1:]
    with pytest.raises(ValueError, match='duplicate'):
        port._validate(raw, env)


def test_actual_dispatcher_reopen_with_fresh_receiver_does_not_resend_unexpired(tmp_path):
    ns = 'component-validation:async-port'
    root = tmp_path/'qsys-async-validation-port'
    root.mkdir(mode=0o700)
    (root/'.qsys-async-validation').write_text(ns)
    t = ValidationTransport(MemoryTransport(), ns)
    opts = dict(enabled=True,root=root,transport=t,streams=StreamConfig(),
        account_scope='validation:ping',strategy_id='diagnostic-only',session_id='synthetic-session')
    cmd = command()
    first = P.DiagnosticPingPort(Memory(),RUN,enabled=True)
    with open_validation_dispatcher(**opts,adapter_factory=lambda:first) as d:
        d.dispatch(cmd)
        original = d.store.read(cmd.client_order_id)
        assert original['state'] == 'transport_received'
    second_memory = Memory('d'*32)
    second = P.DiagnosticPingPort(second_memory,'d'*32,enabled=True)
    with open_validation_dispatcher(**opts,adapter_factory=lambda:second) as d:
        assert datetime.now() < cmd.expires_at
        d.dispatch(cmd)
        restored = d.store.read(cmd.client_order_id)
        assert restored['state'] == 'unknown'
        assert restored['attempt_id'] == original['attempt_id']
        assert restored['receipt_json'] == original['receipt_json']
        assert second_memory.packets == [] and second_memory.receiver.seen == {}
