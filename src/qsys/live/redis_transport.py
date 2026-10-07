"""Redis Stream 传输实现。

redis-py 在方法内延迟导入 —— 本模块只在真实部署时被使用，
单元测试一律用 MemoryTransport。

公共客户端只承载短命令。``read_group`` 故意不传 ``block``，所以也属于短轮询；
阻塞式 Streams 消费必须另建客户端，并让 socket timeout 大于 BLOCK 时长，不能复用
这里的命令超时。
"""

from __future__ import annotations

from math import isfinite
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
        from redis.backoff import NoBackoff
        from redis.retry import Retry

        connect_timeout = cfg.redis.connect_timeout_seconds
        command_timeout = cfg.redis.command_timeout_seconds
        if (
            not isfinite(connect_timeout) or connect_timeout <= 0
            or not isfinite(command_timeout) or command_timeout <= 0
        ):
            raise ValueError("Redis 连接与命令超时必须是有限的正数秒")
        no_retry = Retry(NoBackoff(), 0)
        client = redis.Redis(
            host=cfg.redis.host,
            port=cfg.redis.port,
            password=cfg.redis.password or None,
            db=cfg.redis.db,
            ssl=cfg.redis.tls,
            decode_responses=False,
            socket_keepalive=True,
            socket_connect_timeout=connect_timeout,
            socket_timeout=command_timeout,
            # 超时立即交还给业务循环，让其落闸、告警或降级。redis-py 会丢弃
            # 出错连接，下一轮命令从连接池重连；在这里隐式重试只会扩大停顿。
            retry=no_retry,
            health_check_interval=30,
            # The Windows bridge still runs a RESP2-only Redis-compatible
            # server.  Redis Streams used here need RESP2, not HELLO/RESP3.
            protocol=2,
        )
        return cls(client)

    def append(self, stream: str, payload: dict[str, str]) -> str:
        return _decode(self._r.xadd(stream, payload))

    def ensure_group(self, stream: str, group: str, start_id: str = "$") -> None:
        try:
            self._r.xgroup_create(stream, group, id=start_id, mkstream=True)
        except Exception as exc:  # BUSYGROUP：组已存在，属正常
            if "BUSYGROUP" not in str(exc):
                raise

    def reset_group(self, stream: str, group: str, start_id: str = "$") -> None:
        # Quote responses are request-scoped and worthless after their caller
        # has restarted.  SETID skips the historical fan-out backlog; deleting
        # the sole logical consumer also clears a crash-left pending set.
        self._r.xgroup_setid(stream, group, start_id)
        try:
            self._r.xgroup_delconsumer(stream, group, group.removeprefix("paper-quotes-"))
        except Exception as exc:
            if "NOGROUP" not in str(exc):
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
        # 不加 block：公共客户端的 socket_timeout 是短命令上限。调用方已有
        # 轮询节奏，因此空队列应立即返回，不让长轮询占住心跳线程。
        return self._to_messages(
            self._r.xreadgroup(group, consumer, {stream: ">"}, count=count)
        )

    def ack(self, stream: str, group: str, message_id: str) -> None:
        self._r.xack(stream, group, message_id)

    def pending(self, stream: str, group: str) -> list[StreamMessage]:
        # XREADGROUP ... 0 只能读取“当前 consumer 自己”的 PEL，无法恢复崩溃
        # 进程留下的消息。XAUTOCLAIM 才能把旧 consumer 的 pending 转交回来。
        try:
            response = self._r.xautoclaim(
                stream, group, "recovery", min_idle_time=5_000,
                start_id="0-0", count=1000,
            )
            entries = response[1] if response and len(response) > 1 else []
        except Exception as exc:
            # Redis 5 has Streams and XCLAIM, but XAUTOCLAIM was only added in
            # Redis 6.2.  The deployed RESP2-compatible server is Redis-5-like.
            if "unknown command" not in str(exc).lower() or "xautoclaim" not in str(exc).lower():
                raise
            pending = self._r.xpending_range(stream, group, "-", "+", 1000)
            message_ids = []
            for item in pending:
                idle = item.get("time_since_delivered", item.get(b"time_since_delivered", 0))
                if int(idle) < 5_000:
                    continue
                message_id = item.get("message_id", item.get(b"message_id"))
                if message_id is not None:
                    message_ids.append(message_id)
            entries = (
                self._r.xclaim(
                    stream, group, "recovery", min_idle_time=5_000,
                    message_ids=message_ids,
                )
                if message_ids else []
            )
        out: list[StreamMessage] = []
        for message_id, fields in entries:
            out.append(StreamMessage(
                _decode(message_id),
                {_decode(k): _decode(v) for k, v in fields.items()},
            ))
        return out

    def set_key(self, key: str, value: str) -> None:
        self._r.set(key, value)

    def get_key(self, key: str) -> str | None:
        value = self._r.get(key)
        return None if value is None else _decode(value)

    def delete_key(self, key: str) -> None:
        self._r.delete(key)
