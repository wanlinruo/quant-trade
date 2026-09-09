"""估值快照采集。

akshare 的 stock_zh_a_spot_em 一次调用返回全市场当日行情与估值，
比逐只查询快几个数量级。

**已知缺口**：只有当日数据。历史估值从首次采集之日起累积，更早的历史
需另寻数据源。策略中依赖历史市值的过滤（check_high_open 的
market_cap >= 70亿 / circulating <= 520亿）在缺口区间内无法生效——
移植版与聚宽版会在这里产生可解释的差异。
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
