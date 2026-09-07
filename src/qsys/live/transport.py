"""传输层抽象。

必须用 Stream 而非 pub/sub —— pub/sub 断线即丢且不可追溯，
断线期间的成交回报会永久丢失。Stream 有持久化与消费确认，
核心侧重启后能从上次位置继续消费。

MemoryTransport 是全部实盘测试的基础：单元测试不得连接真实 Redis。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from itertools import count


@dataclass(frozen=True, slots=True)
class StreamMessage:
    id: str
    payload: dict[str, str]


class Transport(ABC):
    @abstractmethod
    def append(self, stream: str, payload: dict[str, str]) -> str: ...

    @abstractmethod
    def ensure_group(self, stream: str, group: str) -> None: ...

    @abstractmethod
    def read_group(
        self, stream: str, group: str, consumer: str, count: int = 10
    ) -> list[StreamMessage]: ...

    @abstractmethod
    def ack(self, stream: str, group: str, message_id: str) -> None: ...

    @abstractmethod
    def pending(self, stream: str, group: str) -> list[StreamMessage]: ...

    @abstractmethod
    def set_key(self, key: str, value: str) -> None: ...

    @abstractmethod
    def get_key(self, key: str) -> str | None: ...

    @abstractmethod
    def delete_key(self, key: str) -> None: ...


class MemoryTransport(Transport):
    """语义对齐 Redis Stream 的内存实现。"""

    def __init__(self) -> None:
        self._streams: dict[str, list[StreamMessage]] = {}
        self._cursors: dict[tuple[str, str], int] = {}
        self._pending: dict[tuple[str, str], list[str]] = {}
        self._keys: dict[str, str] = {}
        self._seq = count(1)

    def append(self, stream: str, payload: dict[str, str]) -> str:
        message = StreamMessage(f"{next(self._seq):020d}-0", dict(payload))
        self._streams.setdefault(stream, []).append(message)
        return message.id

    def ensure_group(self, stream: str, group: str) -> None:
        key = (stream, group)
        if key in self._cursors:
            return
        # 与 XGROUP CREATE ... $ 一致：新组只看新消息
        self._cursors[key] = len(self._streams.get(stream, []))
        self._pending[key] = []

    def read_group(
        self, stream: str, group: str, consumer: str, count: int = 10
    ) -> list[StreamMessage]:
        key = (stream, group)
        if key not in self._cursors:
            raise KeyError(f"no such group {group!r} on {stream!r}")
        messages = self._streams.get(stream, [])
        start = self._cursors[key]
        batch = messages[start : start + count]
        self._cursors[key] = start + len(batch)
        self._pending[key].extend(m.id for m in batch)
        return list(batch)

    def ack(self, stream: str, group: str, message_id: str) -> None:
        key = (stream, group)
        if key in self._pending and message_id in self._pending[key]:
            self._pending[key].remove(message_id)

    def pending(self, stream: str, group: str) -> list[StreamMessage]:
        key = (stream, group)
        ids = set(self._pending.get(key, []))
        return [m for m in self._streams.get(stream, []) if m.id in ids]

    def set_key(self, key: str, value: str) -> None:
        self._keys[key] = value

    def get_key(self, key: str) -> str | None:
        return self._keys.get(key)

    def delete_key(self, key: str) -> None:
        self._keys.pop(key, None)

    # --- 测试自省。生产代码不得使用 ---

    def all_messages(self, stream: str) -> list[StreamMessage]:
        """返回流中全部消息，绕过消费组语义。仅供测试断言使用。"""
        return list(self._streams.get(stream, []))
