"""每轮主循环对大 QMT 执行器的同步：网关心跳、KILL 字与 unknown 对账（#601）。"""
from __future__ import annotations

from collections.abc import Callable

import structlog

from qsys.agent.qmt.memory import SharedMemory

log = structlog.get_logger(__name__)


class QmtLink:
    def __init__(self, memory: SharedMemory, reconciler, *, clock_ms: Callable[[], int]) -> None:
        self._memory, self._reconciler, self._clock_ms = memory, reconciler, clock_ms

    def __call__(self, kill_engaged: bool) -> None:
        # 心跳与 KILL 先写：对账失败不能让执行器误判网关失联，也不能延迟急停。
        self._memory.beat(self._clock_ms())
        self._memory.set_kill(kill_engaged)
        try:
            for client_order_id, outcome in self._reconciler.run_once():
                log.warning('qmt.unknown_resolved', client_order_id=client_order_id,
                            outcome=outcome)
        except Exception as exc:
            log.error('qmt.reconcile_failed', error=type(exc).__name__)
