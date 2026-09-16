"""Redis Stream 传输实现。

redis-py 在方法内延迟导入 —— 本模块只在真实部署时被使用，
单元测试一律用 MemoryTransport。
"""

from __future__ import annotations

from typing import Any

from qsys.live.config import DeployConfig
from qsys.live.transport import StreamMessage, Transport


def _decode(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


class RedisTransport(Transport):
    def __init__(self, client: Any) -> None:
        self._r = client

    @classmethod
    def from_config(cls, cfg: DeployConfig) -> RedisTransport:
        import redis

        client = redis.Redis(
            host=cfg.redis.host,
            port=cfg.redis.port,
            password=cfg.redis.password or None,
            db=cfg.redis.db,
            ssl=cfg.redis.tls,
            decode_responses=False,
            socket_keepalive=True,
            # The Windows bridge still runs a RESP2-only Redis-compatible
            # server.  Redis Streams used here need RESP2, not HELLO/RESP3.
            protocol=2,
        )
        return cls(client)

    def append(self, stream: str, payload: dict[str, str]) -> str:
        return _decode(self._r.xadd(stream, payload))

    def ensure_group(self, stream: str, group: str) -> None:
        try:
            self._r.xgroup_create(stream, group, id="$", mkstream=True)
        except Exception as exc:  # BUSYGROUP：组已存在，属正常
            if "BUSYGROUP" not in str(exc):
                raise

    def _to_messages(self, response: Any) -> list[StreamMessage]:
        out: list[StreamMessage] = []
        for _, entries in response or []:
            for message_id, fields in entries:
                out.append(
                    StreamMessage(
                        _decode(message_id),
                        {_decode(k): _decode(v) for k, v in fields.items()},
                    )
                )
        return out

    def read_group(
        self, stream: str, group: str, consumer: str, count: int = 10
    ) -> list[StreamMessage]:
        return self._to_messages(
            self._r.xreadgroup(group, consumer, {stream: ">"}, count=count)
        )

    def ack(self, stream: str, group: str, message_id: str) -> None:
        self._r.xack(stream, group, message_id)

    def pending(self, stream: str, group: str) -> list[StreamMessage]:
        return self._to_messages(
            self._r.xreadgroup(group, "recovery", {stream: "0"}, count=1000)
        )

    def set_key(self, key: str, value: str) -> None:
        self._r.set(key, value)

    def get_key(self, key: str) -> str | None:
        value = self._r.get(key)
        return None if value is None else _decode(value)

    def delete_key(self, key: str) -> None:
        self._r.delete(key)
