"""“今日运行”组合契约（#662 计划 06b）。"""

from __future__ import annotations

from datetime import date
from typing import Literal

from qsys.webapp.contracts.base import Contract, Timestamp
from qsys.webapp.contracts.shell import KillStatus


class TodayDisabled(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[False] = False


class TodayConcern(Contract):
    id: str
    severity: Literal["critical", "warning", "info", "unknown"]
    label: str
    detail: str | None
    page: str


class TodayKpis(Contract):
    account_total: float | None
    account_at: Timestamp | None
    index_name: str | None
    index_price: float | None
    index_change_ratio: float | None
    limit_up: int | None
    limit_down: int | None
    seal_ratio: float | None
    link_max_age_seconds: float | None
    link_fresh: int | None
    link_total: int | None


class TodayPhase(Contract):
    id: str
    label: str
    time_label: str
    state: Literal["ok", "warning", "failed", "running", "not_started", "unknown"]
    completed: int
    recorded: int


class TodayEvent(Contract):
    event_key: str
    phase: str
    scheduled_at: Timestamp | None
    started_at: Timestamp | None
    finished_at: Timestamp | None
    state: Literal["completed", "failed"]
    summary: str
    detail: str | None


class TodayLinkPoint(Contract):
    sampled_at: Timestamp
    age_seconds: float | None
    threshold_seconds: float | None
    fresh: bool | None


class TodayLink(Contract):
    source: str
    points: list[TodayLinkPoint]


class TodaySentimentPoint(Contract):
    day: date
    limit_up: int | None
    limit_down: int | None
    broken_board_ratio: float | None
    prior_limitup_return_ratio: float | None


class TodayView(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    day: date
    days: list[date]
    state: Literal["running", "closed", "reduce_only", "waiting", "unknown"]
    title: str
    kill: KillStatus | None
    concerns: list[TodayConcern]
    kpis: TodayKpis
    phases: list[TodayPhase]
    events: list[TodayEvent]
    links: list[TodayLink]
    sentiment: list[TodaySentimentPoint]
    missing: dict[str, str]
