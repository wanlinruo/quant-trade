"""研究侧对外契约：策略、回测、研究任务（#662 R3，设计文档 §6.3、§8.10、§8.11）。

比例一律 0–1（收益、回撤），金额单位元；读不到为 null。请求模型 ``extra="forbid"``。
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from qsys.webapp.contracts.base import Contract, Timestamp

Cell = str | int | float | bool | None
Scalar = int | float | bool | str


class ResearchDisabled(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[False] = False


class BacktestRow(Contract):
    id: str
    strategy: str
    strategy_id: str | None
    start: date | None
    end: date | None
    created_at: Timestamp | None
    started_at: Timestamp | None
    finished_at: Timestamp | None
    duration_seconds: int | None
    status: str
    cost_model: str | None
    total_return: float | None
    benchmark_return: float | None
    max_drawdown: float | None
    has_report: bool
    source: Literal["web", "cli"]
    progress: int | None
    total: int | None
    current_session: date | None


class StrategyRow(Contract):
    id: str
    name: str
    updated_at: Timestamp | None
    version_id: str
    stage: Literal["live", "paper", "backtest", "draft"]
    stage_label: str
    stage_detail: str
    in_use: bool
    used_by: list[str]
    latest_backtest: BacktestRow | None


class ParameterSpec(Contract):
    name: str
    type: Literal["int", "float", "bool", "str"]
    default: Scalar | None
    label: str
    description: str
    minimum: float | None
    maximum: float | None
    choices: list[Scalar]


class StrategyVersion(Contract):
    id: str
    created_at: Timestamp | None


class StrategyList(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    core_label: str
    templates: list[str]
    strategies: list[StrategyRow]


class StrategyDetail(StrategyRow):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    core_label: str
    source: str
    parameters: list[ParameterSpec]
    versions: list[StrategyVersion]
    backtests: list[BacktestRow]


class BacktestList(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    rows: list[BacktestRow]


class BacktestJobInfo(Contract):
    id: str
    strategy_name: str
    status: str
    progress: int
    total: int
    percent: int
    current_session: date | None
    error: str | None
    config: dict[str, JsonValue]


class RunSummary(Contract):
    name: str
    run_id: str
    strategy: str
    start: date | None
    end: date | None
    initial_value: float | None
    total_value: float | None
    cumulative_return: float | None
    buys: int | None
    sells: int | None
    rejects: int | None
    closed_lots: int | None
    cost_model: str | None
    entry_fill: str | None
    params: dict[str, JsonValue]
    generated_at: Timestamp | None
    source: Literal["web", "cli"]


class ReturnPoint(Contract):
    date: str
    strategy: float | None
    benchmark: float | None
    excess: float | None
    drawdown: float | None


class Episode(Contract):
    peak_date: str
    trough_date: str
    recovery_date: str | None
    depth: float


class MonthReturn(Contract):
    month: str
    value: float | None


class RunFileInfo(Contract):
    name: str
    size: int


class BacktestReport(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    report: Literal[True] = True
    name: str
    strategy_id: str | None
    job: BacktestJobInfo | None
    summary: RunSummary
    metrics: dict[str, float]
    series: list[ReturnPoint]
    episodes: list[Episode]
    monthly: list[MonthReturn]
    fills: list[dict[str, Cell]]
    fills_total: int
    rejects: list[dict[str, Cell]]
    files: list[RunFileInfo]
    warnings: list[str]


class BacktestPending(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    report: Literal[False] = False
    strategy_id: str
    job: BacktestJobInfo
    log_tail: str


class ArtifactInfo(Contract):
    path: str
    bytes: int | None
    sha256: str | None


class ResearchJob(Contract):
    id: str
    state: str
    detail: str
    task: str | None
    parameters: dict[str, JsonValue]
    timeout_seconds: int | None
    created_at: Timestamp | None
    code_sha: str | None
    snapshot_id: str | None
    snapshot_content_sha256: str | None
    artifacts: list[ArtifactInfo]


class ResearchJobList(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    submission_enabled: bool
    jobs: list[ResearchJob]


# ---- 写接口 ----
class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrategyInput(_Request):
    name: str
    source: str


class TemplateImport(_Request):
    filename: str


class BacktestRequest(_Request):
    strategy_id: str
    start: date
    end: date
    cash: float = 100_000
    params: dict[str, JsonValue] = {}
    entry_fill: str = "continuous"
    jq_compat: bool = False


class ResearchJobRequest(_Request):
    task: str
    parameters: dict[str, JsonValue] = {}
    timeout_seconds: int | None = None


class Created(Contract):
    schema_version: Literal[1] = 1
    id: str


class Cancelled(Contract):
    schema_version: Literal[1] = 1
    status: Literal["cancelled", "cancellation-requested"]


class Resubmitted(Contract):
    schema_version: Literal[1] = 1
    id: str
    resubmitted_from: str
