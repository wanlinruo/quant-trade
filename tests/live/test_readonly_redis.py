from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import threading
from unittest.mock import Mock

import pytest

from qsys.live.config import DeployConfig
from qsys.live import redis_readonly_gateway as gateway
from qsys.live.readonly_transport import ReadonlyTransport, readonly_from_config


def policy(uid=1001):
    return {"protocol": 1, "redis": {"host": "127.0.0.1", "port": 6379, "db": 0},
            "readers": {str(uid): {"keys": ["control:kill", "state:heartbeat:agent"],
                                    "date_prefixes": ["state:price_limits_check"]}},
            "blocked_uids": [1001, 1002]}


def request(key, **extra):
    return json.dumps({"protocol": 1, "key": key, **extra}).encode() + b"\n"


@pytest.mark.parametrize("key", ["control:kill", "state:heartbeat:agent", "state:price_limits_check:20261022", "state:price_limits_check:20240229"])
def test_precise_keys_and_real_calendar_dates(key):
    assert gateway.request_key(request(key), 1001, policy()) == key


@pytest.mark.parametrize("raw,uid", [
    (request("control:kill"), 1002),
    (request("control:kill", command="SET"), 1001),
    (request("orders"), 1001),
    (request("control:*"), 1001),
    (request("state:price_limits_check:20260229"), 1001),
    (request("state:price_limits_check:20261022:extra"), 1001),
    (b'{"protocol":1,"protocol":1,"key":"control:kill"}\n', 1001),
    (request("control:kill", protocol=True), 1001),
    (request("control:kill")[:-1], 1001),
    (b"x" * 4097 + b"\n", 1001),
])
def test_bad_or_unauthorized_request_fails_before_backend(raw, uid):
    with pytest.raises((ValueError, PermissionError)):
        gateway.request_key(raw, uid, policy())


@pytest.mark.parametrize("method", ["append", "ensure_group", "reset_group", "read_group", "ack", "pending", "set_key", "delete_key"])
def test_client_never_exposes_other_commands(method):
    client = ReadonlyTransport("/absent.sock", 1)
    with pytest.raises(PermissionError):
        getattr(client, method)("unused")


def test_socket_mode_never_constructs_direct_redis(monkeypatch):
    from qsys.live.redis_transport import RedisTransport
    direct = Mock(side_effect=AssertionError("direct fallback forbidden"))
    monkeypatch.setattr(RedisTransport, "from_config", direct)
    monkeypatch.setenv("QSYS_REDIS_READER_SOCKET", "/no-such-qsys-reader.sock")
    client = readonly_from_config(DeployConfig())
    with pytest.raises(OSError):
        client.get_key("control:kill")
    direct.assert_not_called()
    monkeypatch.setenv("QSYS_REDIS_READER_SOCKET", "")
    with pytest.raises(ValueError):
        readonly_from_config(DeployConfig())


@pytest.mark.parametrize("value", [None, '', '{"unicode":"原值\\n","n":1.00}', 'x' * 10000])
@pytest.mark.skipif(not hasattr(socket, 'SO_PEERCRED'), reason="Linux peer credentials")
def test_real_unix_peer_and_original_values(tmp_path, monkeypatch, value):
    endpoint = str(tmp_path / "reader.sock")
    backend = Mock(return_value=value)
    monkeypatch.setattr(gateway, "redis_get", backend)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(endpoint)
        listener.listen()
        def server():
            conn, _ = listener.accept()
            gateway.handle(conn, policy(os.getuid()))
        worker = threading.Thread(target=server)
        worker.start()
        assert ReadonlyTransport(endpoint, 2).get_key("control:kill") == value
        worker.join(3)
        assert not worker.is_alive()
    backend.assert_called_once_with("control:kill")


@pytest.mark.skipif(not hasattr(socket, 'SO_PEERCRED'), reason="Linux peer credentials")
def test_peer_identity_cannot_be_supplied_in_request(monkeypatch):
    backend = Mock(side_effect=AssertionError("must not reach backend"))
    monkeypatch.setattr(gateway, "redis_get", backend)
    client, server = socket.socketpair()
    worker = threading.Thread(target=gateway.handle, args=(server, policy(os.getuid())))
    worker.start()
    with client:
        client.sendall(request("control:kill", uid=os.getuid()))
        reply = json.loads(client.recv(4096))
        assert "error" in reply and "value" not in reply
    worker.join(3)
    backend.assert_not_called()


