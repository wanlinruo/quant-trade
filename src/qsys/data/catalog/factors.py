"""因子类指标：market_regime 15 条和策略侧 10 个不落盘决策量。

因子与普通派生的边界是自由参数或口径选择。尤其是广度股票池必须成为
结构化参数：D-3 的公式没有错，错的是分母池子，单验公式发现不了它。
"""

from __future__ import annotations

from qsys.data.catalog.types import Computed, Kind, Metric, MetricStatus, Validity

DATASETS = frozenset({"market_regime"})

_BREADTH = "先排除代码前缀 4/8/68，再与 boards=main,gem,star 取交集"
_BREADTH_PARAMS = (("universe", _BREADTH), ("return_precision", 12))
_BREADTH_ISSUES = ("D-3", "D-5")
_GATE_CONSUMERS = ("S6 分类", "S7 Gate")


def _regime(
    column: str,
    meaning: str,
    upstream: tuple[str, ...],
    formula: str,
    checks: tuple[str, ...],
    *,
    params: tuple[tuple[str, object], ...] = _BREADTH_PARAMS,
    issues: tuple[str, ...] = _BREADTH_ISSUES,
    gate_input: bool = False,
) -> Metric:
    return Metric(
        id=f"market_regime.{column}",
        kind=Kind.FACTOR,
        validity=Validity.SESSION,
        status=MetricStatus.ACTIVE,
        meaning=meaning,
        lineage=Computed(upstream, formula, params),
        checks=checks,
        known_issues=tuple(dict.fromkeys(issues + (("D-4",) if gate_input else ()))),
        ext_consumers=_GATE_CONSUMERS if gate_input else (),
    )


def _strategy(
    column: str,
    meaning: str,
    upstream: tuple[str, ...],
    formula: str,
    params: tuple[tuple[str, object], ...],
    consumer: str,
    *,
    issues: tuple[str, ...] = (),
) -> Metric:
    return Metric(
        id=f"strategy.{column}",
        kind=Kind.FACTOR,
        validity=Validity.SESSION,
        status=MetricStatus.ACTIVE,
        meaning=meaning,
        lineage=Computed(upstream, formula, params),
        checks=("C1.no_fill",),
        known_issues=issues,
        ext_consumers=(consumer,),
        landed=False,
    )


_RETURN_UPSTREAM = (
    "tushare_daily.close",
    "daily_flags.prev_close",
    "adj_factors.cum_factor",
)

