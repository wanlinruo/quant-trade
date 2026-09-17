"""派生类指标 26 条。

每条 ``formula`` 是 A2「独立重算」的规格书：规则实现必须另写计算路径，
不能调用这里描述的生产函数。血缘以每日生产调度实际调用的路径为准；
``valuation_snapshot`` 另有 Akshare 当日快照入口，但它不是每日同步的生产路径。
"""

from __future__ import annotations

from qsys.data.catalog.types import UNCOVERED, Computed, Kind, Metric, MetricStatus, Validity

DATASETS = frozenset({"daily_flags", "valuation_snapshot", "delisting_pool"})


def _m(
    metric_id: str,
    meaning: str,
    upstream: tuple[str, ...],
    formula: str,
    checks: tuple[str, ...],
    issues: tuple[str, ...] = (),
) -> Metric:
    return Metric(
        id=metric_id,
        kind=Kind.DERIVED,
        validity=Validity.SESSION,
        status=MetricStatus.ACTIVE,
        meaning=meaning,
        lineage=Computed(upstream=upstream, formula=formula),
        checks=checks,
        known_issues=issues,
    )


# 退市整理期池：到货与一致性由每日服务质检独立重算 namechange 核对，指标规则不重复
_POOL_CHECKS = UNCOVERED(
    "每日服务质检按 name_history 独立重算当日池子并逐只核对（scripts/service_checks.py）"
)
_POOL_FORMULA = ("name_history 中 change_reason='退市整理期' 且 start_date <= dt <= end_date"
                 "（end_date 为空视为未结束）的区间，与 tushare_daily 交易日交叉")


def _pool(column: str, meaning: str, upstream: str) -> Metric:
    return Metric(
        id=f"delisting_pool.{column}", kind=Kind.DERIVED, validity=Validity.SESSION,
        status=MetricStatus.ACTIVE, meaning=meaning,
        lineage=Computed(upstream=(upstream,), formula=_POOL_FORMULA), checks=_POOL_CHECKS,
    )


