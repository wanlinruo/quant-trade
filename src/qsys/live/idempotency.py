"""幂等去重。

client_order_id 由核心侧生成，Windows 侧维护已处理集合。
重复指令直接回放旧结果——网络重传永不导致重复下单，
这是实盘系统最不能出的错。

失败的指令不记入已处理集合，允许重试。
"""

from __future__ import annotations

from collections.abc import Callable

import structlog

from qsys.live.transport import Transport

log = structlog.get_logger(__name__)


class IdempotencyGuard:
    def __init__(self, transport: Transport, *, prefix: str = "idem:") -> None:
        self._transport = transport
        self._prefix = prefix

    def _key(self, client_order_id: str) -> str:
        return f"{self._prefix}{client_order_id}"

    def seen(self, client_order_id: str) -> bool:
        return self._transport.get_key(self._key(client_order_id)) is not None

    def remember(self, client_order_id: str, result: str) -> None:
        self._transport.set_key(self._key(client_order_id), result)

    def recall(self, client_order_id: str) -> str | None:
        return self._transport.get_key(self._key(client_order_id))

    def process_once(
        self, client_order_id: str, handler: Callable[[], str]
    ) -> tuple[str, bool]:
        """执行一次并记住结果；已处理过则回放，返回 (结果, 是否为重放)。"""
        previous = self.recall(client_order_id)
        if previous is not None:
            log.info("idempotency.replayed", client_order_id=client_order_id)
            return previous, True

        result = handler()  # 失败时异常上抛，不记入已处理集合
        self.remember(client_order_id, result)
        return result, False
