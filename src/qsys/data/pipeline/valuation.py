"""估值：从 Tushare ``daily_basic`` 直取字段投影，外加独立快照采集。

akshare 的 stock_zh_a_spot_em 一次调用返回全市场当日行情与估值，
比逐只查询快几个数量级。

历史估值的总市值、流通市值和换手率已经随 ``tushare_daily`` 落库，禁止再用
收盘价和股本反算。这里只做单位换算：元 / 1e8 = 亿元。

`ingest_valuation_snapshot` 是当日快照的独立通道，保留作对照。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.providers.akshare_provider import AkshareProvider
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

_COLUMNS = [
    "dt",
    "symbol",
    "close",
    "market_cap",
    "circulating_market_cap",
    "turnover_ratio",
]


def ingest_valuation_snapshot(
    provider: AkshareProvider, warehouse: Warehouse, dt: date
) -> int:
    """采集 dt 当日的全市场估值快照。返回本次写入的标的数。"""
    snapshot = provider.fetch_valuation_snapshot()
    if snapshot.height == 0:
        return 0

    fresh = snapshot.with_columns(pl.lit(dt).cast(pl.Date).alias("dt")).select(_COLUMNS)
    existing = WarehouseQuery(warehouse).valuation(
        date(dt.year, 1, 1), date(dt.year, 12, 31)
    )
    merged = (
        pl.concat([existing, fresh], how="vertical")
        # 同日重采覆盖旧值；maintain_order 让「保留最后一条」有确定语义
        .unique(subset=["dt", "symbol"], keep="last", maintain_order=True)
        .sort(["dt", "symbol"])
        .select(_COLUMNS)
    )
    warehouse.write(
        "valuation_snapshot",
        merged.to_arrow().cast(schema.VALUATION_SNAPSHOT),
        year=dt.year,
    )
    log.info("valuation.ingested", dt=str(dt), rows=fresh.height)
    return fresh.height


def build_valuation(warehouse: Warehouse, start: date, end: date) -> int:
    """投影 Tushare 每日指标；缺失保持缺失，不反算。"""
    query = WarehouseQuery(warehouse)
    written = 0
    for year in range(start.year, end.year + 1):
        lo_year, hi_year = date(year, 1, 1), date(year, 12, 31)
        lo, hi = max(start, lo_year), min(end, hi_year)
        if lo > hi:
            continue

        bars = query.tushare_daily(None, lo, hi, columns=[
            "dt", "symbol", "close", "market_cap",
            "circulating_market_cap", "turnover_ratio",
        ])
        if bars.height == 0:
            continue
        fresh = bars.drop_nulls([
            "market_cap", "circulating_market_cap", "turnover_ratio"
        ]).with_columns(
            (pl.col("market_cap") / 1e8).alias("market_cap"),
            (pl.col("circulating_market_cap") / 1e8).alias(
                "circulating_market_cap"
            ),
        ).select(_COLUMNS)

        outside = query.valuation(lo_year, hi_year).filter(
            ~pl.col("dt").is_between(lo, hi)
        )
        merged = (
            pl.concat([outside, fresh], how="vertical")
            .unique(subset=["dt", "symbol"], keep="last", maintain_order=True)
            .sort(["dt", "symbol"])
            .select(_COLUMNS)
        )
        warehouse.write(
            "valuation_snapshot",
            merged.to_arrow().cast(schema.VALUATION_SNAPSHOT),
            year=year,
        )
        written += fresh.height

    log.info("valuation.built", rows=written, start=str(start), end=str(end))
    return written
