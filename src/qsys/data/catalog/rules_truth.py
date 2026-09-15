"""A 类真实性规则：独立量纲反查与跨源、跨表恒等式。"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Callable

import polars as pl

from qsys.data.catalog.checks import Category, Check, CheckContext, CheckResult, Scope, register
from qsys.data.quality.minute_rules import check_minute_bars

_REL_TOL = 1e-4
_GAP_TOL = 0.005


def _relative_bad(frame: pl.DataFrame, tolerance: float) -> tuple[pl.DataFrame, int]:
    comparable = frame.filter(
        pl.col("actual").is_not_null()
        & pl.col("expected").is_not_null()
        & (pl.col("expected").abs() > 0)
    )
    bad = comparable.filter(
        (pl.col("actual") - pl.col("expected")).abs()
        / pl.col("expected").abs() > tolerance
    )
    return bad, comparable.height


def _identity(
    check_id: str,
    metrics: tuple[str, ...],
    columns: list[str],
    build: Callable[[pl.DataFrame], pl.DataFrame],
    description: str,
    *,
    tolerance: float = _REL_TOL,
) -> None:
    def run(ctx: CheckContext) -> CheckResult:
        source = ctx.query.tushare_daily(None, ctx.start, ctx.end, columns=columns)
        if source.height == 0:
            return CheckResult(0, 0, "区间内无日线数据")
        bad, checked = _relative_bad(build(source), tolerance)
        return CheckResult(
            bad.height, checked,
            f"{bad.height} / {checked} 行超出相对容差 {tolerance}",
            bad.head(5000) if bad.height else None,
        )

    register(Check(check_id, Category.TRUTHFULNESS, Scope.INCREMENTAL,
                   metrics, (), description, run))


_identity(
    "A1.dimension.amount_vs_volume_close",
    ("tushare_daily.amount", "tushare_daily.volume"),
    ["symbol", "dt", "close", "volume", "amount"],
    lambda frame: frame.with_columns(
        pl.col("amount").alias("actual"),
        (pl.col("volume") * 100.0 * pl.col("close")).alias("expected"),
    ),
    "成交额约等于成交量（手）×100×收盘价",
    tolerance=0.05,
)
_identity(
    "A3.change_identity",
    ("tushare_daily.change", "tushare_daily.pre_close"),
    ["symbol", "dt", "close", "pre_close", "change"],
    lambda frame: frame.with_columns(
        pl.col("change").alias("actual"),
        (pl.col("close") - pl.col("pre_close")).alias("expected"),
    ),
    "涨跌额 = 收盘价 − 前收价",
)
_identity(
    "A3.pct_change_identity",
    ("tushare_daily.pct_change", "tushare_daily.close"),
    ["symbol", "dt", "close", "pre_close", "pct_change"],
    lambda frame: frame.with_columns(
        pl.col("pct_change").alias("actual"),
        ((pl.col("close") / pl.col("pre_close") - 1) * 100).alias("expected"),
    ),
    "涨跌幅（%）= (收盘价/前收价−1)×100",
)
_identity(
    "A3.market_cap_vs_shares",
    ("tushare_daily.market_cap", "tushare_daily.total_shares",
     "valuation_snapshot.market_cap", "valuation_snapshot.circulating_market_cap"),
    ["symbol", "dt", "close", "market_cap", "total_shares"],
    lambda frame: frame.with_columns(
        pl.col("market_cap").alias("actual"),
        (pl.col("close") * pl.col("total_shares")).alias("expected"),
    ),
    "总市值 = 收盘价 × 总股本",
)
_identity(
    "A3.turnover_identity",
    ("tushare_daily.turnover_ratio", "tushare_daily.circulating_shares",
     "valuation_snapshot.turnover_ratio"),
    ["symbol", "dt", "volume", "turnover_ratio", "circulating_shares"],
    lambda frame: frame.with_columns(
        pl.col("turnover_ratio").alias("actual"),
        (pl.col("volume") * 100 / pl.col("circulating_shares") * 100).alias("expected"),
    ),
    "换手率（%）= 成交量（手）×100/流通股本×100",
)


def _missed_exdiv(ctx: CheckContext) -> CheckResult:
    start = ctx.start - timedelta(days=10)
    frame = ctx.query.tushare_daily(
        None, start, ctx.end, columns=["symbol", "dt", "close", "pre_close", "factor"]
    ).sort(["symbol", "dt"]).with_columns(
        pl.col("close").shift(1).over("symbol").alias("previous_close"),
        pl.col("factor").shift(1).over("symbol").alias("previous_factor"),
    ).filter(
        pl.col("dt").is_between(ctx.start, ctx.end)
        & pl.col("previous_close").is_not_null()
        & pl.col("previous_factor").is_not_null()
        & (pl.col("previous_close") > 0)
        & (pl.col("previous_factor") > 0)
        & pl.col("pre_close").is_not_null()
        & pl.col("factor").is_not_null()
    ).with_columns(
        ((pl.col("pre_close") - pl.col("previous_close")).abs()
         / pl.col("previous_close")).alias("gap"),
        (pl.col("factor") / pl.col("previous_factor")).alias("factor_jump"),
        (pl.col("previous_close") / pl.col("pre_close")).alias("expected_jump"),
    )
    if frame.height == 0:
        return CheckResult(0, 0, "区间内没有可回看的股-日")
    bad = frame.filter(
        (pl.col("gap") > _GAP_TOL)
        & ((pl.col("factor_jump") - pl.col("expected_jump")).abs()
           / pl.col("expected_jump").abs() > _REL_TOL)
    )
    return CheckResult(bad.height, frame.height,
                       f"{bad.height} 个价格缺口没有匹配的因子跳变",
                       bad.head(5000) if bad.height else None)


register(Check(
    "A3.missed_exdiv", Category.TRUTHFULNESS, Scope.FULL,
    ("tushare_daily.factor", "tushare_daily.pre_close",
     "adj_factors.cum_factor", "paid_daily.factor"),
    ("D-1", "D-2"), "价格除权缺口必须有匹配的因子跳变", _missed_exdiv,
))


def _limit_list(ctx: CheckContext) -> CheckResult:
    flags = ctx.query.daily_flags(ctx.start, ctx.end)
    listed = ctx.query.limit_list(None, ctx.start, ctx.end)
    if flags.height == 0:
        return CheckResult(0, 0, "区间内无 daily_flags")
    mine = flags.filter(pl.col("is_limit_up")).select("symbol", "dt", "is_st")
    theirs = listed.filter(pl.col("limit_type") == "U").select("symbol", "dt")
    only_mine = mine.join(theirs, on=["symbol", "dt"], how="anti")
    only_theirs = theirs.join(mine.select("symbol", "dt"), on=["symbol", "dt"], how="anti")
    unexplained = only_mine.filter(~pl.col("is_st").fill_null(False))
    bad = pl.concat([
        unexplained.select("symbol", "dt").with_columns(pl.lit("只在自算且非ST").alias("side")),
        only_theirs.with_columns(pl.lit("只在limit_list").alias("side")),
    ])
    return CheckResult(bad.height, flags.height,
                       f"自算涨停 {mine.height}，接口涨停 {theirs.height}，无法解释 {bad.height}",
                       bad if bad.height else None)


register(Check(
    "A3.limit_list_vs_selfcalc", Category.TRUTHFULNESS, Scope.INCREMENTAL,
    ("daily_flags.is_limit_up", "daily_flags.is_first_board", "limit_list_daily.limit_type"),
    ("D-11",), "自算涨停与 limit_list_d 的差集只能是 ST", _limit_list,
))


def _limit_prices(ctx: CheckContext) -> CheckResult:
    flags = ctx.query.daily_flags(ctx.start, ctx.end).select(
        "symbol", "dt", pl.col("limit_up").alias("mine_up"),
        pl.col("limit_down").alias("mine_down"),
    )
    direct = ctx.query.price_limits(None, ctx.start, ctx.end).select(
        "symbol", "dt", pl.col("limit_up").alias("their_up"),
        pl.col("limit_down").alias("their_down"),
    )
    joined = flags.join(direct, on=["symbol", "dt"], how="inner").drop_nulls()
    if joined.height == 0:
        return CheckResult(0, 0, "两张表没有可比较的行")
    bad = joined.filter(
        ((pl.col("mine_up") - pl.col("their_up")).abs() > 0.005)
        | ((pl.col("mine_down") - pl.col("their_down")).abs() > 0.005)
    )
    return CheckResult(bad.height, joined.height, "自算涨跌停价与 stk_limit 对账",
                       bad.head(5000) if bad.height else None)


register(Check(
    "A3.limit_price_vs_selfcalc", Category.TRUTHFULNESS, Scope.INCREMENTAL,
    ("daily_flags.limit_up", "daily_flags.limit_down",
     "price_limits_daily.limit_up", "price_limits_daily.limit_down"),
    (), "自算涨跌停价与 Tushare stk_limit 直取值一致", _limit_prices,
))


def _minute_vs_daily(ctx: CheckContext) -> CheckResult:
    rows: list[dict[str, object]] = []
    checked = 0
    day = ctx.start
    cross_rules = {"volume_vs_daily", "high_vs_daily", "first_bar_open"}
    while day <= ctx.end:
        minutes = ctx.query.minute_bars(
            None, datetime.combine(day, time.min), datetime.combine(day, time.max)
        )
        daily = ctx.query.tushare_daily(None, day, day, columns=["symbol", "dt"])
        if minutes.height and daily.height:
            checked += minutes.select("symbol").unique().join(
                daily.select("symbol").unique(), on="symbol", how="inner"
            ).height
            for issue in check_minute_bars(ctx.warehouse, day):
                if issue.rule in cross_rules:
                    rows.append({
                        "symbol": issue.symbol,
                        "dt": issue.dt,
                        "rule": issue.rule,
                        "detail": issue.detail,
                    })
        day += timedelta(days=1)
    sample = pl.DataFrame(rows) if rows else None
    return CheckResult(len(rows), checked, f"{len(rows)} 个分钟/日线跨源差异", sample)


register(Check(
    "A3.minute_vs_daily", Category.TRUTHFULNESS, Scope.INCREMENTAL,
    ("minute_bars.open", "minute_bars.high", "minute_bars.volume",
     "tushare_daily.open", "tushare_daily.high", "tushare_daily.volume"),
    (), "分钟聚合与独立日线的开盘、最高和成交量一致", _minute_vs_daily,
))
