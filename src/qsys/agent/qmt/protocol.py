"""大 QMT 信号通道协议：帧、HMAC 与共享内存布局（#601）。

只用标准库。QMT 内的 ``executor_source.py`` 复制同名常量，测试断言两边一致。
"""
from __future__ import annotations

import hashlib
import hmac
import json

PROTOCOL = 'qsys.qmt-signal.v1'
MAP_SIZE = 262144
REQUEST_OFFSET = 0
RESPONSE_OFFSET = 4096
# 网关写：心跳 u64 @+0、KILL u32 @+8、已消费报告序号 u64 @+16
CONTROL_OFFSET = 8192
# 执行器写：纪元 32 字节 ASCII @+0、心跳 u64 @+32、最新报告序号 u64 @+40、环满 u32 @+48、
# 本实例启动时刻 u64 @+56
EXECUTOR_OFFSET = 8448
COORDINATOR_MARKER_OFFSET = 8704
SCHEDULED_MARKER_OFFSET = 9216
RING_OFFSET = 16384
RING_SLOT_SIZE = 1024
RING_SLOTS = 240
FRAME_MAX = 3800
TRANSPORT_TTL_MS = 5000

KINDS = ('place', 'cancel', 'cancel_all', 'snapshot', 'ping')
RECEIPTS = ('accepted', 'rejected', 'duplicate')
REQUEST_FIELDS = frozenset({'protocol', 'run_id', 'seq', 'kind', 'attempt_id', 'session_nonce',
                            'command_sha256', 'command_json', 'issued_at_ms', 'expires_at_ms',
                            'mac'})


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def sign(value: dict, key: bytes) -> str:
    return hmac.new(key, canonical(value), 'sha256').hexdigest()


def verify(obj: dict, key: bytes) -> bool:
    body = dict(obj)
    mac = body.pop('mac', None)
    return isinstance(mac, str) and hmac.compare_digest(mac, sign(body, key))


def build_request(*, run_id: str, seq: int, kind: str, command: dict, attempt_id: str,
                  session_nonce: str, now_ms: int, key: bytes) -> bytes:
    if kind not in KINDS:
        raise ValueError('request_kind')
    command_json = canonical(command).decode('ascii')
    row = dict(protocol=PROTOCOL, run_id=run_id, seq=seq, kind=kind, attempt_id=attempt_id,
               session_nonce=session_nonce,
               command_sha256=hashlib.sha256(command_json.encode('ascii')).hexdigest(),
               command_json=command_json, issued_at_ms=now_ms,
               expires_at_ms=now_ms + TRANSPORT_TTL_MS)
    row['mac'] = sign(row, key)
    raw = canonical(row)
    if len(raw) > FRAME_MAX:
        raise ValueError('request_size')
    return raw


def parse_receipt(raw: bytes, key: bytes) -> dict:
    obj = json.loads(raw.decode('ascii'))
    if not isinstance(obj, dict) or not verify(obj, key):
        raise ValueError('receipt_signature')
    if obj.get('protocol') != PROTOCOL:
        raise ValueError('receipt_protocol')
    return obj
