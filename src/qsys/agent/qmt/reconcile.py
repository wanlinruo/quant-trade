"""状态不明（unknown）委托的自动对账消解（#601，设计 §4.4）。

- 柜台查到同备注委托 → ``resolved_present``，真实状态由回报推进，不重发。
- 判定“确认未下单”须同时满足：已过指令有效期 + 宽限；执行器本实例启动早于指令创建
  且期间纪元未变；执行器心跳新鲜；两次相隔不少于 5 秒的新鲜查询都查不到。
  满足后自动发布拒单释放 Core 预留（维护者 2026-11-09 决定），依据写入账本 reason。
- 任一条件不满足（含查询失败、QMT 重建）一律保持 unknown。
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

import structlog

from qsys.agent.async_dispatch import AsyncOrderDispatcher
from qsys.agent.qmt.adapter import QmtIpcAdapter
from qsys.agent.qmt.memory import SharedMemory
from qsys.live.messages import OrderCommand

log = structlog.get_logger(__name__)
HEARTBEAT_FRESH_MS = 5000
SECOND_QUERY_AFTER_S = 5.0


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


class UnknownReconciler:
    def __init__(self, dispatcher: AsyncOrderDispatcher, adapter: QmtIpcAdapter,
                 memory: SharedMemory, *, grace_s: float = 60.0,
                 clock: Callable[[], datetime] = datetime.now) -> None:
        self._d, self._adapter, self._memory = dispatcher, adapter, memory
        self._grace = timedelta(seconds=grace_s)
        self._clock = clock
        self._absent: dict[str, tuple[datetime, str]] = {}

    def run_once(self) -> list[tuple[str, str]]:
        rows = self._d.store.unresolved()
        if not rows:
            self._absent.clear()
            return []
        now = self._clock()
        try:
            self._adapter.query_account_details()      # 新鲜 ORDER 查询并读入报告
        except Exception as exc:
            log.warning('qmt.reconcile_query_failed', error=type(exc).__name__)
            return []
        state = self._memory.executor_state()
        fresh = 0 <= _ms(now) - state.heartbeat_ms <= HEARTBEAT_FRESH_MS
        out: list[tuple[str, str]] = []
        for row in rows:
            command = OrderCommand.model_validate_json(row['command_json'])
            cid = command.client_order_id
            if self._adapter.find_order(cid) is not None:
                self._d.resolve(cid, 'present', f'order_found epoch={state.epoch}')
                self._absent.pop(cid, None)
                out.append((cid, 'present'))
                continue
            eligible = (fresh and command.expires_at is not None
                        # 柜台 ORDER 只含当日委托；跨日或存在非本系统备注的委托（可能是备注被截断）
                        # 时“查不到”不可信。
                        and command.created_at.date() == now.date()
                        and self._adapter.last_snapshot.get('foreign_orders') == 0
                        and now >= command.expires_at + self._grace
                        and 0 < state.started_ms <= _ms(command.created_at))
            if not eligible:
                self._absent.pop(cid, None)
                continue
            first = self._absent.get(cid)
            if first is None or first[1] != state.epoch:
                self._absent[cid] = (now, state.epoch)
                continue
            if (now - first[0]).total_seconds() < SECOND_QUERY_AFTER_S:
                continue
            reason = (f'orders_absent epoch={state.epoch} executor_started_ms={state.started_ms} '
                      f'queries={first[0].isoformat()},{now.isoformat()} '
                      f'expires_at={command.expires_at.isoformat()}')
            self._d.resolve(cid, 'absent', reason)
            self._absent.pop(cid, None)
            out.append((cid, 'absent'))
        return out
