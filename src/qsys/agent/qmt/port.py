"""``AsyncDispatchPort`` 实现：经共享内存把订单交给大 QMT 执行器（#601）。

执行器回执 ``accepted``/``duplicate`` 只表示“已调用或已存在”，不是券商受理；
``rejected`` 表示未调用券商接口，转为发送前拒单；无回执、签名或绑定不符、执行器
调用异常一律抛错，由账本记为 unknown，绝不重发。
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from datetime import datetime

from qsys.agent.async_dispatch import DispatchAcknowledgement, DispatchEnvelope, ExecutorRejected
from qsys.agent.qmt import protocol
from qsys.agent.qmt.memory import SharedMemory

__all__ = ['ExecutorCallUnknown', 'ExecutorRejected', 'QmtIpcPort']

# 这些拒绝原因说明帧本身不可信或与已处理尝试冲突，不能证明“从未下单”。
_UNSAFE_REJECTIONS = frozenset({'attempt_conflict', 'fields', 'identity'})


class ExecutorCallUnknown(RuntimeError):
    """执行器已尝试原生调用但结果未知（调用异常），必须走对账。"""


def _epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


class QmtIpcPort:
    def __init__(self, memory: SharedMemory, *, run_id: str, key: bytes,
                 clock_ms: Callable[[], int], sleep=time.sleep,
                 receipt_timeout_s: float = 6.0) -> None:
        # 回执等待须长于传输有效期（5 秒），否则执行器仍可能在网关记 unknown 后执行。
        self._memory, self._run_id, self._key = memory, run_id, key
        self._clock_ms, self._sleep, self._timeout = clock_ms, sleep, receipt_timeout_s
        self._seq = 0
        self._session_nonce = uuid.uuid4().hex
        self.last_epoch: str | None = None

    def _exchange(self, kind: str, command: dict, attempt_id: str, session_nonce: str) -> dict:
        self._seq += 1
        # 每条指令前刷新网关心跳：09:25 突发多单时主循环一轮可能超过 10 秒。
        self._memory.beat(self._clock_ms())
        raw = protocol.build_request(run_id=self._run_id, seq=self._seq, kind=kind,
                                     command=command, attempt_id=attempt_id,
                                     session_nonce=session_nonce, now_ms=self._clock_ms(),
                                     key=self._key)
        reply = self._memory.exchange(raw, self._timeout, self._sleep)
        if reply is None:
            raise TimeoutError('executor receipt timeout')
        receipt = protocol.parse_receipt(reply, self._key)
        sent = json.loads(raw)
        if (receipt.get('attempt_id'), receipt.get('command_sha256'), receipt.get('run_id')) != (
                attempt_id, sent['command_sha256'], self._run_id):
            raise ValueError('receipt binding mismatch')
        if receipt.get('status') not in protocol.RECEIPTS:
            raise ValueError('receipt status')
        self.last_epoch = receipt.get('epoch')
        return receipt

    def send(self, envelope: DispatchEnvelope) -> DispatchAcknowledgement:
        order = json.loads(envelope.command_json)
        command = dict(client_order_id=order['client_order_id'], symbol=order['symbol'],
                       side=order['side'], qty=order['qty'], order_type=order['order_type'],
                       limit_price=None if order['limit_price'] is None else float(order['limit_price']),
                       expires_at_ms=_epoch_ms(datetime.fromisoformat(order['expires_at'])))
        receipt = self._exchange('place', command, envelope.attempt_id, envelope.session_nonce)
        if receipt['status'] == 'rejected':
            if receipt.get('reason') in _UNSAFE_REJECTIONS:
                raise ValueError('executor rejected frame identity: ' + str(receipt.get('reason')))
            raise ExecutorRejected(str(receipt.get('reason')))
        result = receipt.get('result') or {}
        if result.get('status') == 'call_exception':
            raise ExecutorCallUnknown('native call raised inside executor')
        return DispatchAcknowledgement(
            command_sha256=envelope.command_sha256, attempt_id=envelope.attempt_id,
            session_nonce=envelope.session_nonce, receiver_instance='qmt:' + str(receipt.get('epoch')),
            received_at=datetime.now())

    def request(self, kind: str, command: dict | None = None) -> dict:
        """cancel / cancel_all / snapshot / ping；每次新 attempt，失败抛错。"""
        if kind == 'place':
            raise ValueError('place goes through send()')
        return self._exchange(kind, command or {}, uuid.uuid4().hex, self._session_nonce)

    def close(self) -> None:
        """不关闭共享内存：映射由网关进程持有到退出。"""
