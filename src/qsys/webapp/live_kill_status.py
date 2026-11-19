"""实盘停机闸只读观测；不复用会把读取失败转换为停机原因的交易控制器。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import os
from pathlib import Path
import stat
from time import monotonic

from qsys.live.messages import Heartbeat
from qsys.live.transport import Transport


@dataclass(frozen=True)
class KillSource:
    id: str
    name: str
    state: str
    reason: str
    recorded_at: str | None = None


@dataclass(frozen=True)
class LiveKillStatus:
    state: str
    label: str
    message: str
    checked_at: str
    valid_for_seconds: float
    sources: tuple[KillSource, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def _stamp(value: str) -> tuple[str, str | None]:
    stamp, separator, reason = value.partition(' ')
    try:
        datetime.fromisoformat(stamp)
    except ValueError:
        return value or '原因未上报', None
    return (reason if separator and reason else '原因未上报'), stamp


class LiveKillReader:
    def __init__(self, transport: Transport | None, local_path: Path | None, *,
                 heartbeat_prefix: str = 'state:heartbeat', max_age: float = 10.0):
        self.transport = transport
        self.local_path = local_path
        self.heartbeat_prefix = heartbeat_prefix
        self.max_age = max_age

    def _local(self) -> KillSource:
        source = ('linux', 'Linux 本地停机闸')
        if self.local_path is None:
            return KillSource(*source, 'unknown', '实盘路径未配置')
        try:
            from qsys.live.state_binding import local_kill_path
            path = local_kill_path(self.local_path)
        except (OSError, ValueError):
            return KillSource(*source, 'unknown', '实盘状态绑定异常')
        try:
            info = path.lstat()
        except FileNotFoundError:
            # ENOENT 也可能来自丢失的父目录；必须先确认目录真实可访问。
            try:
                parent = path.parent
                if not parent.is_dir() or not os.access(parent, os.R_OK | os.X_OK):
                    raise OSError('parent unavailable')
            except OSError:
                return KillSource(*source, 'unknown', '实盘路径不可访问')
            return KillSource(*source, 'clear', '未发现停机标记')
        except OSError:
            return KillSource(*source, 'unknown', '停机标记不可访问')
        if not stat.S_ISREG(info.st_mode):
            return KillSource(*source, 'unknown', '停机标记类型异常')
        try:
            # 文件存在即停机，内容不可读时也不能显示解除；限制展示输入大小。
            with path.open() as stream:
                value = stream.read(4096).strip()
            reason, stamp = _stamp(value)
        except (OSError, UnicodeError):
            reason, stamp = '标记存在，原因不可读', None
        return KillSource(*source, 'stopped', reason, stamp)

    def read(self, *, now: datetime | None = None) -> LiveKillStatus:
        started = monotonic()
        moment = now or datetime.now()
        local = self._local()
        ttl = self.max_age
        try:
            if self.transport is None:
                raise OSError('transport unavailable')
            value = self.transport.get_key('control:kill')
            reason, stamp = _stamp(value) if value is not None else ('停机闸未生效', None)
            remote = KillSource('redis', 'Redis 实盘停机闸',
                                'stopped' if value is not None else 'clear', reason, stamp)
        except Exception:
            remote = KillSource('redis', 'Redis 实盘停机闸', 'unknown', '读取失败或未配置')
        try:
            if self.transport is None:
                raise OSError('transport unavailable')
            raw = self.transport.get_key(f'{self.heartbeat_prefix}:agent')
            beat = Heartbeat.model_validate_json(raw) if raw is not None else None
            if beat is None or beat.source != 'agent' or beat.trading_mode != 'live':
                raise ValueError('live agent heartbeat unavailable')
            age = (moment - beat.at).total_seconds()
            if age < -1 or age >= self.max_age or beat.local_kill_engaged is None:
                raise ValueError('heartbeat expired or missing field')
            ttl = min(ttl, self.max_age - max(0.0, age))
            agent = KillSource('agent', 'Windows 实盘 agent',
                               'stopped' if beat.local_kill_engaged else 'clear',
                               '原因未上报' if beat.local_kill_engaged else '停机闸未生效',
                               beat.at.isoformat())
        except Exception:
            agent = KillSource('agent', 'Windows 实盘 agent', 'unknown',
                               '心跳不可用、过期或实盘停机字段未上报')
        sources = (local, remote, agent)
        state = ('stopped' if any(s.state == 'stopped' for s in sources) else
                 'clear' if all(s.state == 'clear' for s in sources) else 'unknown')
        ttl = max(0.0, ttl - (monotonic() - started))
        if ttl == 0:
            state = 'unknown'
        label, message = {
            'stopped': ('实盘已停机', '禁止自动开仓；减仓与风控沿用现有规则。'),
            'clear': ('停机闸已解除', '仅表示三处停机闸未生效，不代表其他交易条件均满足。'),
            'unknown': ('停机状态未知', '无法确认当前停机状态，请核对异常来源。'),
        }[state]
        return LiveKillStatus(state, label, message, moment.isoformat(), max(0.0, ttl), sources)
