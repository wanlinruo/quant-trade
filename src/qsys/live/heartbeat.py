"""双向心跳。

核心侧与 Windows agent 互相心跳，任一侧失联告警。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import structlog

from qsys.live.messages import Heartbeat
from qsys.live.transport import Transport

log = structlog.get_logger(__name__)


class HeartbeatMonitor:
    def __init__(self, transport: Transport, *, prefix: str = "state:heartbeat") -> None:
        self._t = transport
        self._prefix = prefix

    def _key(self, source: str) -> str:
        return f"{self._prefix}:{source}"

    def beat(self, source: str, now: datetime | None = None) -> None:
        moment = now or datetime.now()
        self._t.set_key(
            self._key(source), Heartbeat(source=source, at=moment).model_dump_json()
        )

    def last_beat(self, source: str) -> datetime | None:
        raw = self._t.get_key(self._key(source))
        if raw is None:
            return None
        return Heartbeat.model_validate_json(raw).at

    def is_alive(
        self, source: str, *, max_age_seconds: float = 60.0, now: datetime | None = None
    ) -> bool:
        last = self.last_beat(source)
        if last is None:
            return False
        age = ((now or datetime.now()) - last).total_seconds()
        return age <= max_age_seconds

    def dead_sources(
        self, sources: Sequence[str], *, max_age_seconds: float = 60.0,
        now: datetime | None = None,
    ) -> tuple[str, ...]:
        dead = tuple(
            s for s in sources
            if not self.is_alive(s, max_age_seconds=max_age_seconds, now=now)
        )
        if dead:
            log.error("heartbeat.dead_sources", sources=list(dead))
        return dead
