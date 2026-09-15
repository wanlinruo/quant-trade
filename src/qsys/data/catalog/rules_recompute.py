"""A2 派生指标独立重算；禁止导入任何 pipeline 计算函数。"""

from __future__ import annotations

from datetime import timedelta
from typing import Callable

import polars as pl

from qsys.data.catalog.checks import Category, Check, CheckContext, CheckResult, Scope, register

_TOL = 1e-6


def _compare(frame: pl.DataFrame, *, boolean: bool = False) -> CheckResult:
    usable = frame.drop_nulls(["actual", "expected"])
    if usable.height == 0:
        return CheckResult(0, 0, "上游有 null 或无可比对的行")
    bad = usable.filter(
        pl.col("actual") != pl.col("expected") if boolean else
        (pl.col("actual") - pl.col("expected")).abs() > _TOL
    )
    return CheckResult(bad.height, usable.height,
                       f"{bad.height} / {usable.height} 行与独立重算不符",
                       bad.head(5000) if bad.height else None)


def _add(column: str, metric: str, build: Callable[[CheckContext], pl.DataFrame],
         *, boolean: bool = False) -> None:
    register(Check(
        f"A2.recompute.{column}", Category.TRUTHFULNESS, Scope.INCREMENTAL,
        (metric,), (), f"独立重算 {metric}",
        lambda ctx: _compare(build(ctx), boolean=boolean),
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


def _limit_pct(ctx: CheckContext) -> pl.DataFrame:
    frame = ctx.query.daily_flags(ctx.start, ctx.end)
    instruments = ctx.query.instruments()
    boards = dict(zip(instruments["symbol"].to_list(), instruments["board"].to_list()))

    def ratio(symbol: str) -> float:
        board = boards.get(symbol)
        if board is None:
            code = symbol.split(".")[0]
            if code.startswith(("688", "689")):
                board = "star"
            elif code.startswith(("300", "301")):
                board = "gem"
            elif code[:2] in ("43", "83", "87", "92"):
                board = "bse"
            elif code[:2] in ("90", "20"):
                board = "bshare"
            else:
                board = "main"
        return {"main": .10, "gem": .20, "star": .20, "bse": .30, "bshare": .10}.get(board, .10)

    return frame.with_columns(
        pl.col("limit_pct").alias("actual"),
        pl.when(pl.col("is_st")).then(.05)
        .otherwise(pl.when(pl.col("is_st").is_null()).then(None).otherwise(
            pl.col("symbol").map_elements(ratio, return_dtype=pl.Float64)
        )).alias("expected"),
    )


_add("limit_pct", "daily_flags.limit_pct", _limit_pct)


def _limit(column: str, sign: int) -> Callable[[CheckContext], pl.DataFrame]:
    def build(ctx: CheckContext) -> pl.DataFrame:
        frame = ctx.query.daily_flags(ctx.start, ctx.end)
        cents = (pl.col("adj_prev_close") * 100).round().cast(pl.Int64)
        milli = ((1 + sign * pl.col("limit_pct")) * 1000).round().cast(pl.Int64)
        expected = (((cents * milli + 500) // 1000).cast(pl.Float64) / 100).round(2)
        return frame.with_columns(pl.col(column).alias("actual"), expected.alias("expected"))
    return build


_add("limit_up", "daily_flags.limit_up", _limit("limit_up", 1))
_add("limit_down", "daily_flags.limit_down", _limit("limit_down", -1))
_add("is_limit_up", "daily_flags.is_limit_up",
     lambda c: _flags_daily(c, ["close"]).select(
         pl.col("is_limit_up").alias("actual"),
         (pl.col("close") >= pl.col("limit_up")).alias("expected")), boolean=True)
_add("is_limit_down", "daily_flags.is_limit_down",
     lambda c: _flags_daily(c, ["close"]).select(
         pl.col("is_limit_down").alias("actual"),
         (pl.col("close") <= pl.col("limit_down")).alias("expected")), boolean=True)
_add("touched_limit_up", "daily_flags.touched_limit_up",
     lambda c: _flags_daily(c, ["high"]).select(
         pl.col("touched_limit_up").alias("actual"),
         (pl.col("high") >= pl.col("limit_up")).alias("expected")), boolean=True)
_add("is_broken_board", "daily_flags.is_broken_board",
     lambda c: c.query.daily_flags(c.start, c.end).select(
         pl.col("is_broken_board").alias("actual"),
         (pl.col("touched_limit_up") & ~pl.col("is_limit_up")).alias("expected")), boolean=True)


def _first_board(ctx: CheckContext) -> pl.DataFrame:
    frame = ctx.query.daily_flags(ctx.start - timedelta(days=10), ctx.end).sort(["symbol", "dt"])
    return frame.with_columns(
        pl.col("is_first_board").alias("actual"),
        (pl.col("is_limit_up") & ~pl.col("is_limit_up").shift(1, fill_value=False).over("symbol"))
        .alias("expected"),
    ).filter(pl.col("dt").is_between(ctx.start, ctx.end))


_add("is_first_board", "daily_flags.is_first_board", _first_board, boolean=True)


def _names(ctx: CheckContext) -> pl.DataFrame:
    flags = ctx.query.daily_flags(ctx.start, ctx.end).select(
        "symbol", "dt", pl.col("is_st").alias("actual_st"),
        pl.col("st_source").alias("actual_source"))
    daily = ctx.query.tushare_daily(None, ctx.start, ctx.end,
                                    columns=["symbol", "dt", "name"])
    resolved = flags.join(daily, on=["symbol", "dt"], how="left").with_columns(
        pl.when(pl.col("name").is_not_null()).then(pl.lit("tushare_daily"))
        .otherwise(None).alias("expected_source"))
    for other, label in ((ctx.query.paid_daily(None, ctx.start, ctx.end,
                                                columns=["symbol", "dt", "name"]), "paid_daily"),
                         (ctx.query.name_snapshots(None, ctx.start, ctx.end), "name_snapshot")):
        resolved = resolved.join(other.select("symbol", "dt", pl.col("name").alias("_name")),
                                 on=["symbol", "dt"], how="left").with_columns(
            pl.when(pl.col("name").is_null() & pl.col("_name").is_not_null()).then(pl.lit(label))
            .otherwise(pl.col("expected_source")).alias("expected_source"),
            pl.coalesce("name", "_name").alias("name")).drop("_name")
    history = ctx.query.name_history().filter(pl.col("start_date").is_not_null()).select(
        "symbol", pl.col("name").alias("_hist"), "start_date", "end_date").sort(["symbol", "start_date"])
    if history.height:
        resolved = resolved.sort(["symbol", "dt"]).join_asof(
            history, left_on="dt", right_on="start_date", by="symbol", strategy="backward")
        in_range = pl.col("_hist").is_not_null() & (pl.col("end_date").is_null() | (pl.col("dt") <= pl.col("end_date")))
        resolved = resolved.with_columns(
            pl.when(pl.col("name").is_null() & in_range).then(pl.lit("namechange"))
            .otherwise(pl.col("expected_source")).alias("expected_source"),
            pl.when(pl.col("name").is_null() & in_range).then(pl.col("_hist"))
            .otherwise(pl.col("name")).alias("name"))
    return resolved.with_columns(
        pl.when(pl.col("name").is_null()).then(None)
        .otherwise(pl.col("name").str.contains("ST")).alias("expected_st"))


_add("is_st", "daily_flags.is_st", lambda c: _names(c).select(
    pl.col("actual_st").alias("actual"), pl.col("expected_st").alias("expected")), boolean=True)
_add("st_source", "daily_flags.st_source", lambda c: _names(c).select(
    pl.col("actual_source").alias("actual"), pl.col("expected_source").alias("expected")), boolean=True)


def _valuation(ctx: CheckContext, column: str) -> pl.DataFrame:
    values = ctx.query.valuation(ctx.start, ctx.end)
    daily = ctx.query.tushare_daily(None, ctx.start, ctx.end,
                                    columns=["symbol", "dt", "close", "volume"])
    capital = ctx.query.share_capital().select("symbol", "dt", "total", "circulating").sort("dt")
    if not values.height or not daily.height or not capital.height:
        return pl.DataFrame({"actual": [], "expected": []}, schema={"actual": pl.Float64, "expected": pl.Float64})
    joined = values.join(daily, on=["symbol", "dt"], how="inner", suffix="_daily").sort("dt").join_asof(
        capital, on="dt", by="symbol", strategy="backward")
    formulas = {
        "market_cap": pl.col("close_daily") * pl.col("total") / 1e8,
        "circulating_market_cap": pl.col("close_daily") * pl.col("circulating") / 1e8,
        "turnover_ratio": pl.col("volume") * 100 / pl.col("circulating") * 100,
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


_add("live_total_value", "live_equity.total_value", _live_total)


# ---- market_regime：从日线、flags、因子和指数重新聚合 -----------------

def _breadth_filter(frame: pl.DataFrame, boards: dict[str, str]) -> pl.DataFrame:
    if frame.height == 0:
        return frame

    def board(symbol: str) -> str:
        known = boards.get(symbol)
        if known is not None:
            return known
        code = symbol.split(".")[0]
        if code.startswith(("688", "689")):
            return "star"
        if code.startswith(("300", "301")):
            return "gem"
        if code[:2] in ("43", "83", "87", "92"):
            return "bse"
        if code[:2] in ("90", "20"):
            return "bshare"
        return "main"

    code = pl.col("symbol").str.split(".").list.first()
    excluded = code.str.starts_with("4") | code.str.starts_with("8") | code.str.starts_with("68")
    return frame.filter(
        ~excluded
        & pl.col("symbol").map_elements(board, return_dtype=pl.Utf8)
        .is_in(["main", "gem", "star"])
    )


def _expected_regime(ctx: CheckContext) -> pl.DataFrame:
    start = ctx.start - timedelta(days=40)
    instruments = ctx.query.instruments()
    boards = (
        dict(zip(instruments["symbol"].to_list(), instruments["board"].to_list()))
        if instruments.height else {}
    )
    bars = _breadth_filter(
        ctx.query.tushare_daily(
            None, start, ctx.end,
            columns=["symbol", "dt", "close", "amount"],
        ), boards,
    )
    flags = _breadth_filter(ctx.query.daily_flags(start, ctx.end), boards)
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