METRICS: tuple[Metric, ...] = (
    Metric(
        id="market_regime.dt",
        kind=Kind.DERIVED,
        validity=Validity.SESSION,
        status=MetricStatus.ACTIVE,
        meaning="交易日",
        lineage=Computed(("daily_flags.dt",), "按交易日聚合后的分组键"),
        checks=("B1.calendar",),
    ),
    _regime(
        "limit_up_count",
        "广度股票池内收盘涨停的标的数",
        ("daily_flags.is_limit_up",),
        "按 dt 对 is_limit_up 求和并转 Int64",
        ("A2.recompute.limit_up_count",),
    ),
    _regime(
        "limit_down_count",
        "广度股票池内收盘跌停的标的数",
        ("daily_flags.is_limit_down",),
        "按 dt 对 is_limit_down 求和并转 Int64",
        ("A2.recompute.limit_down_count",),
    ),
    _regime(
        "limit_up_down_ratio",
        "涨停家数与跌停家数之比；无跌停时分母保底为 1",
        ("market_regime.limit_up_count", "market_regime.limit_down_count"),
        "limit_up_count / max(limit_down_count, 1)",
        ("A2.recompute.limit_up_down_ratio",),
        params=(("universe", _BREADTH), ("denominator_floor", 1)),
    ),
    _regime(
        "touched_limit_up_count",
        "S6 活跃度：广度股票池内盘中触及涨停的标的数",
        ("daily_flags.touched_limit_up",),
        "按 dt 对 touched_limit_up 求和并转 Int64",
        ("A2.recompute.touched_limit_up_count",),
        gate_input=True,
    ),
    _regime(
        "broken_board_count",
        "广度股票池内炸板标的数",
        ("daily_flags.is_broken_board",),
        "按 dt 对 is_broken_board 求和并转 Int64",
        ("A2.recompute.broken_board_count",),
    ),
    _regime(
        "broken_board_rate",
        "炸板数占触板数的比例；没有触板时记真实的 0",
        (
            "market_regime.broken_board_count",
            "market_regime.touched_limit_up_count",
        ),
        "touched_limit_up_count>0 ? broken_board_count/touched_limit_up_count : 0",
        ("A2.recompute.broken_board_rate",),
        params=(("universe", _BREADTH), ("zero_touched_result", 0.0)),
    ),
    _regime(
        "first_board_count",
        "广度股票池内首板标的数",
        ("daily_flags.is_first_board",),
        "按 dt 对 is_first_board 求和并转 Int64",
        ("A2.recompute.first_board_count",),
    ),
    _regime(
        "market_advance_ratio",
        "S6 广度：上涨标的数占上涨与下跌标的数之和；平盘不进分母",
        _RETURN_UPSTREAM,
        "上涨数 / max(上涨+下跌, 1)；平盘与 null 均不进分母；"
        "ret_1d=round(close/prev_close*日因子-1, 12)",
        ("A2.recompute.market_advance_ratio",),
        gate_input=True,
    ),
    _regime(
        "market_median_return_1d",
        "S6 广度：广度股票池复权日收益的中位数",
        _RETURN_UPSTREAM,
        "median(round(close/prev_close*(cum_factor(t)/cum_factor(t-1))-1, 12))；"
        "没有因子记录时日因子按 1",
        ("A2.recompute.market_median_return_1d",),
        gate_input=True,
    ),
    _regime(
        "market_total_money",
        "广度股票池当日成交额合计（元）",
        ("tushare_daily.amount",),
        "按 dt 对 amount 求和",
        ("A1.dimension.amount_vs_volume_close", "A2.recompute.market_total_money"),
    ),
    _regime(
        "prior_limitup_nextday_return_mean",
        "S6 热度：昨日涨停标的今日复权收益均值",
        ("daily_flags.is_limit_up",) + _RETURN_UPSTREAM,
        "每只标的取昨日 is_limit_up；筛出 True 后对今日 ret_1d 求均值",
        ("A2.recompute.prior_limitup_nextday_return_mean",),
        gate_input=True,
    ),
    _regime(
        "prior_limitup_nextday_return_median",
        "昨日涨停标的今日复权收益中位数",
        ("daily_flags.is_limit_up",) + _RETURN_UPSTREAM,
        "每只标的取昨日 is_limit_up；筛出 True 后对今日 ret_1d 求中位数",
        ("A2.recompute.prior_limitup_nextday_return_median",),
    ),
    _regime(
        "valid_stock_count",
        "当日成功连接日线与 daily_flags 的广度股票池行数；这是家数类因子的口径分母",
        ("tushare_daily.symbol", "daily_flags.symbol"),
        "日线与 daily_flags 按 (symbol, dt) inner join 后按 dt 计行数",
        ("A2.recompute.valid_stock_count", "B3.null_rate"),
    ),
    _regime(
        "index_ma20_slope_5d",
        "S6 趋势：沪深300的 MA20 相对 5 个交易日前 MA20 的变化率；"
        "不足 25 根或没有指数数据时留空，不填 0",
        ("index_bars.close",),
        "rolling_mean(close, 20)(t) / rolling_mean(close, 20)(t-5) - 1",
        ("A2.recompute.index_ma20_slope_5d", "C1.no_fill"),
        params=(("ma_window", 20), ("lookback", 5), ("index", "000300.SH")),
        issues=("D-4",),
        gate_input=True,
    ),
    _strategy(
        "stock_return_10d",
        "S6 LOW_MAIN 个股因子：近 10 个交易日收益率",
        ("tushare_daily.close", "adj_factors.cum_factor"),
        "close(t)/close(t-10)-1；不足 11 根或 t-10 收盘为 0 时 null",
        (("window", 10),),
        "S6 分类 _classify_low_main",
        issues=("D-4", "D-9"),
    ),
    _strategy(
        "distance_to_60d_high",
        "S6 LOW_MAIN 个股因子：收盘价相对近 60 日最高价的距离",
        ("tushare_daily.close", "tushare_daily.high", "adj_factors.cum_factor"),
        "close(t)/max(high[-60:])-1；不足 60 根或最高价为 0 时 null",
        (("window", 60),),
        "S6 分类 _classify_low_main",
        issues=("D-4", "D-9"),
    ),
    _strategy(
        "low_relative_position_60d",
        "低开入场：昨日收盘在最近 60 根日线高低区间内的相对位置",
        (
            "tushare_daily.close",
            "tushare_daily.high",
            "tushare_daily.low",
            "adj_factors.cum_factor",
        ),
        "(last_close-min(low[-60:]))/(max(high[-60:])-min(low[-60:])); 区间为 0 时无结果",
        (("window", 60), ("max", 0.5)),
        "check_low_open",
    ),
    _strategy(
        "low_auction_price_ratio",
        "低开入场：竞价开盘价相对昨日收盘价的比值",
        ("tushare_daily.open", "tushare_daily.close", "adj_factors.cum_factor"),
        "session_open(当日 tushare_daily.open) / 前复权历史的 last_close；"
        "历史锚在昨日，故 last_close 保持原价",
        (("lower_inclusive", 0.955), ("upper_inclusive", 0.97)),
        "check_low_open",
    ),
    _strategy(
        "high_avg_inc",
        "高开入场：昨日成交均价相对前日收盘的涨幅；日线 volume 单位为手",
        (
            "tushare_daily.amount",
            "tushare_daily.volume",
            "tushare_daily.close",
            "adj_factors.cum_factor",
            "adj_factors.cum_share_factor",
        ),
        "vwap=prev_amount/(prev_volume(手)*100)；high_avg_inc=vwap/pre_prev_close-1",
        (("lot_size", 100), ("minimum", 0.07)),
        "check_high_open",
    ),
    _strategy(
        "high_auction_volume_ratio",
        "高开入场：竞价量相对昨日成交量的比值，两者单位均为手",
        ("minute_bars.volume", "tushare_daily.volume", "adj_factors.cum_share_factor"),
        "auction_volume/prev_volume；竞价量缺失且允许跳过时不计算",
        (("minimum", 0.03), ("require_by_default", True)),
        "check_high_open",
    ),
    _strategy(
        "high_auction_price_ratio",
        "高开入场：竞价开盘价相对昨日收盘价的比值",
        ("tushare_daily.open", "tushare_daily.close", "adj_factors.cum_factor"),
        "session_open(当日 tushare_daily.open) / 前复权历史的 prev_close；"
        "历史锚在昨日，故 prev_close 保持原价",
        (("lower_exclusive", 1.0), ("upper_exclusive", 1.06)),
        "check_high_open",
    ),
    _strategy(
        "low_main_auction_volume_ratio",
        "LOW_MAIN 附加过滤：竞价量相对昨日成交量的比值",
        ("minute_bars.volume", "tushare_daily.volume", "adj_factors.cum_share_factor"),
        "auction_volume/prev_volume；竞价量缺失时由 require_auction_volume 决定是否跳过",
        (("minimum", 0.02), ("require_by_default", True)),
        "StructuralSelector._auction_volume_ok",
    ),
    _strategy(
        "left_pressure_days",
        "左侧压力：从倒数第 3 根开始向前寻找不低于昨日最高价的最近距离",
        ("tushare_daily.high", "adj_factors.cum_factor"),
        "从 i=3 到 len(highs)-1，首个 highs[-i]>=highs[-1] 时取 i-1；"
        "找不到时取 100",
        (("history_window", 101), ("search_start", 3), ("default", 100)),
        "check_left_pressure",
    ),
    _strategy(
        "left_pressure_volume_ratio",
        "左压观察窗内昨日成交量相对更早最大成交量的比值",
        (
            "tushare_daily.volume",
            "adj_factors.cum_share_factor",
            "strategy.left_pressure_days",
        ),
        "window=volume[-(left_pressure_days+5):]；last/max(window[:-1])",
        (("padding", 5), ("minimum_exclusive", 0.9)),
        "check_left_pressure",
    ),
)
