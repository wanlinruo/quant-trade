"""阶段 11 的可重复策略输入证据生成器。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
import json
import math
from numbers import Number

import polars as pl

from qsys.data import schema
from qsys.data.cutover import StrategyComparison
from qsys.data.pipeline.canonical_reference import Reconciliation, reconcile_frames
from qsys.data.pipeline.canonical_state import _security_status_frame
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


def _digest_value(value: object) -> object:
    """按对账使用的值语义生成稳定且不依赖 Arrow dtype 的摘要值。"""
    if value is None:
        return None
    if isinstance(value, Number):
        if isinstance(value, bool):
            return {"number": str(int(value))}
        if isinstance(value, float) and not math.isfinite(value):
            return {"number": str(value)}
        return {"number": str(Decimal(str(value)).normalize())}
    if isinstance(value, datetime):
        return {"datetime": value.isoformat()}
    if isinstance(value, date):
        return {"date": value.isoformat()}
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    return {"value": str(value)}


def _update_digest(
    digest, name: str, frame: pl.DataFrame, keys: tuple[str, ...]
) -> None:
    values = sorted(column for column in frame.columns if column not in keys)
    columns = [*keys, *values]
    ordered = frame.select(columns).sort(list(keys)) if frame.height else frame
    digest.update(json.dumps(
        {"family": name, "columns": columns},
        ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode())
    digest.update(b"\0")
    for row in ordered.iter_rows():
        digest.update(json.dumps(
            [_digest_value(value) for value in row],
            ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        ).encode())
        digest.update(b"\n")


def _combine(results: list[Reconciliation]) -> Reconciliation:
    return Reconciliation(
        sum(result.left_rows for result in results),
        sum(result.right_rows for result in results),
        tuple(key for result in results for key in result.left_only),
        tuple(key for result in results for key in result.right_only),
        tuple(key for result in results for key in result.mismatched),
    )


def strategy_input_comparison(
    warehouse: Warehouse, start: date, end: date
) -> StrategyComparison:
    """对账策略可见的历史行情、因子、状态和派生输入并生成双侧摘要。"""
    query = WarehouseQuery(warehouse)
    old_daily = query.daily_bars(None, start, end)
    new_daily = query.bar_daily(
        None, start, end, columns=schema.DAILY_BARS.names
    )
    old_factor = query.adj_factors().filter(
        pl.col("dt").is_between(start, end)
    ).select("symbol", "dt", "cum_factor", "cum_share_factor")
    new_factor = query.adjustment_factor_daily(None, start, end).select(
        "symbol", "dt", pl.col("price_factor").alias("cum_factor"),
        pl.col("share_factor").alias("cum_share_factor"),
    )
    old_valuation = query.valuation(start, end)
    new_valuation = query.valuation_daily(None, start, end).select(
        "symbol", "dt", "market_cap", "circulating_market_cap", "turnover_ratio"
    ).join(
        query.bar_daily(None, start, end, columns=("symbol", "dt", "close")),
        on=["symbol", "dt"], how="left",
    ).select(schema.VALUATION_SNAPSHOT.names)
    old_status = _security_status_frame(warehouse, start, end)
    new_status = query.security_status_daily(None, start, end)

    pairs: tuple[tuple[str, pl.DataFrame, pl.DataFrame, tuple[str, ...]], ...] = (
        ("daily_bars", old_daily, new_daily, ("symbol", "dt")),
        ("adjustment_factors", old_factor, new_factor, ("symbol", "dt")),
        ("valuation", old_valuation, new_valuation, ("symbol", "dt")),
        ("price_limits", query.price_limits(None, start, end),
         query.price_limit_daily(None, start, end), ("symbol", "dt")),
        ("security_status", old_status, new_status, ("symbol", "dt")),
        ("market_flags", query.daily_flags(start, end),
         query.market_flags_daily(None, start, end), ("symbol", "dt")),
        ("market_regime", query.market_regime(start, end),
         query.market_regime_daily(start, end), ("dt",)),
    )
    results = [
        (name, reconcile_frames(left, right, keys), left, right, keys)
        for name, left, right, keys in pairs
    ]
    left_digest = hashlib.sha256()
    right_digest = hashlib.sha256()
    for name, _, left, right, keys in results:
        _update_digest(left_digest, name, left, keys)
        _update_digest(right_digest, name, right, keys)
    minute_results: list[Reconciliation] = []
    current = start
    while current <= end:
        day_lo = datetime.combine(current, datetime.min.time())
        day_hi = datetime.combine(current, datetime.max.time())
        old_minute = query.minute_bars(None, day_lo, day_hi).with_columns(
            pl.col("volume").cast(pl.Float64)
        )
        new_minute = query.bar_minute_1m(
            None, day_lo, day_hi,
            columns=("symbol", "ts", "open", "high", "low", "close", "volume", "amount"),
        ).rename({"ts": "dt"})
        minute_results.append(reconcile_frames(
            old_minute, new_minute, ("symbol", "dt")
        ))
        family = f"minute_bars:{current.isoformat()}"
        _update_digest(left_digest, family, old_minute, ("symbol", "dt"))
        _update_digest(right_digest, family, new_minute, ("symbol", "dt"))
        current += timedelta(days=1)
    minute_result = _combine(minute_results)
    results.insert(1, (
        "minute_bars", minute_result,
        pl.DataFrame(), pl.DataFrame(), ("symbol", "dt"),
    ))
    matches = all(result.matches for _, result, _, _, _ in results)
    cases = sum(max(result.left_rows, result.right_rows)
                for _, result, _, _, _ in results)
    detail = "; ".join(
        f"{name}: old/new={result.left_rows}/{result.right_rows}, "
        f"left_only={len(result.left_only)}, right_only={len(result.right_only)}, "
        f"mismatched={len(result.mismatched)}"
        for name, result, _, _, _ in results
    )
    return StrategyComparison(
        matches, cases, left_digest.hexdigest(), right_digest.hexdigest(), detail
    )
