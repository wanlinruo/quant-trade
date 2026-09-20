"""生产定时任务使用的 canonical 幂等发布协调器。

协调器只在 legacy/raw 来源已经成功落盘后运行。它不改变消费者读取模式，
但任何新旧对账失败都会抛错，让 systemd 和 ServiceRun 如实记录失败。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from qsys.data.pipeline.canonical_daily import (
    build_adjustment_factor_daily,
    build_bar_daily,
    build_share_capital_history,
    build_valuation_daily,
    daily_reconciliation,
    require_canonical_daily_sources,
)
from qsys.data.pipeline.canonical_minute import build_bar_minute_1m
from qsys.data.pipeline.canonical_reference import (
    build_security_master,
    build_security_name_history,
    calendar_reconciliation,
    publish_trading_calendar,
    reference_reconciliation,
    require_calendar_coverage,
    require_reference_sources,
)
from qsys.data.pipeline.canonical_state import (
    build_market_flags_daily,
    build_market_regime_daily,
    build_price_limit_daily,
    build_security_status_daily,
    require_canonical_state_sources,
    state_reconciliation,
)
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


@dataclass(frozen=True, slots=True)
class CanonicalPublishResult:
    counts: dict[str, int]
    reconciled: tuple[str, ...]


def _require_matches(results: dict, *, stage: str) -> tuple[str, ...]:
    failed = sorted(name for name, result in results.items() if not result.matches)
    if failed:
        raise RuntimeError(f"{stage} canonical 对账失败：{', '.join(failed)}")
    return tuple(sorted(results))


def publish_premarket(
    warehouse: Warehouse, day: date
) -> CanonicalPublishResult:
    """把已验证的盘前 legacy 涨跌停价发布到 canonical。"""
    counts = {"price_limit_daily": build_price_limit_daily(warehouse, day, day)}
    result = state_reconciliation(warehouse, day, day)["price_limit_daily"]
    reconciled = _require_matches(
        {"price_limit_daily": result}, stage="premarket"
    )
    return CanonicalPublishResult(counts, reconciled)


def publish_postclose(
    warehouse: Warehouse,
    raw_calendar: pl.DataFrame,
    day: date,
    *,
    market: str = "SSE",
) -> CanonicalPublishResult:
    """发布收盘后的 reference、daily、state，并逐表对账。"""
    require_reference_sources(warehouse)
    require_calendar_coverage(raw_calendar, day, day, market=market)
    require_canonical_daily_sources(warehouse, day, day)

    counts = {
        "security_master": build_security_master(warehouse),
        "security_name_history": build_security_name_history(warehouse),
        "trading_calendar": publish_trading_calendar(
            warehouse, raw_calendar, market=market
        ),
        "bar_daily": build_bar_daily(warehouse, day, day),
        "valuation_daily": build_valuation_daily(warehouse, day, day),
        "share_capital_history": build_share_capital_history(warehouse),
        "adjustment_factor_daily": build_adjustment_factor_daily(
            warehouse, day, day
        ),
    }
    # state 的来源包含上面刚发布的 canonical daily/factor。
    require_canonical_state_sources(warehouse, day, day)
    counts.update({
        "price_limit_daily": build_price_limit_daily(warehouse, day, day),
        "security_status_daily": build_security_status_daily(warehouse, day, day),
        "market_flags_daily": build_market_flags_daily(warehouse, day, day),
        "market_regime_daily": build_market_regime_daily(warehouse, day, day),
    })

    reference = reference_reconciliation(warehouse)
    reference["trading_calendar"] = calendar_reconciliation(
        raw_calendar,
        # publish_trading_calendar 已验证覆盖；从目标再读一次保证写后结果。
        WarehouseQuery(warehouse).trading_calendar(day, day, market=market),
        market=market,
    )
    reconciliations = {
        **reference,
        **daily_reconciliation(warehouse, day, day),
        **state_reconciliation(warehouse, day, day),
    }
    return CanonicalPublishResult(
        counts, _require_matches(reconciliations, stage="postclose")
    )


def publish_minutes(
    warehouse: Warehouse, start: date, end: date
) -> CanonicalPublishResult:
    """发布一个日期范围的 canonical 分钟分区。"""
    result = build_bar_minute_1m(warehouse, start, end)
    return CanonicalPublishResult(
        {"bar_minute_1m": result.rows}, ("bar_minute_1m",)
    )
