from __future__ import annotations

import socketserver
import threading
import time
from types import SimpleNamespace

import pytest
import redis
from redis.exceptions import TimeoutError as RedisTimeoutError

from qsys.live.config import DeployConfig, RedisConfig
from qsys.live.redis_transport import RedisTransport


class Redis5Client:
    def __init__(self, *, idle: int = 6_000) -> None:
        self.idle = idle
        self.claimed: list[bytes] = []

    def xautoclaim(self, *args, **kwargs):
        raise RuntimeError("unknown command `XAUTOCLAIM`")

    def xpending_range(self, *args):
        return [{"message_id": b"1-0", "time_since_delivered": self.idle}]

    def xclaim(self, *args, message_ids, **kwargs):
        self.claimed = message_ids
        return [(b"1-0", {b"kind": b"order"})]


def test_pending_falls_back_to_xclaim_on_redis_5() -> None:
    client = Redis5Client()

    messages = RedisTransport(client).pending("orders", "agent")

    assert client.claimed == [b"1-0"]
    assert messages[0].id == "1-0"
    assert messages[0].payload == {"kind": "order"}


def test_pending_does_not_claim_fresh_redis_5_messages() -> None:
    client = Redis5Client(idle=4_999)

    assert RedisTransport(client).pending("orders", "agent") == []
    assert client.claimed == []


def test_pending_does_not_hide_unrelated_redis_errors() -> None:
    client = Redis5Client()
    client.xautoclaim = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("connection lost")
    )

    with pytest.raises(RuntimeError, match="connection lost"):
        RedisTransport(client).pending("orders", "agent")


def test_from_config_bounds_connect_and_short_command_waits(monkeypatch) -> None:
    created = {}

    def fake_redis(**kwargs):
        created.update(kwargs)
        return object()

    monkeypatch.setattr(redis, "Redis", fake_redis)
    cfg = DeployConfig(redis=RedisConfig(
        connect_timeout_seconds=2.5, command_timeout_seconds=4.0,
    ))

    RedisTransport.from_config(cfg)

    assert created["socket_connect_timeout"] == 2.5
    assert created["socket_timeout"] == 4.0
    assert created["retry"]._retries == 0
    assert created["health_check_interval"] == 30


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("connect_timeout_seconds", 0.0),
        ("command_timeout_seconds", 0.0),
        ("connect_timeout_seconds", float("inf")),
        ("command_timeout_seconds", float("nan")),
    ],
)
def test_from_config_rejects_an_unbounded_timeout(monkeypatch, field, value) -> None:
    monkeypatch.setattr(
        redis, "Redis", lambda **kwargs: pytest.fail("client must not be created"),
    )
    values = {field: value}
    cfg = DeployConfig(redis=RedisConfig(**values))

    with pytest.raises(ValueError, match="有限的正数"):
        RedisTransport.from_config(cfg)


def test_read_group_is_a_non_blocking_short_poll() -> None:
    calls = []
    client = SimpleNamespace(
        xreadgroup=lambda *args, **kwargs: calls.append((args, kwargs)) or []
    )

    assert RedisTransport(client).read_group("orders", "agent", "agent-1") == []

    assert calls == [(('agent', 'agent-1', {'orders': '>'}), {'count': 10})]
    assert "block" not in calls[0][1]


def test_a_failed_command_does_not_poison_the_next_poll() -> None:
    responses = iter([TimeoutError("slow Redis"), b"alive"])

    def get(_key):
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result

    transport = RedisTransport(SimpleNamespace(get=get))

    with pytest.raises(TimeoutError, match="slow Redis"):
        transport.get_key("heartbeat")
    assert transport.get_key("heartbeat") == "alive"


def test_a_half_open_server_is_bounded_by_the_command_timeout() -> None:
    release = threading.Event()

    class NoResponse(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(4096)
            release.wait(2)

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with Server(("127.0.0.1", 0), NoResponse) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cfg = DeployConfig(redis=RedisConfig(
            host="127.0.0.1", port=server.server_address[1],
            connect_timeout_seconds=0.2, command_timeout_seconds=0.1,
        ))
        transport = RedisTransport.from_config(cfg)
        started = time.monotonic()
        try:
            with pytest.raises(RedisTimeoutError, match="Timeout"):
                transport.get_key("heartbeat")
            assert time.monotonic() - started < 1.0
        finally:
            release.set()
            server.shutdown()
            thread.join(timeout=1)
