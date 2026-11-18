"""打板看板对外契约（#662 R2，设计文档 §8.9）。

子模型与读取器 ``queries.board_reader`` 的内部读模型同名同字段，时间一律带时区。
不进契约的只有两项：采集器 ``status.json``（运维组另行定义）与题材快照指纹 ``concept_sha``。
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from qsys.webapp.contracts.base import Contract, Timestamp
from qsys.webapp.contracts.market import MarketSchedule

CardStatus = Literal["sealed", "unknown", "broken", "touched", "rush"]
CompareStatus = Literal["promoted", "broken", "rush", "limit_down", "unknown", "missed"]
Pair = tuple[int | None, int | None]


class BoardSentiment(Contract):
    published_at: Timestamp
    quotes: int
    sealed: int
    limit_down: int
    touched: int
    broken: int
    seal_rate: float | None
    max_height: int | None
    promote_1_2: Pair
    promote_2_3: Pair
    promote_3_up: Pair
    rushing: int
    missing_limit: int
    seal_unknown: int


class BoardCard(Contract):
    symbol: str
    name: str
    status: CardStatus
    column: str | None
    height: int | None
    prev_height: int | None
    first_seal: Timestamp | None
    open_times: int
    resealed: bool
    pct: float | None
    is_st: bool | None
    is_bj: bool
    entered_at: Timestamp
    reason: str
    diffs: dict[str, str]  # 字段 → 直取值（盘后对账差异）


class BoardColumn(Contract):
    code: str
    name: str
    sealed_count: int | None
    first_seal: Timestamp | None
    gradient: dict[str, int]


class LadderRow(Contract):
    key: str
    label: str
    yesterday: int | None
    cells: dict[str, list[str]]


class CompareItem(Contract):
    symbol: str
    name: str
    pct: float | None
    column: str | None
    status: CompareStatus
    first_seal: Timestamp | None
    open_times: int | None
    prediction: float | None


class CompareGroup(Contract):
    level: int
    items: list[CompareItem]
    promoted: int


class BoardShadow(Contract):
    state: str
    message: str
    prediction_date: date | None
    model_version: str | None
    precision_at_20: float | None
    scored: int
    hits: int | None


class BoardEvent(Contract):
    at: Timestamp
    symbol: str
    name: str
    kind: str
    detail: str


class ConceptCurve(Contract):
    code: str
    name: str
    points: list[tuple[Timestamp, int]]


class BoardMeta(Contract):
    prev_day: date | None
    concept_fetched_at: Timestamp | None
    concept_error: str | None
    st_published: bool
    heights_error: str | None
    compacted_at: Timestamp | None
    compact_error: str | None
    reconciled_at: Timestamp | None
    reconcile_error: str | None
    has_raw: bool


class EffectItem(Contract):
    symbol: str
    name: str
    pct: float | None
    status: CompareStatus


class MoneyEffect(Contract):
    total: int
    priced: int
    no_quote: int
    avg_pct: float | None
    red_rate: float | None
    multi_avg_pct: float | None
    big_loss: int
    top_level: int
    top_items: list[EffectItem]


class LayerRow(Contract):
    level: int
    total: int
    promoted: int
    broken: int
    break_up: int
    break_down: int
    limit_down: int
    unknown: int
    no_quote: int
    rate: float | None
    prev_rate: float | None
    avg_pct: float | None


class Board(Contract):
    schema_version: Literal[1] = 1
    available: Literal[True] = True
    message: str
    day: date
    days: list[date]
    mode: Literal["live", "final", "history"]
    provisional: bool  # 盘中观测（mode == "live"），数值为暂计
    at: Timestamp | None
    times: list[Timestamp]
    meta: BoardMeta
    sentiment: BoardSentiment | None
    columns: list[BoardColumn]
    rows: list[LadderRow]
    cards: dict[str, BoardCard]
    compare: list[CompareGroup]
    compare_unknown: int
    shadow: BoardShadow | None
    timeline: list[BoardEvent]
    curves: list[ConceptCurve]
    mismatch_count: int
    effect: MoneyEffect | None
    layers: list[LayerRow]
    schedule: MarketSchedule | None
    errors: dict[str, Literal["读取失败"]]


class BoardUnavailable(Contract):
    schema_version: Literal[1] = 1
    available: Literal[False] = False
    message: str
    day: date | None
    days: list[date]


class SeriesPoint(Contract):
    at: Timestamp
    price: float
    volume: int | None  # 相邻快照累计成交量之差；首点或回退为 None


class SeriesEvent(Contract):
    at: Timestamp
    kind: str
    detail: str


class BoardSeries(Contract):
    schema_version: Literal[1] = 1
    available: bool
    message: str
    symbol: str
    name: str
    limit_up: float | None
    prev_close: float | None
    first_seal: Timestamp | None
    points: list[SeriesPoint]
    events: list[SeriesEvent]
