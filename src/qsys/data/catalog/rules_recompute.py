"""A2 派生指标独立重算；禁止导入任何 pipeline 计算函数。"""

from __future__ import annotations

from datetime import timedelta
from typing import Callable

import polars as pl

from qsys.data.catalog.checks import Category, Check, CheckContext, CheckResult, Scope, register

_TOL = 1e-6
# Keep the independent checker wide enough to cross long exchange holidays.
# The production derivation deliberately uses 150 calendar days for the same
# previous-session requirement; a 10-day window produced a false blocking
# alert after the 2026 National Day closure.
_FIRST_BOARD_LOOKBACK_DAYS = 150


def _compare(
    frame: pl.DataFrame, *, boolean: bool = False, empty_is_not_applicable: bool = False,
) -> CheckResult:
    usable = frame.drop_nulls(["actual", "expected"])
    if usable.height == 0:
        return CheckResult(
            0, 0, "上游有 null 或无可比对的行",
            applicable=not empty_is_not_applicable,
        )
    bad = usable.filter(
        pl.col("actual") != pl.col("expected") if boolean else
        (pl.col("actual") - pl.col("expected")).abs() > _TOL
    )
    return CheckResult(bad.height, usable.height,
                       f"{bad.height} / {usable.height} 行与独立重算不符",
                       bad.head(5000) if bad.height else None)


def _add(column: str, metric: str, build: Callable[[CheckContext], pl.DataFrame],
         *, boolean: bool = False, empty_is_not_applicable: bool = False) -> None:
    register(Check(
        f"A2.recompute.{column}", Category.TRUTHFULNESS, Scope.INCREMENTAL,
        (metric,), (), f"独立重算 {metric}",
        lambda ctx: _compare(
            build(ctx), boolean=boolean,
            empty_is_not_applicable=empty_is_not_applicable,
        ),
    ))


def _flags_daily(ctx: CheckContext, daily_columns: list[str]) -> pl.DataFrame:
    flags = ctx.query.daily_flags(ctx.start, ctx.end)
    daily = ctx.query.tushare_daily(None, ctx.start, ctx.end,
                                    columns=["symbol", "dt", *daily_columns])
    return flags.join(daily, on=["symbol", "dt"], how="inner")


def _prev_close(ctx: CheckContext) -> pl.DataFrame:
    daily = ctx.query.tushare_daily(
        None, ctx.start - timedelta(days=10), ctx.end,
        columns=["symbol", "dt", "close"],
    ).sort(["symbol", "dt"]).with_columns(
        pl.col("close").shift(1).over("symbol").alias("expected")
    ).filter(pl.col("dt").is_between(ctx.start, ctx.end))
    return ctx.query.daily_flags(ctx.start, ctx.end).select(
        "symbol", "dt", pl.col("prev_close").alias("actual")
    ).join(daily.select("symbol", "dt", "expected"), on=["symbol", "dt"])


_add("prev_close", "daily_flags.prev_close", _prev_close)
_add("adj_prev_close", "daily_flags.adj_prev_close",
     lambda c: _flags_daily(c, ["pre_close"]).select(
         pl.col("adj_prev_close").alias("actual"), pl.col("pre_close").alias("expected")))


def _nullable(expr: pl.Expr) -> pl.Expr:
    """把 null 显式编码成可比较的值，使「一边有值、一边为 null」也算违例。

    D-11 之后「没有直取值就必须是 null」本身就是要验的事实；`_compare`
    默认丢掉含 null 的行，会把自算出来的值整个放过。
    """
    return pl.when(expr.is_null()).then(pl.lit("null")).otherwise(expr.cast(pl.Utf8))


def _with_direct_limits(ctx: CheckContext) -> pl.DataFrame:
    """daily_flags 左连当日 stk_limit 直取涨跌停价（D-11：只认直取，不留兜底档位）。"""
    frame = ctx.query.daily_flags(ctx.start, ctx.end)
    direct = ctx.query.price_limits(None, ctx.start, ctx.end).select(
        "symbol", "dt",
        pl.col("pre_close").alias("direct_pre_close"),
        pl.col("limit_up").alias("direct_up"),
        pl.col("limit_down").alias("direct_down"),
    )
    return frame.join(direct, on=["symbol", "dt"], how="left")


