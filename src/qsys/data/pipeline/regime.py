"""市场生态因子。

策略的 S7 Gate 完全依赖这些指标判断当日市场环境：
`index_ma20_slope_5d`、`prior_limitup_nextday_return_mean`、
`market_median_return_1d`、`touched_limit_up_count`、`market_advance_ratio`。

派生自 bars_daily + daily_flags，可完全重算。全部用 polars 向量化：
全市场多年逐股 Python 循环在 1.9 GB 内存的机器上跑不动。

与 flags 同样**逐年处理**，并向前多读 `_LOOKBACK_DAYS` 天日历日——
`prior_limitup_nextday_return` 要看昨日的涨停状态，跨年那一天没有回看
就会算空。

**股票池排除科创板与北交所**，与原策略 `_s5_relevant_universe`
（`filter_kcbj_stock`：排除 4/8/68 开头）一致。科创板涨跌幅是 20%，
收益分布与主板不同；`market_median_return_1d` 的判定阈值只有 0.00426，
混进去足以翻转 Gate 判定——与聚宽对账时实测有 19 条因此分歧。
B 股（900/200）不在排除之列，照抄原口径不多排。

`index_ma20_slope_5d` 取自沪深 300 指数日线（`index_bars`）：
MA20 相对 5 个交易日前的 MA20 的变化率。**没有指数数据时留空而不是填 0**——
S6 Gate 对 None 走 UNKNOWN_ALLOW，填 0 会被当成真实的「趋势走平」。
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.pipeline.index_bars import CSI300
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.markets.ashare import board_of

log = structlog.get_logger(__name__)

_LOOKBACK_DAYS = 30

# 日收益取整位数。见 _ret_1d 的说明。
_RET_PRECISION = 12

# 与原策略 filter_kcbj_stock 一致：4/8 开头是北交所，68 开头是科创板
EXCLUDED_CODE_PREFIXES = ("4", "8", "68")

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
    "index_ma20_slope_5d",
]


def _keep_boards(frame: pl.DataFrame, boards: set[str] | None) -> pl.DataFrame:
    """按板块过滤。

    广度因子的分母就是这个池子。付费源比旧表多 343 只北交所，不过滤会让
    market_advance_ratio 在换源当天静默跳变而没有任何地方报错。
    """
    if boards is None or frame.height == 0:
        return frame
    return frame.filter(
        pl.col("symbol")
        .map_elements(lambda s: board_of(s.split(".")[0]), return_dtype=pl.Utf8)
        .is_in(list(boards))
    )


def build_market_regime(
    warehouse: Warehouse,
    start: date,
    end: date,
    boards: set[str] | None = None,
) -> int:
    query = WarehouseQuery(warehouse)

    written = 0
    for year in range(start.year, end.year + 1):
        lo_year, hi_year = date(year, 1, 1), date(year, 12, 31)
        lo = max(start, lo_year)
        hi = min(end, hi_year)
        if lo > hi:
            continue

        back = lo - timedelta(days=_LOOKBACK_DAYS)
        bars = _keep_boards(_in_breadth_universe(query.daily_bars(None, back, hi)), boards)
        flags = _keep_boards(_in_breadth_universe(query.daily_flags(back, hi)), boards)
        if bars.height == 0 or flags.height == 0:
            continue

        daily = _aggregate(bars, flags, query.adj_factors()).filter(
            pl.col("dt").is_between(lo, hi)
        )
        if daily.height == 0:
            continue
        daily = daily.join(_index_slope(query, back, hi), on="dt", how="left")

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


def _in_breadth_universe(frame: pl.DataFrame) -> pl.DataFrame:
    """剔除科创板与北交所。"""
    if frame.height == 0:
        return frame
    code = pl.col("symbol").str.split(".").list.first()
    excluded = code.str.starts_with(EXCLUDED_CODE_PREFIXES[0])
    for prefix in EXCLUDED_CODE_PREFIXES[1:]:
        excluded = excluded | code.str.starts_with(prefix)
    return frame.filter(~excluded)


def _aggregate(
    bars: pl.DataFrame, flags: pl.DataFrame, factors: pl.DataFrame | None = None
) -> pl.DataFrame:
    joined = (
        bars.join(flags, on=["symbol", "dt"], how="inner")
        .sort(["symbol", "dt"])
        .with_columns(
            pl.col("is_limit_up").shift(1).over("symbol").alias("was_limit_up"),
        )
    )
    if factors is not None and factors.height:
        ratio = (
            factors.sort(["symbol", "dt"])
            .with_columns(
                (pl.col("cum_factor")
                 / pl.col("cum_factor").shift(1).over("symbol"))
                .alias("_daily_factor")
            )
            .select(["symbol", "dt", "_daily_factor"])
        )
        joined = joined.join(ratio, on=["symbol", "dt"], how="left")
    joined = joined.with_columns(_ret_1d(joined, factors).alias("ret_1d"))

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


def _ret_1d(joined: pl.DataFrame, factors: pl.DataFrame | None) -> pl.Expr:
    """日收益，**跨除权日要复权**。

    `close / prev_close - 1` 在除权日会看到一笔凭空的大跌：10 送 4 派 0.2
    的当天原始价跌约 29%，实际持有人没亏。不修正的后果是广度类因子被
    系统性压低——`market_advance_ratio` 与 `market_median_return_1d` 的
    判定阈值分别只有 0.5105 和 0.004255，压低一点就翻转。

    复权后的日收益 = (原始收益 + 1) x 当日因子 - 1，其中
    当日因子 = cum_factor(t) / cum_factor(t-1)，非除权日恒为 1。

    实测：三个与聚宽分歧的日子，其因子日的除权只数分别是 35 / 93 / 104，
    而全区间中位数只有 9。
    """
    raw = pl.col("close") / pl.col("prev_close")
    if factors is None or factors.height == 0:
        expr = raw - 1.0
    else:
        expr = raw * pl.col("_daily_factor").fill_null(1.0) - 1.0
    # 取整到 1e-12：没动的票会算出 -2.22e-16 这种数，被判成「下跌」而不是
    # 「持平」。实测 2026-04-30 有 002841.SZ 因此错分。1e-12 的收益在经济上
    # 就是零，而 advance_ratio 的分母不含持平，错分一只就直接改比值。
    return expr.round(_RET_PRECISION)


# MA20 与「5 个交易日前的 MA20」合起来要 25 根
_MA_WINDOW = 20
_SLOPE_LAG = 5


def _index_slope(query: WarehouseQuery, start: date, end: date) -> pl.DataFrame:
    """沪深 300 的 MA20 相对 5 个交易日前 MA20 的变化率，按日返回。

    取数区间要比 [start, end] 更早 —— MA20 需要往前 20 根、再往前 5 根。
    不足 25 根的日子留空：S6 Gate 对 None 走 UNKNOWN_ALLOW，填 0 会被
    当成真实的「趋势走平」而误判。
    """
    empty = pl.DataFrame(schema={"dt": pl.Date, "index_ma20_slope_5d": pl.Float64})
    need = _MA_WINDOW + _SLOPE_LAG
    # 多取 2 倍窗口的日历日，足够覆盖 25 个交易日
    bars = query.index_bars([CSI300], start - timedelta(days=need * 3), end)
    if bars.height < need:
        return empty

    ma = bars.sort("dt").with_columns(
        pl.col("close").rolling_mean(window_size=_MA_WINDOW).alias("_ma20")
    )
    return (
        ma.with_columns(
            (pl.col("_ma20") / pl.col("_ma20").shift(_SLOPE_LAG) - 1.0).alias(
                "index_ma20_slope_5d"
            )
        )
        .select(["dt", "index_ma20_slope_5d"])
        .filter(pl.col("dt").is_between(start, end))
    )