METRICS: tuple[Metric, ...] = (
    _m(
        "daily_flags.dt",
        "交易日",
        ("tushare_daily.dt",),
        "透传 tushare_daily.dt",
        ("B1.calendar",),
    ),
    _m(
        "daily_flags.symbol",
        "标的代码",
        ("tushare_daily.symbol",),
        "透传 tushare_daily.symbol",
        ("B3.null_rate",),
    ),
    _m(
        "daily_flags.prev_close",
        "前一根日线的收盘价（元）；每只标的的首根记录为 null，不填充",
        ("tushare_daily.close",),
        "按 (symbol, dt) 排序，对每只 symbol 取 close.shift(1)；首根记 null",
        ("A2.recompute.prev_close", "C1.no_fill"),
    ),
    _m(
        "daily_flags.adj_prev_close",
        "除权除息参考价（元）；涨跌停按它计算。优先取 stk_limit 随涨跌停价"
        "一并给出的 pre_close，未回填的历史行退回 tushare_daily.pre_close。"
        "两个候选实测逐行相同（切源对账 adj_prev_close 差异 0 行）",
        ("price_limits_daily.pre_close", "tushare_daily.pre_close"),
        "coalesce(price_limits_daily.pre_close, tushare_daily.pre_close)",
        ("A2.recompute.adj_prev_close",),
    ),
    _m(
        "daily_flags.limit_up",
        "涨停价（元），**只认 stk_limit 的交易所公布值**（D-11）。没有直取行"
        "就是 null，不按板块档位或 ST 档位自算",
        ("price_limits_daily.limit_up",),
        "price_limits_daily.limit_up 按 (symbol, dt) 左连透传；缺行为 null",
        ("A2.recompute.limit_up", "A3.limit_price_vs_selfcalc",
         "A3.limit_pct_reproduces_price"),
    ),
    _m(
        "daily_flags.limit_down",
        "跌停价（元）。与 limit_up 同源同法：只认 stk_limit 直取值，缺行为 null",
        ("price_limits_daily.limit_down",),
        "price_limits_daily.limit_down 按 (symbol, dt) 左连透传；缺行为 null",
        ("A2.recompute.limit_down", "A3.limit_price_vs_selfcalc",
         "A3.limit_pct_reproduces_price"),
    ),
    _m(
        "daily_flags.is_limit_up",
        "收盘封住涨停；涨跌停价缺失时为 null（不知道就不宣称是或否）",
        ("tushare_daily.close", "daily_flags.limit_up"),
        "close >= limit_up；任一侧为 null 则 null",
        ("A2.recompute.is_limit_up", "A3.limit_list_vs_selfcalc"),
    ),
    _m(
        "daily_flags.is_limit_down",
        "收盘封住跌停；涨跌停价缺失时为 null（不知道就不宣称是或否）",
        ("tushare_daily.close", "daily_flags.limit_down"),
        "close <= limit_down；任一侧为 null 则 null",
        ("A2.recompute.is_limit_down",),
    ),
    _m(
        "daily_flags.touched_limit_up",
        "盘中触及涨停（含封住）；涨跌停价缺失时为 null（不知道就不宣称是或否）",
        ("tushare_daily.high", "daily_flags.limit_up"),
        "high >= limit_up；任一侧为 null 则 null",
        ("A2.recompute.touched_limit_up",),
    ),
    _m(
        "daily_flags.is_broken_board",
        "炸板：盘中触及涨停但收盘未封住",
        ("daily_flags.touched_limit_up", "daily_flags.is_limit_up"),
        "touched_limit_up and not is_limit_up",
        ("A2.recompute.is_broken_board",),
    ),
    _m(
        "daily_flags.is_first_board",
        "首板：今日涨停且昨日未涨停",
        ("daily_flags.is_limit_up",),
        "is_limit_up(t) and not is_limit_up(昨日)；"
        "窗口内首根没有昨日观测时 shift(fill_value=False)；昨日为 null 则为 null",
        ("A2.recompute.is_first_board", "A3.limit_list_vs_selfcalc"),
    ),
    _m(
        "daily_flags.limit_pct",
        "该行 limit_up 实际对应的那一档涨跌幅，**由直取涨停价解码得出**，"
        "与同一行的 limit_up / limit_down 恒自洽；解不出记 null（新股上市"
        "初期无涨跌幅限制时 stk_limit 返回 up_limit=1,000,000，实测 2,283 行）。"
        "**不能用它判 ST**：2026-07-06 起主板 ST 与普通股同为 0.10，"
        "创业板/科创板 ST 一直是 0.20（D-13/D-14）",
        ("daily_flags.limit_up", "daily_flags.adj_prev_close"),
        "逐档试 {0.05,0.10,0.20,0.30}，取能精确还原 limit_up 的那一档，"
        "命中零档或多档记 null；limit_up 为 null 则 null",
        ("A2.recompute.limit_pct", "A3.limit_pct_reproduces_price"),
        ("D-13",),
    ),
    _m(
        "daily_flags.is_st",
        "ST 真值，**只认 stock_st_daily**（D-11，用户 2026-09-17 定）。"
        "缺行为 null，不从名称推断、不默认为非 ST",
        ("stock_st_daily.is_st",),
        "stock_st_daily.is_st 按 (symbol, dt) 左连透传；缺行为 null",
        ("A2.recompute.is_st", "B3.null_rate"),
        ("D-15",),
    ),
    _m(
        "daily_flags.st_source",
        "该行 ST 真值的来源：有 stock_st 行为 \"stock_st\"，否则 null。"
        "覆盖率可直接从本列查出",
        ("stock_st_daily.is_st",),
        "stock_st_daily 有该行则字面量 \"stock_st\"，否则 null",
        ("A2.recompute.st_source",),
    ),
    _m(
        "valuation_snapshot.dt",
        "交易日；每日生产路径从 Tushare 日线透传",
        ("tushare_daily.dt",),
        "透传 tushare_daily.dt",
        ("B1.calendar",),
    ),
    _m(
        "valuation_snapshot.symbol",
        "标的代码；每日生产路径从 Tushare 日线透传",
        ("tushare_daily.symbol",),
        "透传 tushare_daily.symbol",
        ("B3.null_rate",),
    ),
    _m(
        "valuation_snapshot.close",
        "不复权收盘价（元）；每日生产路径从 Tushare 日线透传",
        ("tushare_daily.close",),
        "透传 tushare_daily.close",
        ("B3.null_rate",),
    ),
    _m(
        "valuation_snapshot.market_cap",
        "总市值（亿元）。2026-09-15 起直接投影 tushare_daily.market_cap，"
        "**不再用收盘价乘 QMT 股本反算**——反算会在 A+H 股上差出上亿股。"
        "实测 4,801,648 行逐行核对，与源列 /1e8 的最大差为 0",
        ("tushare_daily.market_cap",),
        "market_cap = tushare_daily.market_cap / 1e8（元→亿元）",
        ("A2.recompute.market_cap", "A3.market_cap_vs_shares"),
    ),
    _m(
        "valuation_snapshot.circulating_market_cap",
        "流通市值（亿元）。同 market_cap，直接投影不再反算",
        ("tushare_daily.circulating_market_cap",),
        "circulating_market_cap = tushare_daily.circulating_market_cap / 1e8",
        ("A2.recompute.circulating_market_cap", "A3.market_cap_vs_shares"),
    ),
    _m(
        "valuation_snapshot.turnover_ratio",
        "换手率（%）。直接透传 tushare_daily.turnover_ratio，不再由成交量"
        "与流通股本反算。实测 4,801,648 行最大差为 0",
        ("tushare_daily.turnover_ratio",),
        "turnover_ratio = tushare_daily.turnover_ratio（原样透传）",
        ("A2.recompute.turnover_ratio", "A3.turnover_identity"),
    ),
    _pool("dt", "交易日，取自 tushare_daily", "tushare_daily.dt"),
    _pool("symbol", "当日处于退市整理期的标的", "name_history.symbol"),
    _pool("name", "namechange 登记的整理期简称（如「立方退」）", "name_history.name"),
    _pool("start_date", "整理期开始日", "name_history.start_date"),
    _pool("end_date", "整理期结束日；为空表示 namechange 尚未登记结束", "name_history.end_date"),
    _pool("ann_date", "公告日；使用方按 T-1 已公告过滤，为空按已知处理", "name_history.ann_date"),
)
