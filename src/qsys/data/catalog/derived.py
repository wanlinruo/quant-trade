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
        "除权除息参考价（元）；涨跌停按它计算。2026-09-12 起直取当日 pre_close",
        ("tushare_daily.pre_close",),
        "透传 tushare_daily.pre_close",
        ("A2.recompute.adj_prev_close",),
    ),
    _m(
        "daily_flags.limit_up",
        "涨停价（元），按整数分精确计算并四舍五入到分",
        ("daily_flags.adj_prev_close", "daily_flags.limit_pct"),
        "cents=round(adj_prev_close*100)；"
        "limit_up=round(((cents*round((1+limit_pct)*1000)+500)//1000)/100, 2)",
        ("A2.recompute.limit_up", "A3.limit_price_vs_selfcalc"),
    ),
    _m(
        "daily_flags.limit_down",
        "跌停价（元），按整数分精确计算并四舍五入到分",
        ("daily_flags.adj_prev_close", "daily_flags.limit_pct"),
        "cents=round(adj_prev_close*100)；"
        "limit_down=round(((cents*round((1-limit_pct)*1000)+500)//1000)/100, 2)",
        ("A2.recompute.limit_down", "A3.limit_price_vs_selfcalc"),
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
        "当日实际涨跌幅上限：ST 为 5%，否则按板块取 10%/20%/30%",
        ("daily_flags.is_st", "instruments.board", "daily_flags.symbol"),
        "is_st=True ? 0.05；is_st=null ? null；否则按 instruments.board 取"
        " main=0.10/gem=0.20/star=0.20/bse=0.30；标的未入 instruments 时"
        "按 symbol 号段调用 board_of 后取同一张表",
        ("A2.recompute.limit_pct",),
    ),
    _m(
        "daily_flags.is_st",
        "ST 真值；按当日名称是否包含 ST 判定，名称缺失时为 null",
        _NAME_SOURCES,
        "name=coalesce(tushare_daily.name, paid_daily.name[精确日], "
        "name_snapshots.name[精确日], name_history.name[区间覆盖])；"
        "name 为 null 则 null，否则 name.contains('ST')",
        ("A2.recompute.is_st", "B3.null_rate"),
        ("D-10",),
    ),
    _m(
        "daily_flags.st_source",
        "该行 ST 真值实际命中的名称来源；名称为 null 时来源也为 null",
        _NAME_SOURCES,
        "依四级名称优先级记录首个非 null 候选的来源标签；全部缺失则 null",
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
        "总市值（亿元）；股本取生效日不晚于交易日的最近一条记录",
        ("tushare_daily.close", "share_capital.total"),
        "market_cap=close*asof(share_capital.total, by=symbol, dt<=交易日)/1e8；"
        "无有效股本或股本<=0 时不产出该行",
        ("A2.recompute.market_cap", "A3.market_cap_vs_shares"),
    ),
    _m(
        "valuation_snapshot.circulating_market_cap",
        "流通市值（亿元）；股本取生效日不晚于交易日的最近一条记录",
        ("tushare_daily.close", "share_capital.circulating"),
        "circulating_market_cap=close*asof(share_capital.circulating, "
        "by=symbol, dt<=交易日)/1e8；无有效股本或股本<=0 时不产出该行",
        ("A2.recompute.circulating_market_cap", "A3.market_cap_vs_shares"),
    ),
    _m(
        "valuation_snapshot.turnover_ratio",
        "换手率（%）；日线成交量单位为手，先乘 100 换算成股",
        ("tushare_daily.volume", "share_capital.circulating"),
        "turnover_ratio=volume(手)*100/"
        "asof(share_capital.circulating, by=symbol, dt<=交易日)*100%",
        ("A2.recompute.turnover_ratio", "A3.turnover_identity"),
    ),
)
