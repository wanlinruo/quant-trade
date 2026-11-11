"""状态不明（unknown）委托的对账消解（#601，设计 §4.4）。

- 柜台查到同备注委托 → ``resolved_present``，真实状态由回报推进，不重发。
- 判定“确认未下单”须同时满足：已过指令有效期 + 宽限；执行器本实例启动早于指令创建
  且期间纪元未变；执行器心跳新鲜；两次相隔不少于 5 秒的新鲜查询都查不到。
  满足后自动发布拒单释放 Core 预留（维护者 2026-11-09 决定），依据写入账本 reason。
- 任一条件不满足（含查询失败、QMT 重建）一律保持 unknown。
- 人工对账请求（F5 前置）：执行器在指令创建后重启时自动判定永久不可用。维护者在私有
  请求目录放入 JSON 请求，只放宽“执行器启动早于指令”这一条，其余条件与两次查询照旧；
  ``present`` 只记事实不释放预留；跨日的 absent 一律拒绝（柜台委托查询只含当日）。
  结果写入同名 ``.result.json``，请求改名 ``.done``，不重放。
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re

import structlog

from qsys.agent.async_dispatch import AsyncOrderDispatcher
from qsys.agent.qmt.adapter import QmtIpcAdapter
from qsys.agent.qmt.memory import SharedMemory
from qsys.live.messages import OrderCommand

log = structlog.get_logger(__name__)
HEARTBEAT_FRESH_MS = 5000
SECOND_QUERY_AFTER_S = 5.0
REQUEST_MAX_AGE = timedelta(hours=1)
_REQUEST_KEYS = {'client_order_id', 'outcome', 'operator', 'evidence', 'requested_at'}
_OPERATOR = re.compile(r'^[A-Za-z0-9_.-]{1,40}$')


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


class ManualRequest:
    def __init__(self, path: Path, body: dict) -> None:
        self.path, self.body = path, body
        self.cid: str = body['client_order_id']
        self.outcome: str = body['outcome']

    @property
    def tag(self) -> str:
        digest = hashlib.sha256(self.body['evidence'].encode()).hexdigest()[:16]
        return f"manual operator={self.body['operator']} evidence_sha256={digest}"


def _parse(path: Path) -> dict | None:
    try:
        body = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(body, dict) or set(body) != _REQUEST_KEYS:
        return None
    if body['outcome'] not in {'absent', 'present'} or not isinstance(body['client_order_id'], str):
        return None
    if not isinstance(body['operator'], str) or not _OPERATOR.fullmatch(body['operator']):
        return None
    evidence = body['evidence']
    if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 500:
        return None
    try:
        datetime.fromisoformat(body['requested_at'])
    except (TypeError, ValueError):
        return None
    return body


class UnknownReconciler:
    def __init__(self, dispatcher: AsyncOrderDispatcher, adapter: QmtIpcAdapter,
                 memory: SharedMemory, *, grace_s: float = 60.0,
                 clock: Callable[[], datetime] = datetime.now,
                 requests: Path | None = None) -> None:
        self._d, self._adapter, self._memory = dispatcher, adapter, memory
        self._grace = timedelta(seconds=grace_s)
        self._clock = clock
        self._requests = requests
        self._absent: dict[str, tuple[datetime, str]] = {}

    # -- manual requests ------------------------------------------------------
    def _finish(self, request: ManualRequest, status: str, reason: str = '') -> None:
        result = {'status': status, 'reason': reason, 'client_order_id': request.cid,
                  'outcome': request.outcome, 'request': request.body,
                  'finished_at': self._clock().isoformat()}
        target = request.path.with_suffix('.result.json')
        with target.open('x', encoding='utf-8') as out:
            json.dump(result, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        request.path.rename(request.path.with_suffix('.done'))
        log.info('qmt.manual_reconcile', status=status, reason=reason, outcome=request.outcome)

    def _load_requests(self, now: datetime) -> dict[str, ManualRequest]:
        if self._requests is None or not self._requests.is_dir():
            return {}
        out: dict[str, ManualRequest] = {}
        for path in sorted(self._requests.glob('*.json')):
            if path.name.endswith('.result.json'):
                continue
            body = _parse(path)
            if body is None:
                self._finish(ManualRequest(path, {'client_order_id': '', 'outcome': '',
                                                  'evidence': '', 'operator': ''}),
                             'refused', 'invalid_request')
                continue
            request = ManualRequest(path, body)
            requested = datetime.fromisoformat(body['requested_at'])
            if not timedelta(minutes=-5) <= now - requested <= REQUEST_MAX_AGE:
                self._finish(request, 'refused', 'request_expired')
            elif request.cid in out:
                self._finish(request, 'refused', 'duplicate_request')
            else:
                out[request.cid] = request
        return out

    # -- reconciliation -------------------------------------------------------
    def run_once(self) -> list[tuple[str, str]]:
        rows = self._d.store.unresolved()
        manual = self._load_requests(self._clock())
        unresolved = {OrderCommand.model_validate_json(r['command_json']).client_order_id
                      for r in rows}
        for cid in [c for c in manual if c not in unresolved]:
            self._finish(manual.pop(cid), 'refused', 'not_unknown')
        if not rows:
            self._absent.clear()
            return []
        try:
            self._adapter.query_account_details()      # 新鲜 ORDER 查询并读入报告
        except Exception as exc:
            log.warning('qmt.reconcile_query_failed', error=type(exc).__name__)
            return []
        # 执行器在下一次定时回调中应答并刷新心跳；时刻须在查询之后取，否则心跳总是
        # “晚于 now”而永远判为不新鲜（#601 2026-11-10 F4 现场发现）。
        now = self._clock()
        state = self._memory.executor_state()
        fresh = 0 <= _ms(now) - state.heartbeat_ms <= HEARTBEAT_FRESH_MS
        out: list[tuple[str, str]] = []
        for row in rows:
            command = OrderCommand.model_validate_json(row['command_json'])
            cid = command.client_order_id
            request = manual.get(cid)
            if self._adapter.find_order(cid) is not None:
                self._d.resolve(cid, 'present', f'order_found epoch={state.epoch}')
                self._absent.pop(cid, None)
                out.append((cid, 'present'))
                if request is not None:
                    self._finish(request, 'applied' if request.outcome == 'present' else 'refused',
                                 '' if request.outcome == 'present' else 'order_found')
                continue
            if request is not None and request.outcome == 'present':
                # 只记“已在柜台”事实，不释放预留；真实状态仍待回报推进。
                self._d.resolve(cid, 'present', request.tag)
                self._absent.pop(cid, None)
                out.append((cid, 'present'))
                self._finish(request, 'applied')
                continue
            same_day = command.created_at.date() == now.date()
            if request is not None and not same_day:
                self._finish(request, 'refused', 'cross_day_requires_trade_evidence')
                request = None
            base = (fresh and command.expires_at is not None
                    # 柜台 ORDER 只含当日委托；跨日或存在非本系统备注的委托（可能是备注被截断）
                    # 时“查不到”不可信。
                    and same_day
                    and self._adapter.last_snapshot.get('foreign_orders') == 0
                    and now >= command.expires_at + self._grace)
            automatic = base and 0 < state.started_ms <= _ms(command.created_at)
            eligible = automatic or (base and request is not None)
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
            if not automatic:
                reason = f'{request.tag} {reason}'
            self._d.resolve(cid, 'absent', reason)
            self._absent.pop(cid, None)
            out.append((cid, 'absent'))
            if request is not None:
                self._finish(request, 'applied')
        return out