@pytest.mark.parametrize("wire,expected", [(b"$-1\r\n", None), (b"$0\r\n\r\n", ""), (b"$3\r\nabc\r\n", "abc")])
def test_backend_emits_only_resp2_get(monkeypatch, wire, expected):
    client, server = socket.socketpair()
    received = []
    def backend():
        with server:
            received.append(server.recv(4096))
            server.sendall(wire)
    worker = threading.Thread(target=backend)
    worker.start()
    monkeypatch.setattr(gateway.socket, "create_connection", lambda address, timeout: client)
    assert gateway.redis_get("control:kill") == expected
    worker.join(3)
    assert received == [b"*2\r\n$3\r\nGET\r\n$12\r\ncontrol:kill\r\n"]


@pytest.mark.parametrize("wire", [b"-ERR secret\r\n", b"+OK\r\n", b"$999999999\r\n", b"$4\r\nabc\r\n", b"$-2\r\n", b"$01\r\nx\r\n"])
def test_invalid_backend_response_not_missing_value(monkeypatch, wire):
    client, server = socket.socketpair()
    def backend():
        with server:
            server.recv(4096)
            server.sendall(wire)
    worker = threading.Thread(target=backend)
    worker.start()
    monkeypatch.setattr(gateway.socket, "create_connection", lambda address, timeout: client)
    with pytest.raises(RuntimeError):
        gateway.redis_get("control:kill")
    worker.join(3)


def test_netguard_is_dedicated_and_atomic():
    raw = gateway.nft_script(policy(), True)
    assert raw.startswith("delete table inet qsys_redis_readonly\n")
    assert "meta skuid { 1001, 1002 } tcp dport 6379" in raw
    assert "flush" not in raw and "ip daddr" not in raw
    assert "policy accept" in raw
    p = policy(); p['blocked_uids'] = []
    with pytest.raises(ValueError):
        gateway.nft_script(p, False)


def test_gateway_has_no_business_import_dependency():
    import ast
    tree = ast.parse(Path(gateway.__file__).read_text())
    assert not any(isinstance(node, ast.ImportFrom) and (node.module or '').startswith('qsys') for node in ast.walk(tree))


def test_network_guard_updates_without_stopping_requires_dependents():
    unit = gateway.UNIT_FILES['qsys-redis-readonly-netguard.service']
    assert 'RemainAfterExit=yes' in unit
    assert 'ExecReload=/usr/bin/python3 -I /usr/local/libexec/qsys-redis-reader.py netguard' in unit


def test_uid_inventory_refuses_other_business_users(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(gateway.pwd, 'getpwuid', lambda uid: SimpleNamespace(pw_name='qsys-live-core'))
    with pytest.raises(ValueError, match='outside reviewed'):
        gateway.check_identities(policy())


@pytest.mark.parametrize('timeout', [0, -1, float('inf'), float('nan')])
def test_client_requires_finite_timeout(timeout):
    with pytest.raises(ValueError):
        ReadonlyTransport('/reader.sock', timeout)


@pytest.mark.parametrize('unknown_peer', [False, True])
@pytest.mark.skipif(not hasattr(socket, 'SO_PEERCRED'), reason='Linux peer credentials')
def test_real_peer_denial_and_backend_errors_are_not_null(monkeypatch, unknown_peer):
    backend = Mock(side_effect=OSError('backend secret must not escape'))
    monkeypatch.setattr(gateway, 'redis_get', backend)
    client, server = socket.socketpair()
    worker = threading.Thread(target=gateway.handle,
                              args=(server, policy(os.getuid() + int(unknown_peer))))
    worker.start()
    with client:
        client.sendall(request('control:kill'))
        raw = client.recv(4096)
        assert json.loads(raw) == {'protocol': 1, 'error': 'request failed'}
    worker.join(3)
    assert not worker.is_alive()
    assert backend.call_count == (0 if unknown_peer else 1)


def test_manifest_only_routes_reviewed_readers_to_gateway():
    manifest = json.loads(Path('deploy/component-releases.json').read_text())['components']
    readers = {'web', 'health-observer', 'daily-digest', 'reboot-drill'}
    for name, spec in manifest.items():
        lines = spec['dropin_lines']
        enabled = 'Environment=QSYS_REDIS_READER_SOCKET=/run/qsys-redis-reader.sock' in lines
        assert enabled == (name in readers)
        guarded = any(line.startswith('Requires=qsys-redis-readonly-netguard.service') for line in lines)
        assert guarded == (name in readers | {'service-checks'})
