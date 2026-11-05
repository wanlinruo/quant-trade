"""Recovery wiring with real SQLite and test transport only; no QMT/broker."""
import base64
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2] / 'docs/reference'


def load(filename):
    spec = importlib.util.spec_from_file_location(filename.replace('-', '_'), ROOT / filename)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def reply(gateway, **changes):
    value = dict(protocol=gateway.PROTOCOL, run_id='a' * 32,
                 request_id='b' * 32, seq=1, status='echo', reason='ok',
                 accepted_count=1, authorizes_trading=False)
    value.update(changes)
    value['mac'] = gateway.sign(value)
    return gateway.canonical(value)


def test_same_generation_wrong_session_or_message_cannot_be_acknowledged():
    obj = load('qmt-external-recovery-gateway.py')
    config = dict(session_id='a' * 32)
    result = obj.validate_reply(reply(obj), config, 'b' * 32, 1)
    assert result['accepted_count'] == 1
    for raw in (reply(obj, run_id='c' * 32), reply(obj, request_id='c' * 32),
                reply(obj, seq=True), reply(obj, authorizes_trading=True),
                reply(obj, accepted_count=-1)):
        with pytest.raises(ValueError):
            obj.validate_reply(raw, config, 'b' * 32, 1)


def test_unknown_is_blocked_before_any_mapping_access(tmp_path, monkeypatch):
    obj = load('qmt-external-recovery-gateway.py')
    Ledger = load('qmt-external-dispatch-ledger.py').DiagnosticLedger
    run = 'a' * 32
    root = tmp_path / ('qsys599-ledger-' + run)
    root.mkdir(mode=0o700)
    (root / '.diagnostic-only').write_text(run)
    store = Ledger(root, run)
    store.prepare('b' * 32, 1000, 2000)
    assert store.claim('b' * 32, 'c' * 32, 'd' * 32, 1500)
    store.recover_unfinished()
    monkeypatch.setattr(obj, 'read_config', lambda _: dict(messages={'b': 'b' * 32}, session_id='e' * 32))
    monkeypatch.setattr(obj, 'ledger', lambda _: Ledger(root, run))
    monkeypatch.setattr(obj, 'now_ms', lambda: 1500)
    monkeypatch.setattr(obj, 'open_memory', lambda _: pytest.fail('unknown touched transport'))
    value = obj.worker(tmp_path, 'b', 'complete', 1, 1)
    assert value == dict(status='blocked', message='b', state='unknown', published=0, before_expiry=True)
    assert store.read('b' * 32)['session_nonce'] == 'd' * 32


def test_ntfs_validation_is_bounded_encoded_and_rejects_unavailable_evidence(tmp_path, monkeypatch):
    obj = load('qmt-external-dispatch-ledger.py')
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        script = base64.b64decode(args[-1]).decode('utf-16le')
        assert 'AreAccessRulesProtected' in script
        assert 'ReparsePoint' in script
        assert str(tmp_path) not in script  # path travels as encoded data
        return SimpleNamespace(returncode=0, stdout=b'PRIVATE')

    monkeypatch.setattr(obj.subprocess, 'run', run)
    obj.windows_private_directory(tmp_path)
    assert calls[0][1]['timeout'] == 15
    assert 'shell' not in calls[0][1]
    monkeypatch.setattr(obj.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout=b'REJECTED'))
    with pytest.raises(ValueError, match='NTFS private fixture rejected'):
        obj.windows_private_directory(tmp_path)
