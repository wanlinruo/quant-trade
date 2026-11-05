"""Synthetic in-memory fixtures for the one-shot QMT diagnostic, no OS IPC."""
import ast
import importlib.util
import io
import json
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / 'docs/reference/qmt-external-mmap-probe.py'


def load():
    spec = importlib.util.spec_from_file_location('qmt_mmap_diagnostic', SOURCE)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    probe.RUN_ID = 'a' * 32
    return probe


def memory(probe, **changes):
    request = dict(protocol=probe._PROTOCOL, run_id=probe.RUN_ID,
                   request_id='b' * 32, kind='ping', payload=probe._PAYLOAD,
                   issued_at_ms=1000, expires_at_ms=2000)
    request.update(changes)
    body = json.dumps(request).encode()
    return io.BytesIO(len(body).to_bytes(4, 'little') + body + bytes(4096 - 4 - len(body)))


def test_echo_commits_only_response_slot_and_rejects_second_init(capsys):
    probe = load()
    m = memory(probe)
    original = m.getvalue()[:1024]
    result = probe._echo(m, json, 1500)
    assert m.getvalue()[:1024] == original
    m.seek(1024)
    size = int.from_bytes(m.read(4), 'little')
    response = json.loads(m.read(size))
    assert response['kind'] == 'echo'
    assert response['payload'] == probe._PAYLOAD
    assert response['authorizes_trading'] is False
    assert result['request_id'] == 'b' * 32
    probe._RAN = True
    probe.init(None)
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('changes', [dict(kind='order'), dict(payload='600000.SH'),
    dict(run_id='c' * 32), dict(request_id='bad'), dict(account='forbidden'),
    dict(expires_at_ms=1000), dict(issued_at_ms=True), dict(expires_at_ms=200000)])
def test_invalid_or_trading_message_never_writes_reply(changes):
    probe = load()
    m = memory(probe, **changes)
    before = m.getvalue()
    with pytest.raises(ValueError):
        probe._echo(m, json, 1500)
    assert m.getvalue() == before


def test_no_server_header_rejects_without_writing():
    probe = load()
    m = io.BytesIO(bytes(4096))
    with pytest.raises(ValueError):
        probe._echo(m, json, 1500)
    assert m.getvalue() == bytes(4096)


def test_diagnostic_has_no_files_network_dll_or_broker_calls():
    tree = ast.parse(SOURCE.read_text(), feature_version=(3, 6))
    calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)}
    assert calls <= {'int', 'datetime', 'dict', 'print', 'repr', '_valid_id',
                     'isinstance', 'len', 'all', 'ValueError', 'type', 'set', '_echo',
                     '_now_ms', '_emit'}
    attributes = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute)}
    assert attributes <= {'utcnow', 'total_seconds', 'update', 'seek', 'from_bytes',
                          'read', 'loads', 'decode', 'dumps', 'encode', 'write',
                          'to_bytes', 'now', 'strftime', 'mmap', 'close'}


def test_invalid_frame_closes_mapping_and_does_not_retry(monkeypatch, capsys):
    from datetime import datetime
    from types import SimpleNamespace
    probe = load()
    probe.CLIENT_BUILD = '2.1.33.0'
    probe.PROBE_DATE = datetime.now().strftime('%Y%m%d')
    m = io.BytesIO(bytes(4096))
    calls = []

    def factory(*args, **kwargs):
        calls.append((args, kwargs))
        return m

    monkeypatch.setitem(__import__('sys').modules, 'mmap',
                        SimpleNamespace(mmap=factory, ACCESS_WRITE=2))
    probe.init(None)
    probe.init(None)
    assert m.closed
    assert len(calls) == 1
    rows = [ast.literal_eval(x) for x in capsys.readouterr().out.splitlines()]
    assert len(rows) == 1
    assert rows[0]['status'] == 'failed'
    assert rows[0]['stage'] == 'validate_and_echo'
    assert rows[0]['trading_calls'] == 0


def test_mmap_rejection_is_redacted_without_fallback(monkeypatch, capsys):
    from datetime import datetime
    from types import SimpleNamespace
    probe = load()
    probe.CLIENT_BUILD = '2.1.33.0'
    probe.PROBE_DATE = datetime.now().strftime('%Y%m%d')
    calls = []

    def denied(*args, **kwargs):
        calls.append(1)
        raise PermissionError('private configuration must not appear')

    monkeypatch.setitem(__import__('sys').modules, 'mmap',
                        SimpleNamespace(mmap=denied, ACCESS_WRITE=2))
    probe.init(None)
    probe.init(None)
    raw = capsys.readouterr().out
    assert 'private configuration' not in raw
    assert len(calls) == 1
    row = ast.literal_eval(raw)
    assert row['stage'] == 'open_named_memory'
    assert row['error_type'] == 'PermissionError'
