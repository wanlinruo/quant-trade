from __future__ import annotations

import pytest

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
