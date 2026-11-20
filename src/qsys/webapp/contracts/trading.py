"""交易组对外契约（#662 计划 06c）。

金额单位元，比例取 0–1；没有直取事实就返回 ``None``，原因写入 ``missing``。
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from qsys.webapp.contracts.base import Contract, Timestamp
from qsys.webapp.contracts.shell import KillStatus


class TradingDisabled(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[False] = False


class TradingDayInfo(Contract):
    selected: date | None
    available: list[date]


class MoneyObservation(Contract):
    at: Timestamp | None
    cash: float | None
    market_value: float | None
    total_value: float | None
    return_ratio: None = None


class TradingPosition(Contract):
    symbol: str
    total_qty: int
    available_qty: int
    avg_cost: float | None
    last_price: float | None
    market_value: float | None
    reserved_qty: int | None


class TradingHeartbeat(Contract):
    source: str
    at: Timestamp | None
    age_seconds: float | None
    threshold_seconds: float | None
    fresh: bool | None
    detail: str | None


class RiskLimit(Contract):
    id: str
    label: str
    value: float | None
    limit: float
    ratio: float | None
    unit: Literal["ratio", "yuan"]
    state: Literal["ok", "warning", "blocked", "unknown"]


class EquityPoint(Contract):
    day: date
    total_value: float | None


class LiveTradingOverview(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    day: TradingDayInfo
    posture: Literal["trading", "reduce_only", "closed", "unknown"]
    posture_label: str
    observation: MoneyObservation
    positions: list[TradingPosition]
    heartbeats: list[TradingHeartbeat]
    kill: KillStatus
    risks: list[RiskLimit]
    equity: list[EquityPoint]
    order_count: int | None
    missing: dict[str, str]


class OrderStage(Contract):
    state: str
    at: Timestamp | None
    detail: str | None


class TradingOrder(Contract):
    id: str
    event_key: str
    symbol: str
    side: str
    qty: int
    remaining_qty: int
    order_type: str
    limit_price: float | None
    status: str
    created_at: Timestamp
    updated_at: Timestamp | None
    broker_order_id: str | None
    broker_accepted_at: Timestamp | None
    unknown_no_retry: bool
    stages: list[OrderStage]


class TradingFill(Contract):
    order_id: str
    symbol: str
    side: str
    qty: int
    price: float
    fee: float
    filled_at: Timestamp
    broker_order_id: str | None
    broker_trade_id: str | None


class TradingReject(Contract):
    order_id: str
    symbol: str
    side: str
    qty: int
    reason: str
    at: Timestamp


class TradingOrders(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    day: TradingDayInfo
    orders: list[TradingOrder]
    fills: list[TradingFill]
    rejects: list[TradingReject]
    missing: dict[str, str]


class ReconCell(Contract):
    day: date
    layer: Literal["cash", "positions", "orders"]
    state: Literal["consistent", "inconsistent", "unknown", "not_started"]
    at: Timestamp | None
    detail: str | None


class ReconciliationRecord(Contract):
    at: Timestamp
    layer: str
    mode: str
    consistent: bool
    cash_diff: float | None
    mismatches: int
    detail: str


class KillReviewItemInfo(Contract):
    id: str
    label: str
    state: Literal["pass", "fail", "unknown", "confirm", "info"]
    detail: str
    observed: dict[str, JsonValue]


class KillReviewInfo(Contract):
    reviewed_at: Timestamp
    overall: Literal["ready", "blocked", "needs_confirmation"]
    engaged: bool
    stale: bool
    items: list[KillReviewItemInfo]


class TradingReconciliation(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    day: TradingDayInfo
    heatmap: list[ReconCell]
    records: list[ReconciliationRecord]
    risks: list[RiskLimit]
    kill: KillStatus
    review: KillReviewInfo | None
    missing: dict[str, str]


class StrategyRunInfo(Contract):
    event_key: str
    phase: str
    scheduled_at: Timestamp | None
    started_at: Timestamp | None
    finished_at: Timestamp | None
    duration_ms: int
    status: Literal["completed", "failed"]
    candidates: int | None
    routed: int | None
    passed: int | None
    orders: int
    rejects: int
    summary: str
    reasons: dict[str, int]
    note: str
    error: str
    event_status: str | None
    event_detail: str | None


class TradingRuns(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    day: TradingDayInfo
    runs: list[StrategyRunInfo]
    report_text: str | None
    missing: dict[str, str]


class PaperAccountRow(Contract):
    id: str
    name: str
    strategy_name: str
    version_id: str
    status: str
    activation_date: date
    error: str | None
    total_value: float | None
    return_ratio: float | None
    max_drawdown_ratio: float | None
    position_count: int | None


class PaperAccounts(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    accounts: list[PaperAccountRow]


class PaperEquityPoint(Contract):
    at: Timestamp | None
    day: date
    total_value: float
    strategy_ratio: float | None
    benchmark_ratio: float | None


class PaperBacktestPoint(Contract):
    day: date
    total_value: float | None
    return_ratio: float | None


class PaperAccountDetail(PaperAccountRow):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    backtest_id: str
    strategy_id: str
    initial_cash: float
    cash: float
    notify_mask: str | None
    positions: list[dict[str, JsonValue]]
    orders: list[dict[str, JsonValue]]
    fills: list[dict[str, JsonValue]]
    rejects: list[dict[str, JsonValue]]
    events: list[dict[str, JsonValue]]
    logs: list[dict[str, JsonValue]]
    runs: list[StrategyRunInfo]
    equity: list[PaperEquityPoint]
    source_backtest: list[PaperBacktestPoint]
    day: TradingDayInfo
    missing: dict[str, str]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PaperActionRequest(_Request):
    action: Literal["pause", "resume", "end"]


class PaperNotifyRequest(_Request):
    webhook: str


class PaperActionResult(Contract):
    schema_version: Literal[1] = 1
    id: str
    status: str
    notify_mask: str | None = None
