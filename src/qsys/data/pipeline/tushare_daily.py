"""三接口合成仓库的 28 列。

**单位换算全部实测确认过**（2026-09-13，与旧源逐键比对）：
  daily.amount     千元 x1000  -> 元
  daily.vol        手           -> 手（不换算，小数是真实精度）
  total/float/free_share  万股 x10000 -> 股
  total_mv / circ_mv      万元 x10000 -> 元
  各比率列                 百分数原样保留

**total_shares 必须直取。** 旧源那一列是 market_cap / close 反算的，
A+H 股因此与原值相差上亿股（华虹宏力 1.91 亿）。反算是明令禁止的，
此处落 Tushare 原值，**不得为了与旧值一致而复刻反算**。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

# (仓库列名, 源列名, 乘数)
_FROM_DAILY = [
    ("open", "open", 1.0), ("high", "high", 1.0), ("low", "low", 1.0),
    ("close", "close", 1.0), ("pre_close", "pre_close", 1.0),
    ("change", "change", 1.0), ("pct_change", "pct_chg", 1.0),
    ("volume", "vol", 1.0), ("amount", "amount", 1000.0),
]
_FROM_BASIC = [
    ("turnover_ratio", "turnover_rate", 1.0),
    ("free_turnover_ratio", "turnover_rate_f", 1.0),
    ("volume_ratio", "volume_ratio", 1.0),
    ("pe", "pe", 1.0), ("pe_ttm", "pe_ttm", 1.0), ("pb", "pb", 1.0),
    ("ps", "ps", 1.0), ("ps_ttm", "ps_ttm", 1.0),
    ("dividend_yield", "dv_ratio", 1.0), ("dividend_yield_ttm", "dv_ttm", 1.0),
    ("total_shares", "total_share", 10000.0),
    ("circulating_shares", "float_share", 10000.0),
    ("free_shares", "free_share", 10000.0),
    ("market_cap", "total_mv", 10000.0),
    ("circulating_market_cap", "circ_mv", 10000.0),
]


def _scaled(frame: pl.DataFrame, mapping: list[tuple[str, str, float]]) -> list[pl.Expr]:
    out = []
    for ours, theirs, mult in mapping:
        if theirs in frame.columns:
            expr = pl.col(theirs) if mult == 1.0 else pl.col(theirs) * mult
            out.append(expr.alias(ours))
        else:
            out.append(pl.lit(None, dtype=pl.Float64).alias(ours))
    return out


def compose_daily(
    daily: pl.DataFrame,
    daily_basic: pl.DataFrame,
    adj_factor: pl.DataFrame,
) -> pl.DataFrame:
    """合成 28 列。以 `daily` 为准：它没有的标的当日没有行情，整行不产生。

    `daily_basic` / `adj_factor` 缺的标的，对应列记 **null**（不是 0）——
    0 是一个真实数值，会被下游当成「成交额为零」「因子为零」使用。
    """
    empty = pl.DataFrame(schema={n: pl.Date if n == "dt" else
                                 (pl.Utf8 if n in ("symbol", "name") else pl.Float64)
                                 for n in schema.TUSHARE_DAILY.names})
    if daily.height == 0:
        return empty

    base = daily.select(
        [
            pl.col("ts_code").alias("symbol"),
            pl.col("trade_date").str.strptime(pl.Date, "%Y%m%d").alias("dt"),
        ]
        + _scaled(daily, _FROM_DAILY)
    )

    if daily_basic.height:
        basic = daily_basic.select(
            [pl.col("ts_code").alias("symbol")] + _scaled(daily_basic, _FROM_BASIC)
        )
        base = base.join(basic, on="symbol", how="left")
    else:
        base = base.with_columns(_scaled(pl.DataFrame(), _FROM_BASIC))

    if adj_factor.height:
        fac = adj_factor.select([pl.col("ts_code").alias("symbol"),
                                 pl.col("adj_factor").alias("factor")])
        base = base.join(fac, on="symbol", how="left")
    else:
        base = base.with_columns(pl.lit(None, dtype=pl.Float64).alias("factor"))

    # name 不在这三个接口里——由 namechange 单独成表（Task 7），此处留 null。
    base = base.with_columns(pl.lit(None, dtype=pl.Utf8).alias("name"))
    return base.select(schema.TUSHARE_DAILY.names).sort(["symbol", "dt"])


def write_year(warehouse: Warehouse, frame: pl.DataFrame, year: int) -> int:
    """按年合并写入。分区是整年覆盖写的，必须与同年既有数据合并。"""
    from qsys.data.query import WarehouseQuery

    if frame.height == 0:
        return 0
    query = WarehouseQuery(warehouse)
    lo, hi = date(year, 1, 1), date(year, 12, 31)
    merged = (
        pl.concat([query.tushare_daily(None, lo, hi), frame], how="vertical")
        .unique(subset=["symbol", "dt"], keep="last", maintain_order=True)
        .sort(["symbol", "dt"])
        .select(schema.TUSHARE_DAILY.names)
    )
    warehouse.write("tushare_daily",
                    merged.to_arrow().cast(schema.TUSHARE_DAILY), year=year)
    log.info("tushare_daily.written", year=year, rows=frame.height)
    return frame.height
