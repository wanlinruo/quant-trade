"""隔离预生产传输：包括运行时硬编码的kill/intents在内，所有Redis地址统一加测试前缀。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from qsys.live.transport import StreamMessage, Transport


@dataclass(frozen=True, slots=True)
class ValidationTransport(Transport):
    inner: Transport
    namespace: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"component-validation:[a-z0-9][a-z0-9:_-]{0,95}", self.namespace):
            raise ValueError("隔离命名空间必须以component-validation:开头且只含小写字母/数字/:/_/-")

    def _key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    def append(self, stream: str, payload: dict[str, str]) -> str:
        return self.inner.append(self._key(stream), payload)

    def ensure_group(self, stream: str, group: str, start_id: str = "$") -> None:
        self.inner.ensure_group(self._key(stream), group, start_id)

    def reset_group(self, stream: str, group: str, start_id: str = "$") -> None:
        self.inner.reset_group(self._key(stream), group, start_id)

    def read_group(self, stream: str, group: str, consumer: str, count: int = 10) -> list[StreamMessage]:
        return self.inner.read_group(self._key(stream), group, consumer, count=count)

    def ack(self, stream: str, group: str, message_id: str) -> None:
        self.inner.ack(self._key(stream), group, message_id)

    def pending(self, stream: str, group: str) -> list[StreamMessage]:
        return self.inner.pending(self._key(stream), group)

    def set_key(self, key: str, value: str) -> None:
        self.inner.set_key(self._key(key), value)

    def get_key(self, key: str) -> str | None:
        return self.inner.get_key(self._key(key))

    def delete_key(self, key: str) -> None:
        self.inner.delete_key(self._key(key))
