"""标的主表流水线。

必须保留已退市标的：股票池在历史每一天都要包含当时仍在市的全部股票，
否则幸存者偏差会使回测系统性虚高。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.providers.base import ReferenceProvider
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

COLUMNS = ["symbol", "name", "market", "list_date", "delist_date", "board"]


def build_instruments(
    provider: ReferenceProvider, warehouse: Warehouse, as_of: date
) -> int:
    """`as_of` 是取这份快照的日期，显式传入而不是内部取 today()。

    内部取当天会让这个函数的结果依赖墙上时钟，测试要么锁时间要么失真。
    """
    raw = provider.fetch_instruments().select(COLUMNS)

    # 同一 symbol 可能来自多个来源（在市表 / 退市表），合并时保留信息最全的一行：
    # 按非空字段数降序排序后取第一行。
    informativeness = (
        pl.col("name").is_not_null().cast(pl.Int32)
        + pl.col("list_date").is_not_null().cast(pl.Int32)
        + pl.col("delist_date").is_not_null().cast(pl.Int32)
    )
    merged = (
        raw.with_columns(informativeness.alias("_info"))
        .sort(["symbol", "_info"], descending=[False, True])
        .unique(subset=["symbol"], keep="first", maintain_order=True)
        .drop("_info")
        .sort("symbol")
        .select(COLUMNS)
        .with_columns(pl.lit(as_of, dtype=pl.Date).alias("as_of"))
    )

    warehouse.write("instruments", merged.to_arrow().cast(schema.INSTRUMENTS))
    log.info("instruments.built", provider=provider.name, rows=merged.height)
    return merged.height


def alive_on(instruments: pl.DataFrame, day: date) -> pl.DataFrame:
    """返回 `day` 当天仍在市的标的（上市日 <= day <= 退市日；null 视为无界）。"""
    return instruments.filter(
        (pl.col("list_date").is_null() | (pl.col("list_date") <= day))
        & (pl.col("delist_date").is_null() | (pl.col("delist_date") >= day))
    )
