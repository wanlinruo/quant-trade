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
        """先写本地文件，再写 Redis。

        顺序很要紧：Redis 不可达时 ``set_key`` 会抛，先写 Redis 就意味着本地标记
        永远写不下去——而核心连续故障熔断时，坏的往往正是 Redis。
        （2026-09-27 故障演练发现，见 docs/reference/fault-drills.md。）
        """
        stamp = f"{datetime.now().isoformat()} {reason}"
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(stamp)
        try:
            self._transport.set_key(self._key, stamp)
        except Exception as exc:  # Redis 挂了不妨碍停机，本地标记已经写下了
            log.error("kill_switch.redis_unreachable", op="engage", error=str(exc))
        log.warning("kill_switch.engaged", reason=reason)

    def release(self) -> None:
        """两条路径都要解除，一条失败不耽误另一条。"""
        try:
            self._transport.delete_key(self._key)
        except Exception as exc:
            log.error("kill_switch.redis_unreachable", op="release", error=str(exc))
        if self._path is not None and self._path.exists():
            self._path.unlink()
        log.warning("kill_switch.released")

    def _local_reason(self) -> str | None:
        if self._path is None or not self._path.exists():
            return None
        return self._path.read_text().strip()

    def _remote_reason(self) -> str | None:
        """Redis 读不到时返回一条说明，而不是抛出去。"""
        try:
            return self._transport.get_key(self._key)
        except Exception as exc:
            log.error("kill_switch.redis_unreachable", op="read", error=str(exc))
            return f"Redis 不可达（{exc}），按停机处理"

    def is_engaged(self) -> bool:
        """先看本地，再看 Redis；Redis 读不到就按停机算。

        本地这条路径不依赖任何服务，所以先问它——否则 Redis 一断，手工放下的
        停机标记就再也读不到了。Redis 读失败按停机处理，遵循本模块的原则：
        宁可误停，不可停不下来。
        """
        return self._local_reason() is not None or self._remote_reason() is not None

    def reason(self) -> str:
        """两条路径的原因；同一次停机两边写的是同一句，去重后只显示一次。"""
        seen: list[str] = []
        for part in (self._local_reason(), self._remote_reason()):
            if part and part not in seen:
                seen.append(part)
        return " | ".join(seen)
