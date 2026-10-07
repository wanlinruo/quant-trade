"""阶段 4：从现有已验证事实并行构建规范日线窄表。

只写新目标表；不覆盖 ``tushare_daily`` / ``share_capital`` / ``adj_factors``，
不修改 ``WarehouseQuery.daily_bars`` 的生产读取路径。
"""

from __future__ import annotations

from datetime import date
from typing import Callable

import polars as pl
import pyarrow as pa

from qsys.data import schema
from qsys.data.pipeline.canonical_reference import Reconciliation, reconcile_frames
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


def _require_unique(frame: pl.DataFrame, keys: tuple[str, ...], source: str) -> None:
    duplicates = frame.height - frame.n_unique(subset=list(keys))
    if duplicates:
        raise RuntimeError(f"{source} 含 {duplicates} 行重复键 {keys}，拒绝发布规范表")
    nulls = sum(frame[key].null_count() for key in keys)
    if nulls:
        raise RuntimeError(f"{source} 主键含 {nulls} 个 null，拒绝发布规范表")


def canonical_daily_preflight(
    warehouse: Warehouse, start: date, end: date
) -> tuple[str, ...]:
    """在任何目标写入前验证规范日线来源均具备可对账输入。"""
    if start > end:
        return ("start 晚于 end",)
    query = WarehouseQuery(warehouse)
    daily = query.tushare_daily(
        None, start, end, columns=["symbol", "dt"]
    )
    factors = query.adj_factors(start=start, end=end)
    issues: list[str] = []
    for name, frame, keys in (
        ("tushare_daily", daily, ("symbol", "dt")),
        ("adj_factors", factors, ("symbol", "dt")),
    ):
        if frame.height == 0:
            issues.append(f"{name} 在构建范围内为空")
            continue
        duplicates = frame.height - frame.n_unique(subset=list(keys))
        nulls = sum(frame[key].null_count() for key in keys)
        if duplicates:
            issues.append(f"{name} 有 {duplicates} 行重复键")
        if nulls:
            issues.append(f"{name} 主键有 {nulls} 个 null")
    return tuple(issues)


def require_canonical_daily_sources(
    warehouse: Warehouse, start: date, end: date
) -> None:
    issues = canonical_daily_preflight(warehouse, start, end)
    if issues:
        raise RuntimeError("规范日线预检失败：" + "；".join(issues))


def _publish_years(
    warehouse: Warehouse,
    dataset: str,
    target_schema: pa.Schema,
    fresh: pl.DataFrame,
    start: date,
    end: date,
    reader: Callable[[date, date], pl.DataFrame],
) -> int:
    if fresh.height == 0:
        return 0
    _require_unique(fresh, ("symbol", "dt"), dataset)
    written = 0
    for year in range(start.year, end.year + 1):
        lo, hi = max(start, date(year, 1, 1)), min(end, date(year, 12, 31))
        if lo > hi:
            continue
        chunk = fresh.filter(pl.col("dt").is_between(lo, hi))
        if chunk.height == 0:
            continue
        outside = reader(date(year, 1, 1), date(year, 12, 31)).filter(
            ~pl.col("dt").is_between(lo, hi)
        )
        merged = pl.concat([outside, chunk], how="vertical_relaxed").sort(
            ["symbol", "dt"]
        ).select(target_schema.names)
        warehouse.write(
            dataset, merged.to_arrow().cast(target_schema), year=year
        )
        written += chunk.height
    return written


def build_bar_daily(warehouse: Warehouse, start: date, end: date) -> int:
    query = WarehouseQuery(warehouse)
    fresh = query.tushare_daily(
        None, start, end, columns=schema.BAR_DAILY.names
    ).select(schema.BAR_DAILY.names)
    return _publish_years(
        warehouse, "bar_daily", schema.BAR_DAILY, fresh, start, end,
        lambda lo, hi: query.bar_daily(None, lo, hi),
    )


def build_valuation_daily(warehouse: Warehouse, start: date, end: date) -> int:
    query = WarehouseQuery(warehouse)
    fresh = query.tushare_daily(
        None, start, end, columns=schema.VALUATION_DAILY.names
    ).select(schema.VALUATION_DAILY.names)
    return _publish_years(
        warehouse, "valuation_daily", schema.VALUATION_DAILY, fresh, start, end,
        lambda lo, hi: query.valuation_daily(None, lo, hi),
    )


def _share_capital_changes(query: WarehouseQuery) -> pl.DataFrame:
    """把 daily_basic 每日股本快照压缩成 PIT 正确的观测变化点。

    ``effective_date`` 是新值首次出现在 daily_basic 的交易日，并非公司行为的
    法律生效日。该接口没有公告日，所以必须保留 null，不能沿用旧 QMT 值。
    """
    return query.share_capital_changes()


def build_share_capital_history(warehouse: Warehouse) -> int:
    target = _share_capital_changes(WarehouseQuery(warehouse))
    if target.height == 0:
        return 0
    _require_unique(target, ("symbol", "effective_date"), "share_capital_history")
    warehouse.write(
        "share_capital_history",
        target.to_arrow().cast(schema.SHARE_CAPITAL_HISTORY),
    )
    return target.height


def build_adjustment_factor_daily(
    warehouse: Warehouse, start: date, end: date
) -> int:
    query = WarehouseQuery(warehouse)
    source = query.adj_factors(start=start, end=end)
    if source.height == 0:
        return 0
    fresh = source.select(
        "symbol", "dt",
        pl.col("cum_factor").alias("price_factor"),
        pl.col("cum_share_factor").alias("share_factor"),
    )
    return _publish_years(
        warehouse, "adjustment_factor_daily", schema.ADJUSTMENT_FACTOR_DAILY,
        fresh, start, end,
        lambda lo, hi: query.adjustment_factor_daily(None, lo, hi),
    )


def daily_reconciliation(
    warehouse: Warehouse, start: date, end: date
) -> dict[str, Reconciliation]:
    query = WarehouseQuery(warehouse)
    bar_source = query.tushare_daily(
        None, start, end, columns=schema.BAR_DAILY.names
    )
    valuation_source = query.tushare_daily(
        None, start, end, columns=schema.VALUATION_DAILY.names
    )
    capital_source = _share_capital_changes(query)
    factor_source = query.adj_factors(start=start, end=end).select(
        "symbol", "dt", pl.col("cum_factor").alias("price_factor"),
        pl.col("cum_share_factor").alias("share_factor"),
    )
    return {
        "bar_daily": reconcile_frames(
            bar_source, query.bar_daily(None, start, end), ("symbol", "dt")
        ),
        "valuation_daily": reconcile_frames(
            valuation_source, query.valuation_daily(None, start, end),
            ("symbol", "dt"),
        ),
        "share_capital_history": reconcile_frames(
            capital_source, query.share_capital_history(),
            ("symbol", "effective_date"),
        ),
        "adjustment_factor_daily": reconcile_frames(
            factor_source, query.adjustment_factor_daily(None, start, end),
            ("symbol", "dt"),
        ),
    }
