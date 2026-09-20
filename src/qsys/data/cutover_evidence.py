"""阶段 11 的可重复策略输入证据生成器。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import json

import polars as pl
import pyarrow as pa

from qsys.data import schema
from qsys.data.cutover import StrategyComparison
from qsys.data.pipeline.canonical_reference import Reconciliation, reconcile_frames
from qsys.data.pipeline.canonical_state import _security_status_frame
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


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
    if not ordered.height:
        return
    # 对账允许数值物理类型不同；统一 Arrow 物理表示后再做批量 IPC 摘要，
    # 避免逐行 JSON 在分钟数据上造成数分钟的 CPU 开销。
    casts: list[pl.Expr] = []
    for column, dtype in ordered.schema.items():
        if dtype == pl.Boolean or dtype.is_numeric():
            casts.append(pl.col(column).cast(pl.Float64))
        elif isinstance(dtype, pl.Datetime):
            casts.append(pl.col(column).cast(pl.Datetime("us")))
    normalized = ordered.with_columns(casts) if casts else ordered
    null_exprs: list[pl.Expr] = []
    for index, (column, dtype) in enumerate(normalized.schema.items()):
        null_exprs.append(
            pl.col(column).is_null().cast(pl.Float64).alias(f"__null_{index}")
        )
        if dtype.is_numeric():
            replacement: object = 0.0
        elif isinstance(dtype, pl.Datetime):
            replacement = datetime(1970, 1, 1)
        elif dtype == pl.Date:
            replacement = date(1970, 1, 1)
        elif dtype == pl.Boolean:
            replacement = False
        elif dtype == pl.String:
            replacement = ""
        elif dtype == pl.Binary:
            replacement = b""
        else:
            continue
        null_exprs.append(pl.col(column).fill_null(replacement))
    normalized = normalized.with_columns(null_exprs)
    table = normalized.to_arrow().combine_chunks().replace_schema_metadata(None)
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    digest.update(sink.getvalue().to_pybytes())


def _combine(results: list[Reconciliation]) -> Reconciliation:
    return Reconciliation(
        sum(result.left_rows for result in results),
        sum(result.right_rows for result in results),
        tuple(key for result in results for key in result.left_only),
        tuple(key for result in results for key in result.right_only),
        tuple(key for result in results for key in result.mismatched),
    )


def _round_float_columns(frame: pl.DataFrame, precision: int = 12) -> pl.DataFrame:
    """把派生浮点输入收敛到策略可观察精度，消除运算顺序的机器尾差。"""
    return frame.with_columns(
        pl.col(column).round(precision)
        for column, dtype in frame.schema.items()
        if dtype.is_float()
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
        "symbol", "dt",
        (pl.col("market_cap") / 1e8).alias("market_cap"),
        (pl.col("circulating_market_cap") / 1e8).alias("circulating_market_cap"),
        "turnover_ratio",
    ).join(
        query.bar_daily(None, start, end, columns=("symbol", "dt", "close")),
        on=["symbol", "dt"], how="left",
    ).select(schema.VALUATION_SNAPSHOT.names)
    old_status = _security_status_frame(warehouse, start, end)
    new_status = query.security_status_daily(None, start, end)
    old_regime = _round_float_columns(query.market_regime(start, end))
    new_regime = _round_float_columns(query.market_regime_daily(start, end))

    pairs: tuple[tuple[str, pl.DataFrame, pl.DataFrame, tuple[str, ...]], ...] = (
        ("daily_bars", old_daily, new_daily, ("symbol", "dt")),
        ("adjustment_factors", old_factor, new_factor, ("symbol", "dt")),
        ("valuation", old_valuation, new_valuation, ("symbol", "dt")),
        ("price_limits", query.price_limits(None, start, end),
         query.price_limit_daily(None, start, end), ("symbol", "dt")),
        ("security_status", old_status, new_status, ("symbol", "dt")),
        ("market_flags", query.daily_flags(start, end),
         query.market_flags_daily(None, start, end), ("symbol", "dt")),
        ("market_regime", old_regime, new_regime, ("dt",)),
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
