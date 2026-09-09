"""市场生态因子。

策略的 S7 Gate 完全依赖这些指标判断当日市场环境：
`index_ma20_slope_5d`、`prior_limitup_nextday_return_mean`、
`market_median_return_1d`、`touched_limit_up_count`、`market_advance_ratio`。

派生自 bars_daily + daily_flags，可完全重算。全部用 polars 向量化：
全市场多年逐股 Python 循环在 1.9 GB 内存的机器上跑不动。

与 flags 同样**逐年处理**，并向前多读 `_LOOKBACK_DAYS` 天日历日——
`prior_limitup_nextday_return` 要看昨日的涨停状态，跨年那一天没有回看
就会算空。

**不含 index_ma20_slope_5d**：那需要沪深 300 指数日线，指数数据源与个股
不同，归入后续计划。
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

_LOOKBACK_DAYS = 30

_REGIME_COLUMNS = [
    "dt",
    "limit_up_count",
    "limit_down_count",
    "limit_up_down_ratio",
    "touched_limit_up_count",
    "broken_board_count",
    "broken_board_rate",
    "first_board_count",
    "market_advance_ratio",
    "market_median_return_1d",
    "market_total_money",
    "prior_limitup_nextday_return_mean",
    "prior_limitup_nextday_return_median",
    "valid_stock_count",
]


def build_market_regime(warehouse: Warehouse, start: date, end: date) -> int:
    query = WarehouseQuery(warehouse)

    written = 0
    for year in range(start.year, end.year + 1):
        lo_year, hi_year = date(year, 1, 1), date(year, 12, 31)
        lo = max(start, lo_year)
        hi = min(end, hi_year)
        if lo > hi:
            continue

        back = lo - timedelta(days=_LOOKBACK_DAYS)
        bars = query.daily_bars(None, back, hi)
        flags = query.daily_flags(back, hi)
        if bars.height == 0 or flags.height == 0:
            continue

        daily = _aggregate(bars, flags).filter(pl.col("dt").is_between(lo, hi))
        if daily.height == 0:
            continue

        chunk = daily.sort("dt").select(_REGIME_COLUMNS)
        # 与 flags 同理：分区整年覆盖写，只重算一段时必须先合并同年既有数据
        merged = (
            pl.concat([query.market_regime(lo_year, hi_year), chunk], how="vertical")
            .unique(subset=["dt"], keep="last", maintain_order=True)
            .sort("dt")
            .select(_REGIME_COLUMNS)
        )
        warehouse.write(
            "market_regime", merged.to_arrow().cast(schema.MARKET_REGIME), year=year
        )
        written += chunk.height

    log.info("market_regime.built", rows=written, start=str(start), end=str(end))
    return written


def _aggregate(bars: pl.DataFrame, flags: pl.DataFrame) -> pl.DataFrame:
    joined = (
        bars.join(flags, on=["symbol", "dt"], how="inner")
        .sort(["symbol", "dt"])
        .with_columns(
            (pl.col("close") / pl.col("prev_close") - 1.0).alias("ret_1d"),
            pl.col("is_limit_up").shift(1).over("symbol").alias("was_limit_up"),
        )
    )

    return (
        joined.group_by("dt")
        .agg(
            pl.col("is_limit_up").sum().cast(pl.Int64).alias("limit_up_count"),
            pl.col("is_limit_down").sum().cast(pl.Int64).alias("limit_down_count"),
            pl.col("touched_limit_up")
            .sum()
            .cast(pl.Int64)
            .alias("touched_limit_up_count"),
            pl.col("is_broken_board").sum().cast(pl.Int64).alias("broken_board_count"),
            pl.col("is_first_board").sum().cast(pl.Int64).alias("first_board_count"),
            pl.len().cast(pl.Int64).alias("valid_stock_count"),
            pl.col("amount").sum().alias("market_total_money"),
            (pl.col("ret_1d") > 0).sum().alias("_up"),
            (pl.col("ret_1d") < 0).sum().alias("_down"),
            pl.col("ret_1d").median().alias("market_median_return_1d"),
            # 昨日涨停股今日的溢价——只看昨日涨停的那些票
            pl.col("ret_1d")
            .filter(pl.col("was_limit_up"))
            .mean()
            .alias("prior_limitup_nextday_return_mean"),
            pl.col("ret_1d")
            .filter(pl.col("was_limit_up"))
            .median()
            .alias("prior_limitup_nextday_return_median"),
        )
        .with_columns(
            # 分母保底为 1：无跌停的日子比值就等于涨停家数，不是无穷大
            (
                pl.col("limit_up_count")
                / pl.max_horizontal(pl.col("limit_down_count"), pl.lit(1))
            )
            .cast(pl.Float64)
            .alias("limit_up_down_ratio"),
            pl.when(pl.col("touched_limit_up_count") > 0)
            .then(pl.col("broken_board_count") / pl.col("touched_limit_up_count"))
            .otherwise(0.0)
            .cast(pl.Float64)
            .alias("broken_board_rate"),
            (
                pl.col("_up")
                / pl.max_horizontal(pl.col("_up") + pl.col("_down"), pl.lit(1))
            )
            .cast(pl.Float64)
            .alias("market_advance_ratio"),
        )
    )