def _price_of(cents: pl.Expr, tier: pl.Expr, sign: int) -> pl.Expr:
    milli = ((1 + sign * tier) * 1000).round().cast(pl.Int64)
    return (((cents * milli + 500) // 1000).cast(pl.Float64) / 100).round(2)


_TIERS = (0.05, 0.10, 0.20, 0.30)


def _limit_pct(ctx: CheckContext) -> pl.DataFrame:
    """有直取价的行按解码取档；没有直取价的行档位必须为 null。

    解码独立于生产的 `_decode_tier` 重写：逐档试算再比对，命中恰好一档
    才算数，零档或多档都记 null。
    """
    frame = _with_direct_limits(ctx)
    cents = (pl.col("adj_prev_close") * 100).round().cast(pl.Int64)
    hits = [(t, _price_of(cents, pl.lit(t), 1) == pl.col("direct_up")) for t in _TIERS]
    matched = sum((c.fill_null(False).cast(pl.Int32) for _, c in hits),
                  pl.lit(0, dtype=pl.Int32))
    decoded = pl.lit(None, dtype=pl.Float64)
    for tier, cond in reversed(hits):
        decoded = pl.when(cond.fill_null(False)).then(pl.lit(tier)).otherwise(decoded)
    return frame.with_columns(
        pl.col("limit_pct").alias("actual"),
        pl.when(matched == 1).then(decoded).otherwise(None).alias("expected"),
    )


_add("limit_pct", "daily_flags.limit_pct", _limit_pct)


def _limit(column: str, sign: int) -> Callable[[CheckContext], pl.DataFrame]:
    """涨/跌停价必须等于 stk_limit 直取值；没有直取值就必须为 null。

    **不拿表里的 limit_pct 来算**——它是从这个价格解码出来的，拿它回算
    就成了同义反复，什么也验不出来。
    """
    def build(ctx: CheckContext) -> pl.DataFrame:
        frame = _with_direct_limits(ctx)
        direct = pl.col("direct_up" if sign > 0 else "direct_down")
        return frame.select(
            _nullable(pl.col(column).round(2)).alias("actual"),
            _nullable(direct.round(2)).alias("expected"),
        )
    return build


_add("limit_up", "daily_flags.limit_up", _limit("limit_up", 1), boolean=True)
_add("limit_down", "daily_flags.limit_down", _limit("limit_down", -1), boolean=True)
def _limit_status(ctx: CheckContext, start=None) -> pl.DataFrame:
    """独立按日线、ST 真值与 U/Z 名单重算封板和炸板状态。"""
    lo = start or ctx.start
    flags = ctx.query.daily_flags(lo, ctx.end)
    daily = ctx.query.tushare_daily(
        None, lo, ctx.end, columns=["symbol", "dt", "close", "high"]
    )
    listed = ctx.query.limit_list(None, lo, ctx.end)
    frame = flags.join(daily, on=["symbol", "dt"], how="inner")
    if listed.height:
        classifications = listed.select(
            "symbol", "dt", pl.col("limit_type").alias("_limit_type")
        )
        availability = listed.select("dt").unique().with_columns(
            pl.lit(True).alias("_limit_list_available")
        )
        frame = frame.join(classifications, on=["symbol", "dt"], how="left")
        frame = frame.join(availability, on="dt", how="left")
    else:
        frame = frame.with_columns(
            pl.lit(None, dtype=pl.Utf8).alias("_limit_type"),
            pl.lit(False).alias("_limit_list_available"),
        )
    frame = frame.with_columns(
        (pl.col("high") >= pl.col("limit_up")).alias("expected_touched"),
    )
    price_at_limit = pl.col("close") >= pl.col("limit_up")
    frame = frame.with_columns(
        pl.when(pl.col("limit_up").is_null())
        .then(None)
        .when(pl.col("_limit_type") == "U")
        .then(True)
        .when(pl.col("_limit_type") == "Z")
        .then(False)
        .when(pl.col("is_st") == True)  # noqa: E712 - polars expression
        .then(price_at_limit)
        .when(pl.col("_limit_list_available").is_null() | ~pl.col("_limit_list_available"))
        .then(price_at_limit)
        .when(~pl.col("expected_touched"))
        .then(False)
        .otherwise(None)
        .alias("expected_limit_up")
    )
    return frame.with_columns(
        (pl.col("expected_touched") & ~pl.col("expected_limit_up"))
        .alias("expected_broken_board")
    )


_add("is_limit_up", "daily_flags.is_limit_up",
     lambda c: _limit_status(c).select(
         pl.col("is_limit_up").alias("actual"),
         pl.col("expected_limit_up").alias("expected")), boolean=True)
_add("is_limit_down", "daily_flags.is_limit_down",
     lambda c: _flags_daily(c, ["close"]).select(
         pl.col("is_limit_down").alias("actual"),
         (pl.col("close") <= pl.col("limit_down")).alias("expected")), boolean=True)
_add("touched_limit_up", "daily_flags.touched_limit_up",
     lambda c: _flags_daily(c, ["high"]).select(
         pl.col("touched_limit_up").alias("actual"),
         (pl.col("high") >= pl.col("limit_up")).alias("expected")), boolean=True)
_add("is_broken_board", "daily_flags.is_broken_board",
     lambda c: _limit_status(c).select(
         pl.col("is_broken_board").alias("actual"),
         pl.col("expected_broken_board").alias("expected")), boolean=True)


def _first_board(ctx: CheckContext) -> pl.DataFrame:
    frame = _limit_status(
        ctx, ctx.start - timedelta(days=_FIRST_BOARD_LOOKBACK_DAYS)
    ).sort(["symbol", "dt"])
    return frame.with_columns(
        pl.col("is_first_board").alias("actual"),
        (pl.col("expected_limit_up")
         & ~pl.col("expected_limit_up").shift(1, fill_value=False).over("symbol"))
        .alias("expected"),
    ).filter(pl.col("dt").is_between(ctx.start, ctx.end))


_add("is_first_board", "daily_flags.is_first_board", _first_board, boolean=True)


def _announced_pool(ctx: CheckContext) -> pl.DataFrame:
    """当日已公告生效的退市整理期池；公告日晚于当日的行还没生效。"""
    empty = pl.DataFrame(schema={"symbol": pl.String, "dt": pl.Date, "_pooled": pl.Boolean})
    pool = ctx.query.delisting_pool(ctx.start, ctx.end)
    if pool.height == 0:
        return empty
    return (pool.filter(pl.col("ann_date").is_null() | (pl.col("ann_date") <= pl.col("dt")))
            .select("symbol", "dt").unique()
            .with_columns(pl.lit(True).alias("_pooled")))


def _direct_st(ctx: CheckContext) -> pl.DataFrame:
    """ST 只认 stock_st（D-11）；已公告的退市整理期压过它（D-15）。

    整理期池派生自 namechange，是独立于 stock_st 的另一条直取链路，所以在池子里
    就不是 ST、来源记池子——即便 stock_st 恰好也说 false：决定这一行的是池子那一支。
    只认 stock_st 的旧写法会把「元道退」这样一条正确的行判成阻断（2026-09-30）。
    """
    flags = ctx.query.daily_flags(ctx.start, ctx.end).select(
        "symbol", "dt", pl.col("is_st").alias("actual"),
        pl.col("st_source").alias("actual_source"))
    truth = ctx.query.stock_st(None, ctx.start, ctx.end).select(
        "symbol", "dt", pl.col("is_st").alias("_stock_st"))
    pooled = pl.col("_pooled").is_not_null()   # 左连没命中就是不在池子里
    return (flags.join(truth, on=["symbol", "dt"], how="left")
            .join(_announced_pool(ctx), on=["symbol", "dt"], how="left")
            .with_columns(
                pl.when(pooled).then(pl.lit(False))  # noqa: FBT003
                .otherwise(pl.col("_stock_st")).alias("expected"),
                pl.when(pooled).then(pl.lit("delisting_pool_override"))
                .when(pl.col("_stock_st").is_not_null()).then(pl.lit("stock_st"))
                .alias("expected_source"),
            ))


_add("is_st", "daily_flags.is_st", lambda c: _direct_st(c).select(
    _nullable(pl.col("actual")).alias("actual"),
    _nullable(pl.col("expected")).alias("expected")), boolean=True)
_add("st_source", "daily_flags.st_source", lambda c: _direct_st(c).select(
    _nullable(pl.col("actual_source")).alias("actual"),
    _nullable(pl.col("expected_source")).alias("expected")), boolean=True)


def _valuation(ctx: CheckContext, column: str) -> pl.DataFrame:
    """估值是 daily_basic 的纯投影，重算只做单位换算。

    2026-09-15 前这里按 close × QMT 生效股本反算，那条路已废——反算在
    A+H 股上会差出上亿股。市值两列元转亿元除 1e8，换手率原样透传。
    """
    values = ctx.query.valuation(ctx.start, ctx.end)
    daily = ctx.query.tushare_daily(
        None, ctx.start, ctx.end,
        columns=["symbol", "dt", "market_cap", "circulating_market_cap",
                 "turnover_ratio"],
    )
    if not values.height or not daily.height:
        return pl.DataFrame({"actual": [], "expected": []},
                            schema={"actual": pl.Float64, "expected": pl.Float64})
    joined = values.join(daily, on=["symbol", "dt"], how="inner", suffix="_src")
    formulas = {
        "market_cap": pl.col("market_cap_src") / 1e8,
        "circulating_market_cap": pl.col("circulating_market_cap_src") / 1e8,
        "turnover_ratio": pl.col("turnover_ratio_src"),
    }
    return joined.select(pl.col(column).alias("actual"), formulas[column].alias("expected"))


for _column in ("market_cap", "circulating_market_cap", "turnover_ratio"):
    _add(_column, f"valuation_snapshot.{_column}",
         lambda ctx, column=_column: _valuation(ctx, column))


def _live_total(ctx: CheckContext) -> pl.DataFrame:
    parts = sorted((ctx.warehouse.root / "live_equity").glob("year=*/part.parquet"))
    if not parts:
        return pl.DataFrame({"actual": [], "expected": []},
                            schema={"actual": pl.Float64, "expected": pl.Float64})
    frame = pl.concat([pl.read_parquet(path) for path in parts]).filter(
        pl.col("dt").is_between(ctx.start, ctx.end))
    return frame.select(pl.col("total_value").alias("actual"),
                        (pl.col("cash") + pl.col("market_value")).round(2).alias("expected"))


_add(
    "live_total_value", "live_equity.total_value", _live_total,
    empty_is_not_applicable=True,
)


# ---- market_regime：从日线、flags、因子和指数重新聚合 -----------------

def _expected_regime(ctx: CheckContext) -> pl.DataFrame:
    start = ctx.start - timedelta(days=40)
    bars = ctx.query.tushare_daily(
        None, start, ctx.end,
        columns=["symbol", "dt", "close", "amount"],
    )
    flags = ctx.query.daily_flags(start, ctx.end)
    # 全 A 股口径只排除外币计价的 900/200 B 股；板块与 ST 均保留。
    code = pl.col("symbol").str.split(".").list.first()
    bars = bars.filter(~code.str.starts_with("900") & ~code.str.starts_with("200"))
    flags = flags.filter(~code.str.starts_with("900") & ~code.str.starts_with("200"))
    if not bars.height or not flags.height:
        return pl.DataFrame()
    joined = bars.join(flags, on=["symbol", "dt"], how="inner").sort(["symbol", "dt"])
    joined = joined.with_columns(
        pl.col("is_limit_up").shift(1).over("symbol").alias("was_limit_up")
    )
    factors = ctx.query.adj_factors()
    if factors.height:
        ratios = factors.sort(["symbol", "dt"]).with_columns(
            (pl.col("cum_factor") / pl.col("cum_factor").shift(1).over("symbol"))
            .alias("daily_factor")
        ).select("symbol", "dt", "daily_factor")
        joined = joined.join(ratios, on=["symbol", "dt"], how="left")
        ret = (pl.col("close") / pl.col("prev_close")
               * pl.col("daily_factor").fill_null(1.0) - 1).round(12)
    else:
        ret = (pl.col("close") / pl.col("prev_close") - 1).round(12)
    joined = joined.with_columns(ret.alias("ret_1d"))
    expected = joined.group_by("dt").agg(
        pl.col("is_limit_up").sum().cast(pl.Int64).alias("limit_up_count"),
        pl.col("is_limit_down").sum().cast(pl.Int64).alias("limit_down_count"),
        pl.col("touched_limit_up").sum().cast(pl.Int64).alias("touched_limit_up_count"),
        pl.col("is_broken_board").sum().cast(pl.Int64).alias("broken_board_count"),
        pl.col("is_first_board").sum().cast(pl.Int64).alias("first_board_count"),
        pl.len().cast(pl.Int64).alias("valid_stock_count"),
        pl.col("amount").sum().alias("market_total_money"),
        (pl.col("ret_1d") > 0).sum().alias("up_count"),
        (pl.col("ret_1d") < 0).sum().alias("down_count"),
        pl.col("ret_1d").median().alias("market_median_return_1d"),
        pl.col("ret_1d").filter(pl.col("was_limit_up")).mean()
        .alias("prior_limitup_nextday_return_mean"),
        pl.col("ret_1d").filter(pl.col("was_limit_up")).median()
        .alias("prior_limitup_nextday_return_median"),
    ).with_columns(
        (pl.col("limit_up_count") / pl.max_horizontal("limit_down_count", pl.lit(1)))
        .cast(pl.Float64).alias("limit_up_down_ratio"),
        pl.when(pl.col("touched_limit_up_count") > 0)
        .then(pl.col("broken_board_count") / pl.col("touched_limit_up_count"))
        .otherwise(0.0).alias("broken_board_rate"),
        (pl.col("up_count") / pl.max_horizontal(pl.col("up_count") + pl.col("down_count"), pl.lit(1)))
        .cast(pl.Float64).alias("market_advance_ratio"),
    )
    index = ctx.query.index_bars(["000300.SH"], ctx.start - timedelta(days=75), ctx.end)
    if index.height >= 25:
        slope = index.sort("dt").with_columns(
            pl.col("close").rolling_mean(20).alias("ma20")
        ).with_columns(
            (pl.col("ma20") / pl.col("ma20").shift(5) - 1)
            .alias("index_ma20_slope_5d")
        ).select("dt", "index_ma20_slope_5d")
        expected = expected.join(slope, on="dt", how="left")
    else:
        expected = expected.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("index_ma20_slope_5d")
        )
    return expected.filter(pl.col("dt").is_between(ctx.start, ctx.end))


def _regime_column(ctx: CheckContext, column: str) -> pl.DataFrame:
    actual = ctx.query.market_regime(ctx.start, ctx.end).select(
        "dt", pl.col(column).alias("actual")
    )
    expected = _expected_regime(ctx)
    if not actual.height or not expected.height:
        return pl.DataFrame({"actual": [], "expected": []},
                            schema={"actual": pl.Float64, "expected": pl.Float64})
    return actual.join(
        expected.select("dt", pl.col(column).alias("expected")), on="dt", how="inner"
    ).select("actual", "expected")


for _regime_name in (
    "limit_up_count", "limit_down_count", "limit_up_down_ratio",
    "touched_limit_up_count", "broken_board_count", "broken_board_rate",
    "first_board_count", "market_advance_ratio", "market_median_return_1d",
    "market_total_money", "prior_limitup_nextday_return_mean",
    "prior_limitup_nextday_return_median", "valid_stock_count",
    "index_ma20_slope_5d",
):
    _add(
        _regime_name,
        f"market_regime.{_regime_name}",
        lambda ctx, column=_regime_name: _regime_column(ctx, column),
        boolean=False,
    )
