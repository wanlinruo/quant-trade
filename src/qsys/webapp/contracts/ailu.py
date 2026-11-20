"""AI 涨停契约（设计文档 §8.12）：影子榜单与研究回测，只读；不是交易信号。"""

from __future__ import annotations

from datetime import date
from typing import Literal

from qsys.webapp.contracts.base import Contract, Timestamp

ShadowState = Literal["unavailable", "waiting", "generating", "ready", "stale", "failed"]


class ShadowEntry(Contract):
    rank: int
    symbol: str
    name: str
    board: str
    p_touch: float
    p_seal_given_touch: float
    p_close_limit: float
    actual_touched: bool | None
    actual_closed_limit: bool | None
    opened_at_limit: bool | None
    one_price_limit: bool | None


class ShadowSnapshot(Contract):
    prediction_date: date
    target_session: date
    generated_at: Timestamp
    status: Literal["waiting", "generating", "ready", "failed"]
    model_version: str
    eligible_count: int
    rolling_precision_at_20: float | None
    calibration_error: float | None
    drift_score: float | None
    entries: list[ShadowEntry]


class ShadowDay(Contract):
    prediction_date: date
    target_session: date
    generated_at: Timestamp
    status: Literal["waiting", "generating", "ready", "failed"]
    model_version: str
    entry_count: int
    precision_at_20: float | None


class ShadowBoard(Contract):
    schema_version: Literal[1] = 1
    state: ShadowState
    message: str
    historical: bool
    snapshot: ShadowSnapshot | None
    history: list[ShadowDay]
    missing: dict[str, str]


class ResearchEntry(Contract):
    rank: int
    symbol: str
    name: str
    board: str
    p_touch: float
    p_seal_given_touch: float
    p_close_limit: float
    actual_touched: bool
    actual_closed_limit: bool
    opened_at_limit: bool
    one_price_limit: bool


class ResearchDay(Contract):
    prediction_date: date
    target_session: date
    split: Literal["TEST", "OBS"]
    precision_at_20: float
    touch_precision_at_20: float
    entries: list[ResearchEntry]


class ResearchHistoryDay(Contract):
    prediction_date: date
    target_session: date
    split: Literal["TEST", "OBS"]
    precision_at_20: float
    touch_precision_at_20: float


class ResearchMonth(Contract):
    month: date
    split: Literal["TEST", "OBS"]
    precision_at_20: float
    predictions: int
    hits: int


class ResearchSummary(Contract):
    generated_at: Timestamp | None
    source_sha: str
    data_sha: str
    first_prediction_date: date
    last_prediction_date: date
    sessions: int
    test_sessions: int
    obs_sessions: int
    test_precision_at_20: float
    obs_precision_at_20: float
    obs_baseline_precision_at_20: float
    obs_lift_at_20: float
    obs_month_win_rate: float
    promoted_to_shadow: bool


class ResearchBoard(Contract):
    schema_version: Literal[1] = 1
    state: Literal["ready", "unavailable", "not_found"]
    message: str
    summary: ResearchSummary | None
    selected: ResearchDay | None
    history: list[ResearchHistoryDay]
    monthly: list[ResearchMonth]
