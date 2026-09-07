"""Kill switch —— 双路径。

Redis key 供 Web 与核心进程使用；本地文件是**不依赖任何服务**的独立路径。
Web 挂了、核心进程挂了、Redis 连不上——只要能碰到文件系统就能停机。

两条路径任一生效即视为停机（或的关系）：宁可误停，不可停不下来。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import structlog

from qsys.live.transport import Transport

log = structlog.get_logger(__name__)


class KillSwitch:
    def __init__(
        self,
        transport: Transport,
        *,
        key: str = "control:kill",
        local_path: str | Path | None = None,
    ) -> None:
        self._transport = transport
        self._key = key
        self._path = Path(local_path) if local_path is not None else None

    def engage(self, reason: str) -> None:
        stamp = f"{datetime.now().isoformat()} {reason}"
        self._transport.set_key(self._key, stamp)
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(stamp)
        log.warning("kill_switch.engaged", reason=reason)

    def release(self) -> None:
        self._transport.delete_key(self._key)
        if self._path is not None and self._path.exists():
            self._path.unlink()
        log.warning("kill_switch.released")

    def _local_reason(self) -> str | None:
        if self._path is None or not self._path.exists():
            return None
        return self._path.read_text().strip()

    def is_engaged(self) -> bool:
        return self._transport.get_key(self._key) is not None or self._local_reason() is not None

    def reason(self) -> str:
        parts = [self._transport.get_key(self._key), self._local_reason()]
        return " | ".join(p for p in parts if p)
