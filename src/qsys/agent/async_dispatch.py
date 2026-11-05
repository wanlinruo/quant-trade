"""异步发送端口首阶段：默认关闭，仅供隔离命名空间接线。

这里没有QMT下单实现或生产CLI开关。传输ACK不是券商单号，未知尝试绝不自动重发。
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import stat
import threading
from typing import Protocol
import uuid

from pydantic import BaseModel, ConfigDict, Field

from qsys.agent.dispatch_store import DispatchStore
from qsys.agent.locking import single_instance
from qsys.live.config import StreamConfig
from qsys.live.messages import OrderCommand, RejectEvent
from qsys.live.safety import check_order_market
from qsys.live.validation_transport import ValidationTransport

_VALIDATION_TOKEN = object()


@dataclass(frozen=True)
class DispatchEnvelope:
    command_json: str
    command_sha256: str
    attempt_id: str
    session_nonce: str


class DispatchAcknowledgement(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    command_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    attempt_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    session_nonce: str = Field(pattern=r'^[0-9a-f]{32}$')
    receiver_instance: str = Field(min_length=1)
    received_at: datetime


class AsyncDispatchPort(Protocol):
    def send(self, envelope: DispatchEnvelope) -> DispatchAcknowledgement: ...
    def close(self) -> None:
        """仅关闭本地连接，不提交/撤销任何订单。"""
        ...


class PreDispatchRejection(ValueError):
    """确定发生在首次发送之前的门禁拒绝。"""


class AsyncOrderDispatcher:
    def __init__(self, store: DispatchStore, port: AsyncDispatchPort,
                 transport: ValidationTransport, streams: StreamConfig,
                 *, session_id: str, strategy_id: str, clock: Callable[[], datetime],
                 _validation_token: object = None):
        if _validation_token is not _VALIDATION_TOKEN or not isinstance(transport, ValidationTransport):
            raise ValueError('use the explicitly enabled isolated dispatcher factory')
        self.store, self._port, self._transport, self._streams = store, port, transport, streams
        self.session_id, self.strategy_id = session_id, strategy_id
        self._clock = clock
        self.session_nonce = uuid.uuid4().hex
        self._lock = threading.RLock()

    def matches(self, transport, streams: StreamConfig) -> bool:
        return transport is self._transport and streams == self._streams

    def close(self) -> None:
        with self._lock:
            try:
                self._port.close()
            finally:
                self.store.close()

    def _publish(self, row: dict) -> None:
        if row['state'] == 'rejected_before_send':
            command = OrderCommand.model_validate_json(row['command_json'])
            self._transport.append(self._streams.rejects, RejectEvent(
                client_order_id=command.client_order_id, symbol=command.symbol,
                side=command.side, unfilled_qty=command.qty, reason=row['reason'],
                at=datetime.fromisoformat(row['updated_at']),
            ).to_payload())
            return
        self._transport.append(self._streams.dispatch_status, self.store.event(row).to_payload())

    def reject_before_send(self, command: OrderCommand, reason: str) -> None:
        with self._lock:
            if self.replay_existing(command):
                return
            row = self.store.reject_before_send(command, uuid.uuid4().hex,
                self.session_nonce, reason, self._clock())
            self._publish(row)

    def replay_existing(self, command: OrderCommand) -> bool:
        with self._lock:
            row = self.store.existing(command)
            if row is None:
                return False
            if row['state'] == 'dispatching':
                row = self.store.update(command.client_order_id, state='unknown',
                    reason='unfinished_dispatch_requires_reconciliation', at=self._clock())
            self._publish(row)
            return True

    def dispatch(self, command: OrderCommand) -> None:
        with self._lock:
            if self.replay_existing(command):
                return
            now = self._clock()
            market = check_order_market(command.symbol, command.side)
            if not market.allowed:
                raise PreDispatchRejection(market.reason)
            if command.order_type != 'limit' or command.limit_price is None or not math.isfinite(command.limit_price) or command.limit_price <= 0:
                raise PreDispatchRejection('async validation requires an explicit finite positive limit')
            if ('schema_version' not in command.model_fields_set or command.schema_version != 2
                    or command.session_id != self.session_id or command.strategy_id != self.strategy_id):
                raise PreDispatchRejection('explicit current session/strategy required')
            if command.expires_at is None:
                raise PreDispatchRejection('explicit expiry required')
            try:
                valid_time = command.created_at <= now < command.expires_at
            except TypeError:
                raise PreDispatchRejection('timestamp timezone mismatch') from None
            if not valid_time:
                raise PreDispatchRejection('intent outside explicit time window')
            if command.side == 'buy' and self.store.has_unresolved():
                raise PreDispatchRejection('unresolved dispatch blocks new opening intents')
            row = self.store.claim(command, uuid.uuid4().hex, self.session_nonce, now)
            # Store and status publication failures must propagate without
            # falsely rejecting/ACKing the order stream or calling the port.
            self._publish(row)
            envelope = DispatchEnvelope(row['command_json'], row['command_sha256'],
                                        row['attempt_id'], row['session_nonce'])
            reason = ''
            ack = None
            try:
                candidate = self._port.send(envelope)
                if not isinstance(candidate, DispatchAcknowledgement):
                    reason = 'missing_or_invalid_transport_ack'
                elif (candidate.command_sha256, candidate.attempt_id, candidate.session_nonce) != (
                        envelope.command_sha256, envelope.attempt_id, envelope.session_nonce):
                    reason = 'transport_ack_binding_mismatch'
                else:
                    ack = candidate
            except Exception as exc:
                reason = 'adapter_error:' + type(exc).__name__
            receipt = ack.model_dump_json() if ack is not None else None
            row = self.store.update(command.client_order_id,
                state='transport_received' if ack is not None else 'unknown',
                reason=reason, at=self._clock(), receipt_json=receipt)
            self._publish(row)


def _private_root(root: Path, namespace: str) -> None:
    if root.is_symlink() or not root.is_dir() or not root.name.startswith('qsys-async-validation-'):
        raise ValueError('dedicated existing async validation root required')
    import os
    if os.name == 'nt':
        from qsys.agent.windows_validation import validate_windows_validation_root
        validate_windows_validation_root(root)
    elif stat.S_IMODE(root.stat().st_mode) & 0o077:
        raise ValueError('async validation root must be private')
    marker = root / '.qsys-async-validation'
    if marker.is_symlink() or marker.read_text(encoding='utf-8') != namespace:
        raise ValueError('async validation marker mismatch')
    for name in ('dispatch.sqlite3', 'dispatch.lock'):
        if (root / name).is_symlink():
            raise ValueError('async state/lock symlink rejected')


@contextmanager
def open_validation_dispatcher(*, enabled: bool = False, root: Path,
        transport: ValidationTransport, streams: StreamConfig,
        account_scope: str, strategy_id: str, session_id: str,
        adapter_factory: Callable[[], AsyncDispatchPort],
        clock: Callable[[], datetime] = datetime.now) -> Iterator[AsyncOrderDispatcher]:
    if not enabled:
        raise ValueError('async dispatch is disabled')
    if not isinstance(transport, ValidationTransport):
        raise ValueError('async dispatch requires isolated ValidationTransport')
    if not account_scope.startswith('validation:') or not strategy_id or not session_id:
        raise ValueError('explicit validation scope/strategy/session required')
    addresses = [streams.orders, streams.control, streams.fills, streams.rejects,
                 streams.order_status, streams.dispatch_status]
    if len(addresses) != len(set(addresses)):
        raise ValueError('dispatch and order/report stream addresses must be distinct')
    root = Path(root)
    _private_root(root, transport.namespace)
    scope = json.dumps([transport.namespace, account_scope, strategy_id], separators=(',', ':'))
    with single_instance(root / 'dispatch.lock'):
        store = DispatchStore(root / 'dispatch.sqlite3', scope=scope)
        dispatcher = None
        try:
            store.recover(clock())
            port = adapter_factory()
            dispatcher = AsyncOrderDispatcher(store, port, transport, streams,
                session_id=session_id, strategy_id=strategy_id, clock=clock,
                _validation_token=_VALIDATION_TOKEN)
            yield dispatcher
        finally:
            if dispatcher is not None:
                dispatcher.close()
            else:
                store.close()
