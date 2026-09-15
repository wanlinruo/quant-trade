"""派生类指标 20 条。

每条 ``formula`` 是 A2「独立重算」的规格书：规则实现必须另写计算路径，
不能调用这里描述的生产函数。血缘以每日生产调度实际调用的路径为准；
``valuation_snapshot`` 另有 Akshare 当日快照入口，但它不是每日同步的生产路径。
"""

from __future__ import annotations

from qsys.data.catalog.types import Computed, Kind, Metric, MetricStatus, Validity

DATASETS = frozenset({"daily_flags", "valuation_snapshot"})


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


_NAME_SOURCES = (
    "tushare_daily.name",
    "paid_daily.name",
    "name_snapshots.name",
    "name_history.name",
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
        "涨停价（元）。**优先直取 stk_limit 的交易所公布值**；未回填的历史行"
        "才按兜底档位自算并四舍五入到分。自算那条路已知会错：兜底档位来自"
        "「ST 一律 5%」，而创业板/科创板 ST 实为 20%、2026-07-06 起主板 ST "
        "实为 10%",
        ("price_limits_daily.limit_up", "daily_flags.adj_prev_close",
         "daily_flags.is_st", "instruments.board"),
        "coalesce(price_limits_daily.limit_up, 自算)；自算 = "
        "cents=round(adj_prev_close*100)，"
        "round(((cents*round((1+兜底档位)*1000)+500)//1000)/100, 2)",
        ("A2.recompute.limit_up", "A3.limit_price_vs_selfcalc",
         "A3.limit_pct_reproduces_price"),
    ),
    _m(
        "daily_flags.limit_down",
        "跌停价（元）。与 limit_up 同源同法：优先直取，未回填才自算",
        ("price_limits_daily.limit_down", "daily_flags.adj_prev_close",
         "daily_flags.is_st", "instruments.board"),
        "coalesce(price_limits_daily.limit_down, 自算)；自算 = "
        "cents=round(adj_prev_close*100)，"
        "round(((cents*round((1-兜底档位)*1000)+500)//1000)/100, 2)",
        ("A2.recompute.limit_down", "A3.limit_price_vs_selfcalc",
         "A3.limit_pct_reproduces_price"),
    ),
    _m(
        "daily_flags.is_limit_up",
        "收盘封住涨停；比较所需值缺失时生产实现记 False",
        ("tushare_daily.close", "daily_flags.limit_up"),
        "(close >= limit_up).fill_null(False)",
        ("A2.recompute.is_limit_up", "A3.limit_list_vs_selfcalc"),
    ),
    _m(
        "daily_flags.is_limit_down",
        "收盘封住跌停；比较所需值缺失时生产实现记 False",
        ("tushare_daily.close", "daily_flags.limit_down"),
        "(close <= limit_down).fill_null(False)",
        ("A2.recompute.is_limit_down",),
    ),
    _m(
        "daily_flags.touched_limit_up",
        "盘中触及涨停（含封住）；比较所需值缺失时生产实现记 False",
        ("tushare_daily.high", "daily_flags.limit_up"),
        "(high >= limit_up).fill_null(False)",
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
        "每只标的没有昨日观测时 shift(fill_value=False)",
        ("A2.recompute.is_first_board", "A3.limit_list_vs_selfcalc"),
    ),
    _m(
        "daily_flags.limit_pct",
        "该行 limit_up 实际对应的那一档涨跌幅，**由直取涨停价解码得出**，"
        "与同一行的 limit_up / limit_down 恒自洽；解不出记 null（新股上市"
        "初期无涨跌幅限制时 stk_limit 返回 up_limit=1,000,000，实测 2,283 行）。"
        "**不能用它判 ST**：2026-07-06 起主板 ST 与普通股同为 0.10，"
        "创业板/科创板 ST 一直是 0.20（D-13/D-14）",
        ("daily_flags.limit_up", "daily_flags.adj_prev_close",
         "daily_flags.is_st", "instruments.board"),
        "有直取价：逐档试 {0.05,0.10,0.20,0.30}，取能精确还原 limit_up 的"
        "那一档，命中零档或多档记 null；无直取价：沿用自算时所用的兜底档位"
        "（is_st=True ? 0.05；is_st=null ? null；否则按板块取）",
        ("A2.recompute.limit_pct", "A3.limit_pct_reproduces_price"),
        ("D-13",),
    ),
    _m(
        "daily_flags.is_st",
        "ST 真值，**直取 stock_st_daily**。2026-09-15 起生产区间全量回填，"
        "st_source 全窗口为 stock_st。仅当整窗口 ST 键覆盖不完整时才退回"
        "名称链路——部分覆盖绝不与旧源逐行拼接",
        ("stock_st_daily.is_st",) + _NAME_SOURCES,
        "整窗口 stock_st 覆盖完整则 is_st = stock_st_daily.is_st；否则退回"
        "name=coalesce(tushare_daily.name, paid_daily.name[精确日], "
        "name_snapshots.name[精确日], name_history.name[区间覆盖])，"
        "name 为 null 则 null，否则 name.contains('ST')",
        ("A2.recompute.is_st", "B3.null_rate"),
        ("D-10", "D-15"),
    ),
    _m(
        "daily_flags.st_source",
        "该行 ST 真值的来源。直取路径恒为 \"stock_st\"；迁移期的名称链路"
        "记下实际命中的那一级，名称全缺则 null",
        ("stock_st_daily.is_st",) + _NAME_SOURCES,
        "直取路径写字面量 \"stock_st\"；名称路径依四级优先级记录首个非 null "
        "候选的来源标签，全部缺失则 null",
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
)
