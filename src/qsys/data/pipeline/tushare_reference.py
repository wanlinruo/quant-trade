"""Tushare 参考事实落库。

外部值只做列名、日期和明确单位换算；不反推、不填充。分区表按年合并，
无分区表原子覆盖。每日同步的接线适配器位于 ``tushare_sync``。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.storage import Warehouse
from qsys.markets.ashare import board_of

log = structlog.get_logger(__name__)


def _merge_write(warehouse: Warehouse, dataset: str, frame: pl.DataFrame,
                 arrow_schema, keys: list[str], reader) -> int:
    if frame.height == 0:
        return 0
    total = 0
    for year in sorted({day.year for day in frame["dt"].to_list()}):
        lo, hi = date(year, 1, 1), date(year, 12, 31)
        chunk = frame.filter(pl.col("dt").is_between(lo, hi))
        merged = (
            pl.concat([reader(lo, hi), chunk], how="vertical_relaxed")
            .unique(subset=keys, keep="last", maintain_order=True)
            .sort(keys)
            .select(arrow_schema.names)
        )
        warehouse.write(dataset, merged.to_arrow().cast(arrow_schema), year=year)
        total += chunk.height
    return total


def _date_expr(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d", strict=False)


def ingest_price_limits(provider, warehouse: Warehouse, days: Sequence[date]) -> int:
    from qsys.data.query import WarehouseQuery

    frames = []
    for day in days:
        raw = provider.stk_limit(day)
        if raw.height:
            frames.append(raw.select(
                pl.col("ts_code").alias("symbol"),
                _date_expr("trade_date").alias("dt"),
                pl.col("pre_close"), pl.col("up_limit").alias("limit_up"),
                pl.col("down_limit").alias("limit_down"),
            ))
    if not frames:
        return 0
    query = WarehouseQuery(warehouse)
    written = _merge_write(
        warehouse, "price_limits_daily", pl.concat(frames), schema.PRICE_LIMITS_DAILY,
        ["symbol", "dt"], lambda lo, hi: query.price_limits(None, lo, hi),
    )
    log.info("price_limits.ingested", rows=written, days=len(days))
    return written


def ingest_suspensions(provider, warehouse: Warehouse, days: Sequence[date]) -> int:
    from qsys.data.query import WarehouseQuery

    frames = []
    for day in days:
        raw = provider.suspend_d(day)
        if not raw.height:
            continue
        timing = (pl.col("suspend_timing").cast(pl.Utf8)
                  if "suspend_timing" in raw.columns else pl.lit(None, dtype=pl.Utf8))
        frames.append(raw.select(
            pl.col("ts_code").alias("symbol"),
            _date_expr("trade_date").alias("dt"),
            pl.col("suspend_type").cast(pl.Utf8), timing.alias("suspend_timing"),
        ))
    if not frames:
        return 0
    query = WarehouseQuery(warehouse)
    written = _merge_write(
        warehouse, "suspend_daily", pl.concat(frames), schema.SUSPEND_DAILY,
        ["symbol", "dt"], lambda lo, hi: query.suspensions(None, lo, hi),
    )
    log.info("suspensions.ingested", rows=written, days=len(days))
    return written


def ingest_index_daily(provider, warehouse: Warehouse, ts_codes: Sequence[str],
                       start: date, end: date) -> int:
    from qsys.data.query import WarehouseQuery

    frames = []
    for code in ts_codes:
        raw = provider.index_daily(code, start, end)
        if not raw.height:
            continue
        frames.append(raw.select(
            pl.col("ts_code").alias("symbol"),
            _date_expr("trade_date").alias("dt"),
            "open", "high", "low", "close",
            pl.col("vol").cast(pl.Float64).alias("volume"),
            (pl.col("amount") * 1000.0).alias("amount"),
        ))
    if not frames:
        return 0
    query = WarehouseQuery(warehouse)
    written = _merge_write(
        warehouse, "index_bars", pl.concat(frames), schema.INDEX_BARS,
        ["symbol", "dt"], lambda lo, hi: query.index_bars(None, lo, hi),
    )
    log.info("index_bars.ingested", rows=written, codes=list(ts_codes))
    return written


def ingest_instruments(provider, warehouse: Warehouse) -> int:
    parts = [frame for status in ("L", "D")
             if (frame := provider.stock_basic(status)).height]
    if not parts:
        return 0
    frame = pl.concat(parts, how="diagonal_relaxed")
    out = frame.select(
        pl.col("ts_code").alias("symbol"), pl.col("name").cast(pl.Utf8),
        pl.col("ts_code").str.split(".").list.last().alias("market"),
        _date_expr("list_date").alias("list_date"),
        _date_expr("delist_date").alias("delist_date"),
        pl.col("ts_code").map_elements(
            lambda symbol: board_of(symbol.split(".")[0]), return_dtype=pl.Utf8
        ).alias("board"),
    ).unique(subset=["symbol"], keep="last").sort("symbol")
    warehouse.write("instruments", out.to_arrow().cast(schema.INSTRUMENTS))
    log.info("instruments.ingested", rows=out.height)
    return out.height


def ingest_name_history(provider, warehouse: Warehouse) -> int:
    raw = provider.namechange()
    if not raw.height:
        return 0
    out = raw.select(
        pl.col("ts_code").alias("symbol"), pl.col("name").cast(pl.Utf8),
        _date_expr("start_date").alias("start_date"),
        _date_expr("end_date").alias("end_date"),
        _date_expr("ann_date").alias("ann_date"),
        pl.col("change_reason").cast(pl.Utf8),
    ).unique(subset=["symbol", "start_date"], keep="last").sort(["symbol", "start_date"])
    warehouse.write("name_history", out.to_arrow().cast(schema.NAME_HISTORY))
    log.info("name_history.ingested", rows=out.height)
    return out.height


def st_on(name_history: pl.DataFrame, day: date) -> pl.DataFrame:
    empty = pl.DataFrame(schema={"symbol": pl.Utf8, "is_st": pl.Boolean, "name": pl.Utf8})
    if not name_history.height:
        return empty
    covering = name_history.filter(
        (pl.col("start_date") <= day)
        & (pl.col("end_date").is_null() | (pl.col("end_date") >= day))
    )
    if not covering.height:
        return empty
    latest = covering.sort(["symbol", "start_date"]).group_by(
        "symbol", maintain_order=True
    ).last()
    return latest.select(
        "symbol",
        (pl.col("name").str.starts_with("ST")
         | pl.col("name").str.starts_with("*ST")).alias("is_st"),
        "name",
    ).sort("symbol")


def ingest_limit_list(provider, warehouse: Warehouse, days: Sequence[date],
                      limit_type: str = "U") -> int:
    """直取涨停列表；接口自 2020 年起且不含 ST 股票统计。"""
    from qsys.data.query import WarehouseQuery

    frames = []
    for day in days:
        raw = provider.limit_list_d(day, limit_type)
        if raw.height:
            frames.append(raw.select(
                pl.col("ts_code").alias("symbol"),
                _date_expr("trade_date").alias("dt"),
                pl.lit(limit_type, dtype=pl.Utf8).alias("limit_type"),
                pl.col("limit_times").cast(pl.Int64),
                pl.col("open_times").cast(pl.Int64),
                pl.col("first_time").cast(pl.Utf8),
                pl.col("fd_amount"), pl.col("limit_amount"), pl.col("float_mv"),
            ))
    if not frames:
        return 0
    query = WarehouseQuery(warehouse)
    written = _merge_write(
        warehouse, "limit_list_daily", pl.concat(frames), schema.LIMIT_LIST_DAILY,
        ["symbol", "dt", "limit_type"], lambda lo, hi: query.limit_list(None, lo, hi),
    )
    log.info("limit_list.ingested", rows=written, days=len(days), limit_type=limit_type)
    return written
