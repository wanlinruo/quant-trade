"""估值：从股本与收盘价派生，外加当日快照采集。

akshare 的 stock_zh_a_spot_em 一次调用返回全市场当日行情与估值，
比逐只查询快几个数量级。

历史估值不去找现成的市值源（免费源要么只有一年，要么逐只查询对全市场
不可行），而是**从股本反推**：市值 = 收盘价 × 股本。收盘价已有全部历史
且经 QMT 分钟线交叉验证，股本取自 QMT 财务表。见 build_valuation。

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


_LOT = 100  # 日线 volume 单位是手，换算成股要乘 100


def build_valuation(warehouse: Warehouse, start: date, end: date) -> int:
    """从收盘价与股本派生历史估值。返回写入行数。

    市值 = 收盘价 × 股本 / 1e8，单位**亿元**，与聚宽 get_valuation 一致，
    策略阈值 70 / 520 可直接比较。

    股本按**生效日**向前找最近一条（as-of join）：某日的股本是生效日不晚于
    该日的最后一条记录。生效日之前没有任何记录的交易日不产出行——
    宁可缺，不可编。
    """
    query = WarehouseQuery(warehouse)
    capital = query.share_capital()
    if capital.height == 0:
        return 0

    written = 0
    for year in range(start.year, end.year + 1):
        lo_year, hi_year = date(year, 1, 1), date(year, 12, 31)
        lo, hi = max(start, lo_year), min(end, hi_year)
        if lo > hi:
            continue

        bars = query.daily_bars(None, lo, hi)
        if bars.height == 0:
            continue

        # join_asof 要求两边都按连接键排序；by="symbol" 保证不串到别人的股本
        left = bars.select(["symbol", "dt", "close", "volume"]).sort("dt")
        right = capital.select(["symbol", "dt", "total", "circulating"]).sort("dt")
        joined = (
            left.join_asof(right, on="dt", by="symbol", strategy="backward")
            .drop_nulls(["total", "circulating"])
            .filter((pl.col("total") > 0) & (pl.col("circulating") > 0))
        )
        if joined.height == 0:
            continue

        fresh = joined.select(
            pl.col("dt"),
            pl.col("symbol"),
            pl.col("close"),
            (pl.col("close") * pl.col("total") / 1e8).alias("market_cap"),
            (pl.col("close") * pl.col("circulating") / 1e8).alias(
                "circulating_market_cap"
            ),
            (pl.col("volume") * _LOT / pl.col("circulating") * 100.0).alias(
                "turnover_ratio"
            ),
        ).select(_COLUMNS)

        merged = (
            pl.concat([query.valuation(lo_year, hi_year), fresh], how="vertical")
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
