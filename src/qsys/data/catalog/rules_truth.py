"""A 类真实性规则：独立量纲反查与跨源、跨表恒等式。"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Callable

import polars as pl

from qsys.data import schema
from qsys.data.catalog.checks import Category, Check, CheckContext, CheckResult, Scope, register
from qsys.data.quality.minute_rules import check_minute_bars

_REL_TOL = 1e-4
_GAP_TOL = 0.005
# Tushare adj_factor 生产中常见只有 3 位小数（2026-09-17：5,553 只里 1,370 只），
# 单个因子的舍入误差最多 ±0.0005，会传到相邻两日因子之比上。
_FACTOR_ROUNDING = 0.0005
_FOUR_DECIMAL_ABS_TOL = 0.0000500001


def _relative_bad(
    frame: pl.DataFrame, tolerance: float, absolute_tolerance: float = 0.0,
) -> tuple[pl.DataFrame, int]:
    comparable = frame.filter(
        pl.col("actual").is_not_null()
        & pl.col("expected").is_not_null()
        & (pl.col("expected").abs() > 0)
    )
    difference = (pl.col("actual") - pl.col("expected")).abs()
    bad = comparable.filter(
        (difference > absolute_tolerance)
        & (difference / pl.col("expected").abs() > tolerance)
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
    absolute_tolerance: float = 0.0,
    upstream_self_consistency: bool = False,
) -> None:
    def run(ctx: CheckContext) -> CheckResult:
        source = ctx.query.tushare_daily(None, ctx.start, ctx.end, columns=columns)
        if source.height == 0:
            return CheckResult(0, 0, "区间内无日线数据")
        bad, checked = _relative_bad(build(source), tolerance, absolute_tolerance)
        return CheckResult(
            bad.height, checked,
            f"{bad.height} / {checked} 行超出相对容差 {tolerance}",
            bad.head(5000) if bad.height else None,
        )

    register(Check(check_id, Category.TRUTHFULNESS, Scope.INCREMENTAL,
                   metrics, (), description, run,
                   upstream_self_consistency=upstream_self_consistency))


def _amount_dimension_split(ctx: CheckContext) -> tuple[int, pl.DataFrame, pl.DataFrame]:
    """返回 (可比行数, 未解释违规, 被直取大宗解释的违规)；两条 A1 规则共用一次计算。

    成交均价必须落在当日最低价和最高价之间。成交额 / 成交量得到的是 VWAP，
    不是收盘价；边界外扩 0.1% 只吸收价格和金额舍入。

    #608：日线总成交可能包含大宗交易（北交所口径不稳定）。只对**已违规**的行，
    在当日该标的存在直取 ``block_trade_daily`` 记录、扣除后剩余量为正、且剩余均价
    回到同样的 [low, high] 区间时，才视为“已由直取大宗解释”；其余一律维持违规。
    未违规的行不做任何扣除；大宗明细未入库时没有证据，同样维持违规。
    """
    key = "A1.amount_dimension_split"
    if key in ctx.cache:
        return ctx.cache[key]
    frame = ctx.query.tushare_daily(
        None, ctx.start, ctx.end,
        columns=["symbol", "dt", "low", "high", "volume", "amount"],
    ).drop_nulls(["low", "high", "volume", "amount"]).filter(pl.col("volume") > 0)
    checked = frame.with_columns(
        (pl.col("volume") * 100.0 * pl.col("low") * 0.999).alias("lower_bound"),
        (pl.col("volume") * 100.0 * pl.col("high") * 1.001).alias("upper_bound"),
    )
    bad = checked.filter(
        (pl.col("amount") < pl.col("lower_bound"))
        | (pl.col("amount") > pl.col("upper_bound"))
    )
    explained = bad.head(0)
    if bad.height:
        blocks = ctx.query.block_trades(
            bad["symbol"].unique().to_list(), ctx.start, ctx.end,
        ).group_by(["symbol", "dt"]).agg(
            pl.len().cast(pl.Int64).alias("block_trades"),
            # 源单位万股 → 手：纯算术（1 万股 = 100 手）
            (pl.col("vol").sum() * 100.0).alias("block_volume_hands"),
            pl.col("amount").sum().alias("block_amount"),
        )
        joined = bad.join(blocks, on=["symbol", "dt"], how="left").with_columns(
            (pl.col("volume") - pl.col("block_volume_hands")).alias("remaining_volume"),
            (pl.col("amount") - pl.col("block_amount")).alias("remaining_amount"),
        ).with_columns(
            (pl.col("remaining_amount") / (pl.col("remaining_volume") * 100.0))
            .alias("remaining_vwap"),
        )
        is_explained = (
            pl.col("block_trades").is_not_null()
            & (pl.col("remaining_volume") > 0)
            & (pl.col("remaining_amount")
               >= pl.col("remaining_volume") * 100.0 * pl.col("low") * 0.999)
            & (pl.col("remaining_amount")
               <= pl.col("remaining_volume") * 100.0 * pl.col("high") * 1.001)
        )  # 无大宗时首项为 False，按 Kleene 与整体为 False，不需要也不允许填充
        explained = joined.filter(is_explained)
        bad = joined.filter(~is_explained).select(bad.columns)
    ctx.cache[key] = (checked.height, bad, explained)
    return ctx.cache[key]


def _amount_dimension(ctx: CheckContext) -> CheckResult:
    total, bad, _explained = _amount_dimension_split(ctx)
    if total == 0:
        return CheckResult(0, 0, "区间内没有正成交量的可比日线")
    return CheckResult(
        bad.height, total,
        f"{bad.height} / {total} 行的成交均价超出最低价/最高价区间且无直取大宗解释",
        bad.head(5000) if bad.height else None,
    )


def _amount_dimension_block_explained(ctx: CheckContext) -> CheckResult:
    total, _bad, explained = _amount_dimension_split(ctx)
    if total == 0:
        return CheckResult(0, 0, "区间内没有正成交量的可比日线")
    return CheckResult(
        explained.height, total,
        f"{explained.height} / {total} 行总成交含当日直取大宗交易，扣除后均价回到区间（证据见样本）",
        explained.head(5000) if explained.height else None,
    )


register(Check(
    "A1.dimension.amount_vs_volume_close", Category.TRUTHFULNESS, Scope.INCREMENTAL,
    ("tushare_daily.amount", "tushare_daily.volume"), (),
    "成交额/成交量所得均价必须落在当日最低价和最高价之间", _amount_dimension,
))
register(Check(
    "A1.dimension.amount_vs_volume_block_explained", Category.TRUTHFULNESS, Scope.INCREMENTAL,
    ("tushare_daily.amount", "tushare_daily.volume"), ("#608",),
    "总成交含大宗交易的量纲差异：仅当日有直取大宗且扣除后均价回到区间时降为已解释警告",
    _amount_dimension_block_explained,
))
_identity(
    "A3.change_identity",
    ("tushare_daily.change", "tushare_daily.pre_close"),
    ["symbol", "dt", "close", "pre_close", "change"],
    lambda frame: frame.with_columns(
        pl.col("change").alias("actual"),
        (pl.col("close") - pl.col("pre_close")).alias("expected"),
    ),
    "涨跌额 = 收盘价 − 前收价",
    upstream_self_consistency=True,
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
    absolute_tolerance=_FOUR_DECIMAL_ABS_TOL,
    upstream_self_consistency=True,
)
# daily_basic 的 total_mv/circ_mv 在股本变化后常仍按旧股本计价若干个交易日：
# 生产 2026 年 95 只出现过，窗口放到 5 个交易日后只剩 9 只，均为数十天不跟上
# 或流通股本量级错误（CDR）的真异常。
MARKET_CAP_SHARE_LAG_SESSIONS = 5


def _market_cap_share_bounds(ctx: CheckContext) -> CheckResult:
    """校验市值采用的股本口径边界，不强求跨市场公司满足错误恒等式。

    A+H/ADR 公司的 ``total_share`` 可包含境外股份，而 A 股 ``total_mv`` 的
    计价股本口径可能更窄；所以 total_mv = A股收盘价 × 全球总股本并非恒等式。
    始终成立的是：总股本不少于总市值反推的计价股本，后者不少于 A 股流通
    股本；流通市值则应与 A 股收盘价 × 流通股本一致。

    源的市值会滞后股本变化，所以股本取「当日及前
    ``MARKET_CAP_SHARE_LAG_SESSIONS`` 个交易日」实际发布过的值，不做任何反推。
    """
    lag = MARKET_CAP_SHARE_LAG_SESSIONS
    frame = ctx.query.tushare_daily(
        None, ctx.start - timedelta(days=lag * 2 + 20), ctx.end,
        columns=["symbol", "dt", "close", "market_cap", "total_shares",
                 "circulating_market_cap", "circulating_shares"],
    ).drop_nulls().sort(["symbol", "dt"])
    recent_total = [pl.col("total_shares").shift(k).over("symbol") for k in range(lag + 1)]
    recent_circulating = [pl.col("circulating_shares").shift(k).over("symbol")
                          for k in range(lag + 1)]
    frame = frame.with_columns(
        pl.max_horizontal(recent_total).alias("recent_max_total_shares"),
        pl.min_horizontal(recent_circulating).alias("recent_min_circulating_shares"),
        pl.any_horizontal([
            # 前面没有那么多交易日时 share 为空：记为不匹配（Kleene 与：False & null = False）
            share.is_not_null()
            & ((pl.col("circulating_market_cap") / pl.col("close") - share).abs() / share
               <= _REL_TOL)
            for share in recent_circulating
        ]).alias("circulating_matches_recent"),
    ).filter(
        pl.col("dt").is_between(ctx.start, ctx.end)
        & (pl.col("close") > 0)
        & (pl.col("market_cap") > 0)
        & (pl.col("total_shares") > 0)
        & (pl.col("circulating_market_cap") > 0)
        & (pl.col("circulating_shares") > 0)
    ).with_columns(
        (pl.col("market_cap") / pl.col("close")).alias("implied_priced_shares"),
        (pl.col("circulating_market_cap") / pl.col("close"))
        .alias("implied_circulating_shares"),
    )
    if frame.height == 0:
        return CheckResult(0, 0, "区间内没有完整的市值/股本可比行")
    tolerance = _REL_TOL
    bad = frame.filter(
        (pl.col("implied_priced_shares") > pl.col("recent_max_total_shares") * (1 + tolerance))
        | (pl.col("recent_min_circulating_shares")
           > pl.col("implied_priced_shares") * (1 + tolerance))
        | (pl.col("circulating_shares") > pl.col("total_shares") * (1 + tolerance))
        | ~pl.col("circulating_matches_recent")
    )
    return CheckResult(
        bad.height, frame.height,
        f"{bad.height} / {frame.height} 行违反市值计价股本边界，或流通市值不匹配"
        f"当日及前 {lag} 个交易日发布过的任一流通股本",
        bad.head(5000) if bad.height else None,
    )


register(Check(
    "A3.market_cap_vs_shares", Category.TRUTHFULNESS, Scope.INCREMENTAL,
    ("tushare_daily.market_cap", "tushare_daily.total_shares",
     "tushare_daily.circulating_market_cap", "tushare_daily.circulating_shares",
     "valuation_snapshot.market_cap", "valuation_snapshot.circulating_market_cap"),
    (), "市值计价股本满足包含边界，且流通市值 = 收盘价 × 流通股本",
    _market_cap_share_bounds,
    # 全部为 Tushare 直取原值：违规即上游自相矛盾（#653：600292.SH 的 circ_mv 与
    # 交易所公告的无限售流通股一致，float_share 不一致）
    upstream_self_consistency=True,
))
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
    absolute_tolerance=_FOUR_DECIMAL_ABS_TOL,
    upstream_self_consistency=True,
)


def _missed_exdiv(ctx: CheckContext) -> CheckResult:
    start = ctx.start - timedelta(days=10)
    source = ctx.query.tushare_daily(
        None, start, ctx.end, columns=["symbol", "dt", "close", "pre_close", "factor"]
    )
    security = ctx.query.security_master()
    if security.height:
        source = source.join(
            security.select("symbol", "list_date").unique("symbol"),
            on="symbol", how="left",
        ).filter(
            pl.col("list_date").is_null() | (pl.col("dt") >= pl.col("list_date"))
        ).drop("list_date")
    frame = source.sort(["symbol", "dt"]).with_columns(
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
    # 因子之比的舍入误差上界：jump × (δ/factor + δ/previous_factor)
    rounding = pl.col("factor_jump") * (
        _FACTOR_ROUNDING / pl.col("factor") + _FACTOR_ROUNDING / pl.col("previous_factor")
    )
    bad = frame.filter(
        (pl.col("gap") > _GAP_TOL)
        & ((pl.col("factor_jump") - pl.col("expected_jump")).abs()
           > pl.col("expected_jump").abs() * _REL_TOL + rounding)
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
    if listed.height == 0:
        return CheckResult(
            0, 0, "limit_list_d 尚未到货；到货异常由 B4.zero_limit_up 单独报告",
            applicable=False,
        )
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


def _limit_pct_reproduces(ctx: CheckContext) -> CheckResult:
    """档位必须能精确还原出同一行的涨跌停价。

    这是 D-13 的不变式。2026-09-15 的直取切换把价格换成了交易所公布值，
    却把档位留在旧规则上（「ST 一律 5%」），当天全仓有 68,649 行两者
    互相矛盾——同一行里 limit_up 是 20% 档的价，limit_pct 写着 0.05。
    这种矛盾不会让任何东西报错，只会让读 limit_pct 的下游安静地算错。

    limit_pct 为 null 的行不参与：那是「无涨跌幅限制」等还原不出档位的
    情形，没有档位不等于档位错。
    """
    frame = ctx.query.daily_flags(ctx.start, ctx.end).drop_nulls(
        ["adj_prev_close", "limit_pct", "limit_up", "limit_down"]
    )
    if frame.height == 0:
        return CheckResult(0, 0, "没有同时具备档位与价格的行")
    cents = (pl.col("adj_prev_close") * 100).round().cast(pl.Int64)

    def price(sign: int) -> pl.Expr:
        milli = ((1 + sign * pl.col("limit_pct")) * 1000).round().cast(pl.Int64)
        return (((cents * milli + 500) // 1000).cast(pl.Float64) / 100).round(2)

    bad = frame.filter(
        ((price(1) - pl.col("limit_up")).abs() > 0.005)
        | ((price(-1) - pl.col("limit_down")).abs() > 0.005)
    )
    return CheckResult(
        bad.height, frame.height,
        f"{bad.height} / {frame.height} 行的档位还原不出自己的涨跌停价",
        bad.head(5000) if bad.height else None,
    )


register(Check(
    "A3.limit_pct_reproduces_price", Category.TRUTHFULNESS, Scope.INCREMENTAL,
    ("daily_flags.limit_pct", "daily_flags.limit_up", "daily_flags.limit_down"),
    (), "档位必须能精确还原出同一行的涨跌停价", _limit_pct_reproduces,
))


register(Check(
    "A3.limit_price_vs_selfcalc", Category.TRUTHFULNESS, Scope.INCREMENTAL,
    ("daily_flags.limit_up", "daily_flags.limit_down",
     "price_limits_daily.limit_up", "price_limits_daily.limit_down"),
    (), "daily_flags 的涨跌停价与 stk_limit 直取值一致——验的是接线没断，不是自算对不对", _limit_prices,
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
                if issue.rule in cross_rules and issue.known_issue is None:
                    rows.append({
                        "symbol": issue.symbol,
                        "dt": issue.dt,
                        "rule": issue.rule,
                        "detail": issue.detail,
                    })
        day += timedelta(days=1)
    sample = pl.DataFrame(rows) if rows else None
    if checked == 0:
        return CheckResult(
            0, 0, "范围内没有同时到货的 QMT 分钟线与日线",
            applicable=False,
        )
    return CheckResult(len(rows), checked, f"{len(rows)} 个分钟/日线跨源差异", sample)


register(Check(
    "A3.minute_vs_daily", Category.TRUTHFULNESS, Scope.INCREMENTAL,
    ("minute_bars.open", "minute_bars.high", "minute_bars.volume",
     "tushare_daily.open", "tushare_daily.high", "tushare_daily.volume"),
    (), "分钟聚合与独立日线的开盘、最高和成交量一致", _minute_vs_daily,
))


def _minute_known_source_difference(ctx: CheckContext) -> CheckResult:
    """D-24：仅报告已逐项登记的 QMT 源差异，不能吞掉新异常。"""
    rows: list[dict[str, object]] = []
    checked = 0
    day = ctx.start
    while day <= ctx.end:
        issues = check_minute_bars(ctx.warehouse, day)
        checked += sum(issue.rule == "high_vs_daily" for issue in issues)
        rows.extend({
            "symbol": issue.symbol,
            "dt": issue.dt,
            "rule": issue.rule,
            "detail": issue.detail,
            "known_issue": issue.known_issue,
        } for issue in issues if issue.known_issue == "D-24")
        day += timedelta(days=1)
    return CheckResult(
        len(rows), max(checked, len(rows)),
        f"{len(rows)} 个已登记的 QMT 分钟源差异（D-24）",
        pl.DataFrame(rows) if rows else None,
        applicable=bool(checked),
    )


register(Check(
    "A3.minute_known_source_difference", Category.TRUTHFULNESS, Scope.INCREMENTAL,
    ("minute_bars.high",), ("D-24",),
    "精确登记的 QMT 分钟源差异保持可见，但不放宽其它差异的门禁",
    _minute_known_source_difference,
))


def sampling_worklist(ctx: CheckContext, limit: int = 200) -> pl.DataFrame:
    """A4：按可疑度导出待人工核验的因子清单。"""
    result = _missed_exdiv(ctx)
    if result.sample is None or result.sample.height == 0:
        return pl.DataFrame(schema={
            "metric_id": pl.Utf8,
            "symbol": pl.Utf8,
            "dt": pl.Date,
            "our_value": pl.Utf8,
            "why": pl.Utf8,
            "priority": pl.Float64,
        })
    return (
        result.sample.with_columns(
            pl.lit("tushare_daily.factor").alias("metric_id"),
            pl.col("factor").cast(pl.Utf8).alias("our_value"),
            (
                pl.lit("价格缺口 ")
                + (pl.col("gap") * 100).round(2).cast(pl.Utf8)
                + pl.lit(
                    "% 而 factor 未匹配跳变；去同花顺核对该日是否除权除息"
                )
            ).alias("why"),
            pl.col("gap").alias("priority"),
        )
        .select("metric_id", "symbol", "dt", "our_value", "why", "priority")
        .sort("priority", descending=True)
        .head(limit)
    )


# ---- legacy 与 canonical 市场情绪逐日逐列一致（#663）----
# 两张表由同一套派生逻辑从各自的输入独立生成，S7 Gate 读 canonical。
# 2026-06-08 曾因构建窗口不同出现 prior_limitup mean 0.0066 vs 0.0176，增量 A2
# 重算只看当日，查不出这种历史行。差异即本系统派生不一致，按阻断。
_REGIME_TOL = 1e-12


def _regime_legacy_vs_canonical(ctx: CheckContext) -> CheckResult:
    legacy = ctx.query.market_regime(ctx.start, ctx.end)
    canonical = ctx.query.market_regime_daily(ctx.start, ctx.end)
    if legacy.height == 0 and canonical.height == 0:
        return CheckResult(0, 0, "两张市场情绪表在区间内都没有数据", applicable=False)
    columns = [c for c in canonical.columns
               if c != "dt" and c in legacy.columns and canonical[c].dtype.is_numeric()]
    joined = legacy.select(["dt", *columns]).join(
        canonical.select(["dt", *columns]), on="dt", how="full",
        suffix="_canonical", coalesce=True,
    )
    missing = joined.filter(
        ~pl.col("dt").is_in(legacy["dt"].implode()) | ~pl.col("dt").is_in(canonical["dt"].implode())
    )
    rows: list[dict] = [
        {"dt": dt, "column": "dt", "legacy": None, "canonical": None}
        for dt in missing["dt"].to_list()
    ]
    present = joined.filter(~pl.col("dt").is_in(missing["dt"].implode()))
    failed: set[str] = set()
    for column in columns:
        a, b = pl.col(column).cast(pl.Float64), pl.col(f"{column}_canonical").cast(pl.Float64)
        bad = present.filter(
            (a.is_not_null() & b.is_not_null() & ((a - b).abs() > _REGIME_TOL))
            | (a.is_null() != b.is_null())
        )
        if bad.height:
            failed.add(f"market_regime_daily.{column}")
        rows.extend({"dt": r[0], "column": column, "legacy": r[1], "canonical": r[2]}
                    for r in bad.select("dt", column, f"{column}_canonical").iter_rows())
    if missing.height:
        failed.add("market_regime_daily.dt")
    checked = present.height * len(columns) + missing.height
    sample = pl.DataFrame(rows, schema={"dt": pl.Date, "column": pl.Utf8,
                                        "legacy": pl.Float64, "canonical": pl.Float64}) \
        if rows else None
    return CheckResult(
        len(rows), checked,
        f"{len(rows)} / {checked} 个日期×列单元格 legacy 与 canonical 不一致"
        f"（缺日 {missing.height}）",
        sample, failed_metrics=tuple(sorted(failed)) if failed else None,
    )


register(Check(
    "A3.regime_legacy_vs_canonical", Category.TRUTHFULNESS, Scope.FULL,
    tuple(f"market_regime_daily.{c}" for c in schema.MARKET_REGIME_DAILY.names),
    (), "legacy market_regime 与 canonical market_regime_daily 逐日逐列一致",
    _regime_legacy_vs_canonical,
))
