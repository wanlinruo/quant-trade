"""顶栏状态契约（设计文档 §5、§10）。"""

from __future__ import annotations

from typing import Literal

from qsys.webapp.contracts.base import Contract, Timestamp


class KillSource(Contract):
    id: str
    name: str
    state: str
    reason: str
    recorded_at: Timestamp | None


class KillStatus(Contract):
    state: Literal["stopped", "clear", "unknown"]
    label: str
    message: str
    sources: list[KillSource]


class LivePosture(Contract):
    state: Literal["trading", "reduce_only", "unknown"]
    label: str


class ShellStatus(Contract):
    schema_version: Literal[1] = 1
    checked_at: Timestamp
    kill: KillStatus
    live: LivePosture
    agent_heartbeat_at: Timestamp | None
    qmt: None = None
    alerts_open: int | None = None
    missing: dict[str, str]
