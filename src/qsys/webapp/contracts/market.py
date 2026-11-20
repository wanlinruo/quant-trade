"""市场总览契约（设计文档 §8.8；行情快照字段见 qsys.live.messages.MarketOverviewSnapshot）。"""

from __future__ import annotations

from datetime import date
from typing import Literal

from qsys.webapp.contracts.base import Contract, Timestamp


class IndexQuote(Contract):
    symbol: str
    name: str
    price: float
    change_pct: float
    amount: float


class ExchangeCoverage(Contract):
    market: Literal["SH", "SZ", "BJ"]
    expected: int
    received: int
    valid: int
    suspended: int
    missing: int
    invalid: int
    other_session: int
    missing_samples: list[str]
    source_at_min: Timestamp | None
    source_at_max: Timestamp | None


class MarketCoverage(Contract):
    status: Literal["complete", "incomplete", "unknown"]
    universe_source: str | None
    universe_session: date | None
    expected: int | None
    excluded_non_a: int | None
    invalid_identity_count: int | None
    unexpected_count: int | None
    by_market: list[ExchangeCoverage]


class MarketSnapshot(Contract):
    session: date
    published_at: Timestamp
    indices: list[IndexQuote]
    valid_stock_count: int
    advance_count: int
    decline_count: int
    flat_count: int
    limit_up_count: int
    limit_down_count: int
    touched_limit_up_count: int
    broken_board_count: int
    broken_board_rate: float
    market_advance_ratio: float
    market_median_return_1d: float
    market_total_money: float
    limit_price_missing_count: int
    limit_up_unknown_count: int | None
    limit_down_unknown_count: int | None
    coverage: MarketCoverage | None


class LiveMarket(Contract):
    available: bool
    stale: bool
    snapshot: MarketSnapshot | None


class MarketSchedule(Contract):
    checked_at: Timestamp
    is_trading_day: bool
    is_open: bool
    next_open_at: Timestamp | None


class LimitPair(Contract):
    up: float | None
    down: float | None


class LimitCheckSample(Contract):
    symbol: str
    tushare: LimitPair | None
    qmt: LimitPair | None


class LimitCheck(Contract):
    session: date
    checked_at: Timestamp | None
    checked: int
    mismatched: int
    unbounded_equivalent: int
    missing_in_qmt: int
    samples: list[LimitCheckSample]
    missing_samples: list[LimitCheckSample]
    # #460：按 Tushare stock_basic 名录拆分未覆盖样本；名录读不到或为空时为 None（不拆分）
    missing_in_roster: list[str] | None
    missing_outside_roster: list[str] | None
    missing_split_complete: bool | None


class RegimeDay(Contract):
    dt: date
    limit_up_count: int | None
    limit_down_count: int | None
    limit_up_down_ratio: float | None
    touched_limit_up_count: int | None
    broken_board_count: int | None
    broken_board_rate: float | None
    first_board_count: int | None
    market_advance_ratio: float | None
    market_median_return_1d: float | None
    market_total_money: float | None
    prior_limitup_nextday_return_mean: float | None
    prior_limitup_nextday_return_median: float | None
    valid_stock_count: int | None
    index_ma20_slope_5d: float | None


class MarketOverview(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[True] = True
    live: LiveMarket | None
    schedule: MarketSchedule | None
    limit_check: LimitCheck | None
    regime: list[RegimeDay] | None
    missing: dict[str, str]
    errors: dict[str, Literal["读取失败"]]


class MarketDisabled(Contract):
    schema_version: Literal[1] = 1
    enabled: Literal[False] = False
