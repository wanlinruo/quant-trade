"""阶段 5：并行构建规范价格限制、证券状态、市场标志与市场生态。

只写新目标表，不覆盖旧事实或派生表，也不切换生产消费者。未知状态保持
``null``；只有来源事实或当日行情能证明的状态才写 true/false。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Callable

import polars as pl
import pyarrow as pa

from qsys.data import schema
from qsys.data.pipeline.canonical_reference import Reconciliation, reconcile_frames
from qsys.data.pipeline.flags import _compute
from qsys.data.pipeline.regime import (
    _aggregate,
    _index_slope,
    _in_breadth_universe,
    _keep_boards,
)
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

_LOOKBACK_DAYS = 150


def _require_unique(frame: pl.DataFrame, keys: tuple[str, ...], source: str) -> None:
    duplicates = frame.height - frame.n_unique(subset=list(keys))
    if duplicates:
        raise RuntimeError(f"{source} 含 {duplicates} 行重复键 {keys}，拒绝发布规范表")
    nulls = sum(frame[key].null_count() for key in keys)
    if nulls:
        raise RuntimeError(f"{source} 主键含 {nulls} 个 null，拒绝发布规范表")


def canonical_state_preflight(
    warehouse: Warehouse, start: date, end: date
) -> tuple[str, ...]:
    """在首次目标写入前检查规范链路所需来源和键。"""
    if start > end:
        return ("start 晚于 end",)
    query = WarehouseQuery(warehouse)
    issues: list[str] = []
    dense = (
        ("bar_daily", query.bar_daily(None, start, end), ("symbol", "dt")),
        ("price_limits_daily", query.price_limits(None, start, end), ("symbol", "dt")),
        ("stock_st_daily", query.stock_st(None, start, end), ("symbol", "dt")),
        ("adjustment_factor_daily", query.adjustment_factor_daily(None, start, end),
         ("symbol", "dt")),
    )
    for name, frame, keys in dense:
        if frame.height == 0:
            issues.append(f"{name} 在构建范围内为空")
            continue
        duplicates = frame.height - frame.n_unique(subset=list(keys))
        if duplicates:
            issues.append(f"{name} 有 {duplicates} 行重复键")
        nulls = sum(frame[key].null_count() for key in keys)
        if nulls:
            issues.append(f"{name} 主键有 {nulls} 个 null")
    # 稀疏事实允许零行，但必须存在已发布数据集，才能把“无事件”与“未采集”区分开。
    for name in ("suspend_daily", "delisting_pool", "limit_list_daily"):
        if not (warehouse.root / name).exists():
            issues.append(f"{name} 尚未发布，不能把缺失解释为无事件")
    suspensions = query.suspensions(None, start, end)
    if suspensions.height:
        event_keys = ["symbol", "dt", "suspend_type"]
        duplicate_events = suspensions.height - suspensions.n_unique(
            subset=event_keys
        )
        if duplicate_events:
            issues.append(f"suspend_daily 有 {duplicate_events} 个重复停复牌事件")
        invalid_types = sorted(
            set(suspensions["suspend_type"].drop_nulls().to_list()) - {"S", "R"}
        )
        if invalid_types:
            issues.append(f"suspend_daily 含未知事件类型 {invalid_types}")
    return tuple(issues)


def require_canonical_state_sources(
    warehouse: Warehouse, start: date, end: date
) -> None:
    issues = canonical_state_preflight(warehouse, start, end)
    if issues:
        raise RuntimeError("规范状态预检失败：" + "；".join(issues))


def _publish_years(
    warehouse: Warehouse,
    dataset: str,
    target_schema: pa.Schema,
    fresh: pl.DataFrame,
    start: date,
    end: date,
    reader: Callable[[date, date], pl.DataFrame],
    keys: tuple[str, ...],
) -> int:
    if fresh.height == 0:
        return 0
    _require_unique(fresh, keys, dataset)
    written = 0
    for year in range(start.year, end.year + 1):
        lo, hi = max(start, date(year, 1, 1)), min(end, date(year, 12, 31))
        chunk = fresh.filter(pl.col("dt").is_between(lo, hi))
        if chunk.height == 0:
            continue
        outside = reader(date(year, 1, 1), date(year, 12, 31)).filter(
            ~pl.col("dt").is_between(lo, hi)
        )
        merged = pl.concat([outside, chunk], how="vertical_relaxed").unique(
            subset=list(keys), keep="last"
        ).sort(list(keys)).select(target_schema.names)
        warehouse.write(dataset, merged.to_arrow().cast(target_schema), year=year)
        written += chunk.height
    return written


def build_price_limit_daily(warehouse: Warehouse, start: date, end: date) -> int:
    query = WarehouseQuery(warehouse)
    fresh = query.price_limits(None, start, end).select(schema.PRICE_LIMIT_DAILY.names)
    return _publish_years(
        warehouse, "price_limit_daily", schema.PRICE_LIMIT_DAILY, fresh, start, end,
        lambda lo, hi: query.price_limit_daily(None, lo, hi), ("symbol", "dt"),
    )


def _security_status_frame(
    warehouse: Warehouse, start: date, end: date
) -> pl.DataFrame:
    query = WarehouseQuery(warehouse)
    bars = query.bar_daily(None, start, end).select("symbol", "dt").with_columns(
        pl.lit(True).alias("_has_bar")
    )
    st = query.stock_st(None, start, end).select(
        "symbol", "dt", pl.col("is_st").alias("_is_st")
    )
    suspension_events = query.suspensions(None, start, end).select(
        "symbol", "dt", "suspend_type"
    )
    _require_unique(
        suspension_events, ("symbol", "dt", "suspend_type"), "suspend_daily"
    )
    suspensions = suspension_events.group_by("symbol", "dt").agg(
        (pl.col("suspend_type") == "S").any().alias("_has_suspend"),
        (pl.col("suspend_type") == "R").any().alias("_has_resume"),
    )
    delisting = query.delisting_pool(start, end).filter(
        pl.col("ann_date").is_null() | (pl.col("ann_date") <= pl.col("dt"))
    ).select("symbol", "dt").unique().with_columns(
        pl.lit(True).alias("_is_delisting")
    )
    for name, frame in (("bar_daily", bars), ("stock_st_daily", st),
                        ("suspend_daily", suspensions), ("delisting_pool", delisting)):
        _require_unique(frame, ("symbol", "dt"), name)
    keys = pl.concat(
        [frame.select("symbol", "dt") for frame in (bars, st, suspensions, delisting)],
        how="vertical_relaxed",
    ).unique().sort(["symbol", "dt"])
    frame = keys.join(bars, on=["symbol", "dt"], how="left")
    frame = frame.join(st, on=["symbol", "dt"], how="left")
    frame = frame.join(suspensions, on=["symbol", "dt"], how="left")
    frame = frame.join(delisting, on=["symbol", "dt"], how="left")
    is_delisting = pl.col("_is_delisting").fill_null(False)
    return frame.select(
        "symbol", "dt",
        pl.when(is_delisting).then(False).otherwise(pl.col("_is_st")).alias("is_st"),
        # suspend_d 是事件表；同一标的日可以同时有 S 和 R。日度状态表示
        # “当日曾发生停牌”，因此 S 优先；只有 R 而没有 S 才表示 false。
        pl.when(pl.col("_has_suspend") == True).then(True)  # noqa: E712
        .when(pl.col("_has_resume") == True).then(False)  # noqa: E712
        .when(pl.col("_has_bar") == True).then(False)  # noqa: E712
        .otherwise(pl.lit(None, dtype=pl.Boolean)).alias("is_suspended"),
        is_delisting.alias("is_delisting"),
        pl.when(is_delisting).then(pl.lit("delisting_pool_override"))
        # 保持现有公开值 ``stock_st``，数据集来源由字段血缘另行记录。
        .when(pl.col("_is_st").is_not_null()).then(pl.lit("stock_st"))
        .otherwise(None).alias("st_source"),
        pl.when(
            (pl.col("_has_suspend") == True)  # noqa: E712
            | (pl.col("_has_resume") == True)  # noqa: E712
        ).then(pl.lit("suspend_daily"))
        .when(pl.col("_has_bar") == True).then(pl.lit("bar_daily_observed"))  # noqa: E712
        .otherwise(None).alias("suspension_source"),
        pl.lit("delisting_pool").alias("delisting_source"),
    ).select(schema.SECURITY_STATUS_DAILY.names)


def build_security_status_daily(
    warehouse: Warehouse, start: date, end: date
) -> int:
    query = WarehouseQuery(warehouse)
    fresh = _security_status_frame(warehouse, start, end)
    return _publish_years(
        warehouse, "security_status_daily", schema.SECURITY_STATUS_DAILY,
        fresh, start, end,
        lambda lo, hi: query.security_status_daily(None, lo, hi), ("symbol", "dt"),
    )


def _market_flags_frame(
    warehouse: Warehouse, start: date, end: date
) -> pl.DataFrame:
    query = WarehouseQuery(warehouse)
    back = start - timedelta(days=_LOOKBACK_DAYS)
    bars = query.bar_daily(None, back, end)
    if bars.height == 0:
        return pl.DataFrame(schema={field.name: pl.from_arrow(
            pa.array([], type=field.type)
        ).dtype for field in schema.MARKET_FLAGS_DAILY})
    status = query.security_status_daily(None, back, end).select(
        "symbol", "dt", "is_st", "st_source"
    )
    limits = query.price_limit_daily(None, back, end).select(
        "symbol", "dt", pl.col("pre_close").alias("_direct_pre_close"),
        pl.col("limit_up").alias("_direct_limit_up"),
        pl.col("limit_down").alias("_direct_limit_down"),
    )
    frame = bars.join(status, on=["symbol", "dt"], how="left")
    frame = frame.join(limits, on=["symbol", "dt"], how="left")
    listed = query.limit_list(None, back, end)
    if listed.height:
        classifications = listed.select(
            "symbol", "dt", pl.col("limit_type").alias("_limit_type")
        )
        _require_unique(classifications, ("symbol", "dt"), "limit_list_daily")
        available = listed.select("dt").unique().with_columns(
            pl.lit(True).alias("_limit_list_available")
        )
        frame = frame.join(classifications, on=["symbol", "dt"], how="left")
        frame = frame.join(available, on="dt", how="left")
    else:
        frame = frame.with_columns(
            pl.lit(None, dtype=pl.Utf8).alias("_limit_type"),
            pl.lit(False).alias("_limit_list_available"),
        )
    return _compute(frame).filter(pl.col("dt").is_between(start, end)).sort(
        ["symbol", "dt"]
    ).select(schema.MARKET_FLAGS_DAILY.names)


def build_market_flags_daily(
    warehouse: Warehouse, start: date, end: date
) -> int:
    query = WarehouseQuery(warehouse)
    fresh = _market_flags_frame(warehouse, start, end)
    return _publish_years(
        warehouse, "market_flags_daily", schema.MARKET_FLAGS_DAILY,
        fresh, start, end,
        lambda lo, hi: query.market_flags_daily(None, lo, hi), ("symbol", "dt"),
    )


def _market_regime_frame(
    warehouse: Warehouse, start: date, end: date, boards: set[str] | None = None
) -> pl.DataFrame:
    query = WarehouseQuery(warehouse)
    back = start - timedelta(days=30)
    bars = _keep_boards(_in_breadth_universe(query.bar_daily(None, back, end)), boards)
    flags = _keep_boards(
        _in_breadth_universe(query.market_flags_daily(None, back, end)), boards
    )
    if bars.height == 0 or flags.height == 0:
        return pl.DataFrame(schema={field.name: pl.from_arrow(
            pa.array([], type=field.type)
        ).dtype for field in schema.MARKET_REGIME_DAILY})
    factors = query.adjustment_factor_daily(None, back, end).rename(
        {"price_factor": "cum_factor", "share_factor": "cum_share_factor"}
    )
    return _aggregate(bars, flags, factors).filter(
        pl.col("dt").is_between(start, end)
    ).join(_index_slope(query, back, end), on="dt", how="left").sort("dt").select(
        schema.MARKET_REGIME_DAILY.names
    )


def build_market_regime_daily(
    warehouse: Warehouse, start: date, end: date,
    boards: set[str] | None = None,
) -> int:
    query = WarehouseQuery(warehouse)
    fresh = _market_regime_frame(warehouse, start, end, boards)
    return _publish_years(
        warehouse, "market_regime_daily", schema.MARKET_REGIME_DAILY,
        fresh, start, end, query.market_regime_daily, ("dt",),
    )


def state_reconciliation(
    warehouse: Warehouse, start: date, end: date
) -> dict[str, Reconciliation]:
    """逐键逐字段比较来源/旧派生与阶段 5 新目标。"""
    query = WarehouseQuery(warehouse)
    return {
        "price_limit_daily": reconcile_frames(
            query.price_limits(None, start, end),
            query.price_limit_daily(None, start, end), ("symbol", "dt"),
        ),
        "security_status_daily": reconcile_frames(
            _security_status_frame(warehouse, start, end),
            query.security_status_daily(None, start, end), ("symbol", "dt"),
        ),
        "market_flags_daily": reconcile_frames(
            query.daily_flags(start, end),
            query.market_flags_daily(None, start, end), ("symbol", "dt"),
        ),
        "market_regime_daily": reconcile_frames(
            query.market_regime(start, end), query.market_regime_daily(start, end),
            ("dt",),
        ),
    }
