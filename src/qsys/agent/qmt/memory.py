"""网关侧共享内存：请求/回执、控制字与报告环游标（#601）。

布局见 ``protocol``。网关写请求区、控制区（心跳、KILL、已消费报告序号）与协调标记；
执行器写回执区、执行器区与报告环。写入顺序固定为“先数据、后代号/序号”。
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass

from qsys.agent.qmt import protocol as p


class ReportGap(RuntimeError):
    """报告环出现序号缺口：可能丢失回报，网关必须停开仓并告警。"""


@dataclass(frozen=True, slots=True)
class ExecutorState:
    epoch: str
    heartbeat_ms: int
    latest_seq: int
    ring_full: bool
    started_ms: int


def _get(buf, offset: int, size: int) -> int:
    return int.from_bytes(bytes(buf[offset:offset + size]), 'little')


def _put(buf, offset: int, size: int, value: int) -> None:
    buf[offset:offset + size] = int(value).to_bytes(size, 'little')


class SharedMemory:
    def __init__(self, buf) -> None:
        if len(buf) < p.MAP_SIZE:
            raise ValueError('shared memory too small')
        self.buf = buf
        self._generation = _get(buf, p.REQUEST_OFFSET, 4)

    # -- markers ------------------------------------------------------------
    def write_coordinator_marker(self, run_id: str) -> None:
        marker = b'QSYS_QMT_SIGNAL:' + run_id.encode('ascii')
        self.buf[p.COORDINATOR_MARKER_OFFSET:p.COORDINATOR_MARKER_OFFSET + len(marker)] = marker
        if bytes(self.buf[p.COORDINATOR_MARKER_OFFSET:p.COORDINATOR_MARKER_OFFSET + len(marker)]) != marker:
            raise RuntimeError('coordinator marker readback failed')

    def scheduled(self, run_id: str) -> bool:
        marker = b'QSYS_QMT_SIGNAL_SCHEDULED:' + run_id.encode('ascii')
        return bytes(self.buf[p.SCHEDULED_MARKER_OFFSET:p.SCHEDULED_MARKER_OFFSET + len(marker)]) == marker

    # -- control words --------------------------------------------------------
    def beat(self, now_ms: int) -> None:
        _put(self.buf, p.CONTROL_OFFSET, 8, now_ms)

    def set_kill(self, engaged: bool) -> None:
        _put(self.buf, p.CONTROL_OFFSET + 8, 4, 1 if engaged else 0)

    def executor_state(self) -> ExecutorState:
        raw = bytes(self.buf[p.EXECUTOR_OFFSET:p.EXECUTOR_OFFSET + 32])
        return ExecutorState(
            epoch=raw.decode('ascii', 'replace').strip('\x00'),
            heartbeat_ms=_get(self.buf, p.EXECUTOR_OFFSET + 32, 8),
            latest_seq=_get(self.buf, p.EXECUTOR_OFFSET + 40, 8),
            ring_full=_get(self.buf, p.EXECUTOR_OFFSET + 48, 4) != 0,
            started_ms=_get(self.buf, p.EXECUTOR_OFFSET + 56, 8),
        )

    # -- request / receipt ---------------------------------------------------
    def exchange(self, raw: bytes, timeout_s: float, sleep=time.sleep) -> bytes | None:
        if not 1 <= len(raw) <= p.FRAME_MAX:
            raise ValueError('request_size')
        self._generation = (self._generation % 0xFFFFFFFF) + 1
        generation = self._generation
        _put(self.buf, p.REQUEST_OFFSET + 4, 4, len(raw))
        self.buf[p.REQUEST_OFFSET + 8:p.REQUEST_OFFSET + 8 + len(raw)] = raw
        _put(self.buf, p.REQUEST_OFFSET, 4, generation)
        deadline = time.monotonic() + timeout_s
        while True:
            if _get(self.buf, p.RESPONSE_OFFSET, 4) == generation:
                size = _get(self.buf, p.RESPONSE_OFFSET + 4, 4)
                if not 1 <= size <= p.RESPONSE_OFFSET:
                    raise ValueError('receipt_size')
                return bytes(self.buf[p.RESPONSE_OFFSET + 8:p.RESPONSE_OFFSET + 8 + size])
            if time.monotonic() >= deadline:
                return None
            sleep(0.05)

    # -- report ring --------------------------------------------------------------
    def read_reports(self) -> list[dict]:
        consumed = _get(self.buf, p.CONTROL_OFFSET + 16, 8)
        latest = _get(self.buf, p.EXECUTOR_OFFSET + 40, 8)
        if latest < consumed:
            raise ReportGap(f'report cursor {consumed} ahead of latest {latest}')
        if latest - consumed > p.RING_SLOTS:
            raise ReportGap(f'reports {consumed + 1}..{latest} exceed ring')
        rows = []
        for seq in range(consumed + 1, latest + 1):
            slot = p.RING_OFFSET + (seq % p.RING_SLOTS) * p.RING_SLOT_SIZE
            if _get(self.buf, slot + 4, 8) != seq:
                raise ReportGap(f'report slot {seq} overwritten or missing')
            size = _get(self.buf, slot, 4)
            rows.append(json.loads(bytes(self.buf[slot + 12:slot + 12 + size])))
        if latest > consumed:
            _put(self.buf, p.CONTROL_OFFSET + 16, 8, latest)
        return rows


def open_named(run_id: str) -> SharedMemory:
    """Windows ``Local\\`` 命名映射；必须在与 QMT 相同的交互 session 中调用。"""
    if sys.platform != 'win32':
        raise RuntimeError('named QMT shared memory requires Windows')
    import mmap
    return SharedMemory(mmap.mmap(-1, p.MAP_SIZE, tagname='Local\\qsys_qmt_signal_' + run_id,
                                  access=mmap.ACCESS_WRITE))
