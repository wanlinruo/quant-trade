"""退市整理期池：每个交易日处于退市整理期的标的，每晚同步后刷新。

**候选池剔除的唯一出处**（`WarehouseQuery.delisting_symbols` 只读这张表）。

纯派生：只认 `name_history`（Tushare namechange）里 change_reason='退市整理期'
的区间，交易日取自 `tushare_daily`，外加调用方给出的下一个交易日——T 日开盘前
选股要用到 T 日的池子，而 T 日日线当晚才有。不从名称里找「退」字（D-11）。

池子只存在整理期的标的，某天 0 行可能是真没有；因此每个年分区在 Parquet
元数据里记录实际构建过的交易日区间，查询据此区分「没有」与「没建」。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

DELISTING_REASON = "退市整理期"


def delisting_rows(history: pl.DataFrame, sessions: Sequence[date]) -> pl.DataFrame:
    """`sessions` 每天处于退市整理期的 (dt, symbol, name, start_date, end_date, ann_date)。"""
    periods = history.filter(pl.col("change_reason") == DELISTING_REASON).select(
        "symbol", "name", "start_date", "end_date", "ann_date")
    if periods.height == 0 or not sessions:
        return pl.from_arrow(schema.DELISTING_POOL.empty_table())
    days = pl.DataFrame({"dt": list(sessions)}, schema={"dt": pl.Date})
    return (
        days.join(periods, how="cross")
        .filter((pl.col("start_date") <= pl.col("dt"))
                & (pl.col("end_date").is_null() | (pl.col("end_date") >= pl.col("dt"))))
        .select(schema.DELISTING_POOL.names)
        .unique(subset=["dt", "symbol"], keep="first", maintain_order=True)
        .sort(["dt", "symbol"])
    )


def build_delisting_pool(warehouse: Warehouse, start: date, end: date, *,
                         extra_sessions: Sequence[date] = ()) -> int:
    """重建 [start, end] 内每个交易日（及 `extra_sessions`）的池子。返回本次构建的行数。

    同一年里已构建、但不在本次范围内的交易日原样保留，覆盖区间取并集。
    """
    query = WarehouseQuery(warehouse)
    daily = query.tushare_daily(None, start, end, columns=["dt"])
    sessions = sorted(set(daily["dt"].to_list()) | set(extra_sessions))
    if not sessions:
        log.warning("delisting_pool.no_sessions", start=str(start), end=str(end))
        return 0
    fresh = delisting_rows(query.name_history(), sessions)
    for year in sorted({day.year for day in sessions}):
        built = [d for d in sessions if d.year == year]
        previous = query.delisting_pool_coverage(year)
        lo, hi = date(year, 1, 1), date(year, 12, 31)
        keep = query.delisting_pool(lo, hi).filter(~pl.col("dt").is_in(built))
        merged = (pl.concat([keep, fresh.filter(pl.col("dt").dt.year() == year)],
                            how="vertical_relaxed")
                  .select(schema.DELISTING_POOL.names).sort(["dt", "symbol"]))
        first, last = built[0], built[-1]
        if previous is not None:
            first, last = min(first, previous[0]), max(last, previous[1])
        table = merged.to_arrow().cast(schema.DELISTING_POOL).replace_schema_metadata({
            "built_from": first.isoformat(), "built_through": last.isoformat()})
        warehouse.write("delisting_pool", table, year=year)
    log.info("delisting_pool.built", start=str(sessions[0]), end=str(sessions[-1]),
             sessions=len(sessions), rows=fresh.height)
    return fresh.height
