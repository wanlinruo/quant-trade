"""Synthetic messages and fake callbacks only; never load QMT or a broker."""
import ast
import hashlib
import hmac
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[2] / 'docs/reference/qmt-external-queue-probe.py'
# Public test fixture, not a credential or production identity.
KEY = bytes([17] * 32)
RUN = 'a' * 32


def load():
    spec = importlib.util.spec_from_file_location('qmt_queue_probe', SOURCE)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    obj.RUN_ID = RUN
    return obj


def packet(**changes):
    value = dict(protocol='qsys.external-queue-diagnostic.v1', run_id=RUN,
                 request_id='b' * 32, seq=1, kind='ping',
                 payload='QSYS599_DIAGNOSTIC_ONLY', issued_at_ms=1000,
                 expires_at_ms=2000)
    value.update(changes)
    body = json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')
    value['mac'] = hmac.new(KEY, body, hashlib.sha256).hexdigest()
    return json.dumps(value, separators=(',', ':')).encode('ascii')


def receiver(obj):
    return obj.Receiver(RUN, KEY, json, hmac)


def assert_signed_reply(raw):
    obj = json.loads(raw)
    signature = obj.pop('mac')
    body = json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()
    assert hmac.compare_digest(signature, hmac.new(KEY, body, hashlib.sha256).hexdigest())
    return obj


def test_duplicate_ack_is_original_signed_bytes_and_accepted_only_once():
    obj = load()
    r = receiver(obj)
    first = r.receive(packet(), 1500)
    assert r.receive(packet(), 1600) == first
    assert assert_signed_reply(first)['accepted_count'] == 1
    assert r.accepted == 1
    assert r.next_seq == 2


@pytest.mark.parametrize('change,reason', [
    (dict(kind='order'), 'synthetic_only'), (dict(payload='600000.SH'), 'synthetic_only'),
    (dict(account='forbidden'), 'fields'), (dict(seq=2), 'sequence'),
    (dict(seq=True), 'identity'), (dict(request_id='bad'), 'identity'),
    (dict(run_id='c' * 32), 'binding'), (dict(expires_at_ms=1200), 'expired'),
    (dict(issued_at_ms=True), 'time_type'), (dict(expires_at_ms=200000), 'expired')])
def test_rejections_never_advance_dedup_or_acceptance(change, reason):
    r = receiver(load())
    response = assert_signed_reply(r.receive(packet(**change), 1500))
    assert response['reason'] == reason
    assert r.accepted == 0
    assert r.seen == {}
    assert r.next_seq == 1


def test_tampered_message_and_wrong_key_are_rejected_before_acceptance():
    obj = load()
    r = receiver(obj)
    v = json.loads(packet())
    v['expires_at_ms'] += 1
    assert assert_signed_reply(r.receive(json.dumps(v).encode(), 1500))['reason'] == 'authentication'
    wrong = obj.Receiver(RUN, bytes([18] * 32), json, hmac)
    assert assert_signed_reply(r.receive(packet(), 1500))['reason'] == 'ok'
    assert json.loads(wrong.receive(packet(), 1500))['reason'] == 'authentication'
    assert wrong.accepted == 0


def test_id_conflict_does_not_reexecute_and_restart_has_no_durable_guarantee():
    obj = load()
    r = receiver(obj)
    r.receive(packet(), 1500)
    changed = packet(issued_at_ms=1001)
    assert assert_signed_reply(r.receive(changed, 1500))['reason'] == 'request_conflict'
    assert r.accepted == 1
    restarted = receiver(obj)
    assert json.loads(restarted.receive(packet(), 1500))['reason'] == 'ok'
    assert restarted.accepted == 1  # Explicitly not durable cross-process dedup.


def test_authentication_key_is_never_logged(capsys):
    obj = load()
    obj.AUTH_KEY_HEX = KEY.hex()
    obj.emit('test')
    assert KEY.hex() not in capsys.readouterr().out


def test_callback_processes_one_generation_without_loop_and_cancel_is_scoped(monkeypatch, capsys):
    obj = load()
    m = io.BytesIO(bytes(8192))
    raw = packet()
    m.write((1).to_bytes(4, 'little') + len(raw).to_bytes(4, 'little') + raw)
    calls = []
    context = SimpleNamespace(cancel_schedule_run=lambda group: calls.append(group) or True)
    obj._STATE = dict(memory=m, receiver=receiver(obj), ticks=0, packets=0,
                      last_generation=0, max_ms=0.0, stopped=False,
                      deadline_ms=2000, clock=lambda: 0.0)
    monkeypatch.setattr(obj, 'now_ms', lambda: 1500)
    obj.on_timer(context)
    obj.on_timer(context)
    assert obj._STATE['packets'] == 1
    assert obj._STATE['receiver'].accepted == 1
    obj.stop(context)
    obj.stop(context)
    assert m.closed
    assert calls == ['qsys599_' + RUN]
    assert KEY.hex() not in capsys.readouterr().out


def test_probe_has_no_account_order_files_socket_or_native_dll_calls():
    tree = ast.parse(SOURCE.read_text(), feature_version=(3, 6))
    callback = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'on_timer')
    assert not any(isinstance(n, (ast.For, ast.While, ast.AsyncFor)) for n in ast.walk(callback))
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert calls <= {'dumps', 'encode', 'loads', 'decode', 'get', 'pop', 'new', 'hexdigest',
                     'compare_digest', 'now', 'timestamp', 'update', 'cancel_schedule_run',
                     'close', 'seek', 'from_bytes', 'read', 'receive', 'write', 'to_bytes',
                     'strftime', 'fromhex', 'mmap', 'schedule_run', 'sign', 'reply'}
