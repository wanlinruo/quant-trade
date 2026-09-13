"""涨跌停与首板判定。

策略的候选池完全建立在这上面：
  涨停   close >= limit_up
  炸板   high >= limit_up 但 close < limit_up
  首板   今日涨停且昨日未涨停（连板不是首板）

派生自 tushare_daily + instruments.board，可完全重算——重算必须得到相同结果。

**全表用 polars 表达式算，不逐行 iter_rows**：全市场 4 年约 500 万行，
每行造一个 dict 会占几个 GB。

**ST 是真值，不是推断**（2026-09-12 起）：`is_st` 由 Tushare `namechange`
名称区间按股票日展开。此前的三条价格启发式（贴 5% 板 + 回看确认 +
制度硬否决）已整段删除——它的近窗一致率只有 93.8%，误差会向下游传播
并伪装成策略问题。拿不到名称就记 null，**不默认成「不是 ST」**：
默认 False 会让 ST 股按 10% 档算涨停价，首板判定直接错。

**涨跌停基准直取 `pre_close`**：Tushare 的 `pre_close` 就是除权参考价
本身（3980 个除权日实测，与因子折算值中位差 0.000154），不再用
`corporate_actions` 事件表自算。`_with_reference_price` 已删除——计划 B 作废后
它不再有调用点。`adj_prev_close` 列保留，来源改为 `pre_close` 直取。

**逐年处理**，并向前多读 `_LOOKBACK_DAYS` 天日历日只为给年初第一根 bar
补上前收。
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import structlog

from qsys.core.constraints import PriceLimitConstraint
from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.markets.ashare import PRICE_LIMITS, board_of

log = structlog.get_logger(__name__)

_DEFAULT_LIMIT = 0.10
ST_LIMIT = 0.05
# 跨年回看窗口：只为补前收——每年 1 月的第一个交易日需要上一年末的收盘价。
_LOOKBACK_DAYS = 150

_FLAG_COLUMNS = [
    "dt",
    "symbol",
    "prev_close",
    "adj_prev_close",
    "limit_up",
    "limit_down",
    "is_limit_up",
    "is_limit_down",
    "touched_limit_up",
    "is_broken_board",
    "is_first_board",
    "limit_pct",
    "is_st",
    "st_source",
]


def attach_name_history(bars: pl.DataFrame, history: pl.DataFrame) -> pl.DataFrame:
    """按名称区间给每个股票日附上 PIT 可见的名称。

    ``tushare_daily`` 本身没有 name；不能把 null 当作非 ST，也不能拿当前名称
    回填历史。这里只找当日之前最近的区间起点，并继续检查区间末端。
    """
    if history.height == 0:
        return bars
    intervals = history.filter(pl.col("start_date").is_not_null()).select(
        "symbol", "name", "start_date", "end_date"
    ).sort(["symbol", "start_date"])
    if intervals.height == 0:
        return bars
    joined = bars.drop("name").sort(["symbol", "dt"]).join_asof(
        intervals, left_on="dt", right_on="start_date", by="symbol", strategy="backward"
    )
    return joined.with_columns(
        pl.when(pl.col("end_date").is_null() | (pl.col("dt") <= pl.col("end_date")))
        .then(pl.col("name"))
        .otherwise(None)
        .alias("name")
    ).drop("start_date", "end_date")


def _assert_name_coverage(bars: pl.DataFrame) -> None:
    """拒绝大面积缺名称的生产窗口；零星历史缺口仍保持 null。"""
    if bars.height < 100:
        return
    missing = bars["name"].null_count()
    ratio = missing / bars.height
    if ratio > 0.10:
        raise RuntimeError(
            f"name_history 覆盖不足：{missing}/{bars.height} ({ratio:.1%}) 为 null；"
            "拒绝生成可能让候选池静默归零的 daily_flags"
        )


def limit_prices(prev_close: float, board: str) -> tuple[float, float]:
    """返回 (涨停价, 跌停价)，四舍五入到分。

    直接复用回测撮合所用的那一个函数——两处若分头实现，候选池筛选与
    撮合就会对同一天给出不同的涨停价。
    """
    return PriceLimitConstraint.limit_prices(
        prev_close, PRICE_LIMITS.get(board, _DEFAULT_LIMIT)
    )


def _limit_expr(cents: pl.Expr, ratio_milli: pl.Expr) -> pl.Expr:
    """整数分运算的涨/跌停价。

    `PriceLimitConstraint.limit_prices` 用 Decimal 精确乘法后四舍五入；
    价格本就是两位小数，用「分 × 千分比 + 半分再整除」可得到完全相同的
    结果（0.01~499.99 元 × 四档涨跌幅共 20 万组已逐一比对）。
    tests/data/test_flags.py 里有一条测试守着这个等价关系。
    """
    # 末尾的 round(2) 不是多余：polars 会把 `/ 100.0` 优化成 `* 0.01`，
    # 95 * 0.01 得到的是 0.9500000000000001，与 float(Decimal("0.95")) 不是同一个双精度数。
    return (((cents * ratio_milli + 500) // 1000).cast(pl.Float64) / 100.0).round(2)


def build_daily_flags(
    warehouse: Warehouse,
    start: date,
    end: date,
    boards: set[str] | None = None,
) -> int:
    """从 Tushare 日线与名称区间派生涨跌停及首板标记。返回写入行数。

    `boards` 给定时只保留该板块的标的。付费源含北交所 343 只而旧表没有，
    不过滤就会让 market_regime 的广度因子静默变化。
    """
    query = WarehouseQuery(warehouse)
    instruments = query.instruments()
    name_history = query.name_history()
    board_map = (
        dict(zip(instruments["symbol"].to_list(), instruments["board"].to_list()))
        if instruments.height
        else {}
    )

    written = 0
    for year in range(start.year, end.year + 1):
        lo_year, hi_year = date(year, 1, 1), date(year, 12, 31)
        lo = max(start, lo_year)
        hi = min(end, hi_year)
        if lo > hi:
            continue

        # 取 28 列：name 给 ST 真值，pre_close 给涨跌停基准。
        bars = query.tushare_daily(None, lo - timedelta(days=_LOOKBACK_DAYS), hi)
        if bars.height == 0:
            continue
        bars = attach_name_history(bars, name_history)
        _assert_name_coverage(bars)
        if boards is not None:
            def _board(s: str) -> str:
                # 显式判 None，不用 `or`——board 若曾经取值空字符串会被
                # `or` 误当成「未匹配」而走号段兜底，虽然当前
                # instruments.board 不会是空串，但判断本身应该准确。
                found = board_map.get(s)
                return found if found is not None else board_of(s.split(".")[0])

            keep = pl.col("symbol").map_elements(
                _board, return_dtype=pl.Utf8
            ).is_in(list(boards))
            bars = bars.filter(keep)
            if bars.height == 0:
                continue

        flags = _compute(bars, board_map).filter(pl.col("dt").is_between(lo, hi))
        if flags.height == 0:
            continue

        chunk = flags.sort(["dt", "symbol"]).select(_FLAG_COLUMNS)
        # 分区是整年覆盖写的，只重算一段区间时直接写会把同年其余日期抹掉，
        # 所以要保留窗口**之外**的既有行。
        #
        # 窗口**之内**则完全以本次重算为准，包括它的缺席：源不再产出的
        # 股-日必须跟着消失。早先这里是「并集 + keep=last」，只做增与覆盖、
        # 从不删除，生产上因此留下 10 行残骸——旧表 bars_daily 有、付费源
        # 没有的股-日，重算后仍带着旧的 limit_pct，而 is_st/st_source 是
        # 新 schema 的 null。一行里一半是旧真值源的断言、一半是新真值源的
        # 空缺，等于在断言当前真值源支撑不了的事实。
        existing = query.daily_flags(lo_year, hi_year)
        outside = existing.filter(~pl.col("dt").is_between(lo, hi))
        merged = (
            pl.concat([outside, chunk], how="vertical")
            .unique(subset=["dt", "symbol"], keep="last", maintain_order=True)
            .sort(["dt", "symbol"])
            .select(_FLAG_COLUMNS)
        )
        warehouse.write(
            "daily_flags", merged.to_arrow().cast(schema.DAILY_FLAGS), year=year
        )
        written += chunk.height

    log.info("daily_flags.built", rows=written, start=str(start), end=str(end))
    return written


def _compute(bars: pl.DataFrame, boards: dict[str, str]) -> pl.DataFrame:
    limits = {board: PRICE_LIMITS.get(board, _DEFAULT_LIMIT) for board in PRICE_LIMITS}
    # 局部名不能叫 board_of——模块里已经从 markets.ashare 导入了同名函数。
    #
    # **default 必须按号段兜底，不能是 "main"**：instruments 缺了的标的
    # 会拿到 main 的 10% 上限。旧表不含北交所所以从没触发过，付费源有
    # 343 只北交所（实际上限 30%），照旧默认会把它们的涨停价全算错，
    # 而且不报错。
    board_expr = pl.col("symbol").replace_strict(
        boards,
        default=pl.col("symbol").map_elements(
            lambda s: board_of(s.split(".")[0]), return_dtype=pl.Utf8
        ),
        return_dtype=pl.Utf8,
    )
    ratio = board_expr.replace_strict(limits, default=_DEFAULT_LIMIT, return_dtype=pl.Float64)

    # ST 真值：名称含 "ST" 即为 ST（含 *ST）。名称为 null 时 is_st 也为 null,
    # 不默认成 False——见模块 docstring。
    is_st = (
        pl.when(pl.col("name").is_null())
        .then(None)
        .otherwise(pl.col("name").str.contains("ST"))
    )

    frame = bars.sort(["symbol", "dt"]).with_columns(
        pl.col("close").shift(1).over("symbol").alias("prev_close"),
        # pre_close 直取即除权参考价，非除权日等于前收。
        pl.col("pre_close").alias("adj_prev_close"),
        ratio.alias("_ratio"),
        is_st.alias("is_st"),
        pl.lit("tushare", dtype=pl.Utf8).alias("st_source"),
    )

    frame = frame.with_columns(
        pl.when(pl.col("is_st"))
        .then(pl.lit(ST_LIMIT))
        .otherwise(pl.when(pl.col("is_st").is_null()).then(None).otherwise(pl.col("_ratio")))
        .alias("limit_pct")
    )

    cents = (pl.col("adj_prev_close") * 100).round(0).cast(pl.Int64)
    up_milli = ((1.0 + pl.col("limit_pct")) * 1000).round(0).cast(pl.Int64)
    down_milli = ((1.0 - pl.col("limit_pct")) * 1000).round(0).cast(pl.Int64)

    frame = frame.with_columns(
        _limit_expr(cents, up_milli).alias("limit_up"),
        _limit_expr(cents, down_milli).alias("limit_down"),
    )

    is_up = (pl.col("close") >= pl.col("limit_up")).fill_null(False)
    is_down = (pl.col("close") <= pl.col("limit_down")).fill_null(False)
    touched = (pl.col("high") >= pl.col("limit_up")).fill_null(False)
    frame = frame.with_columns(
        is_up.alias("is_limit_up"),
        is_down.alias("is_limit_down"),
        touched.alias("touched_limit_up"),
    )
    return frame.with_columns(
        (pl.col("touched_limit_up") & ~pl.col("is_limit_up")).alias("is_broken_board"),
        (
            pl.col("is_limit_up")
            & ~pl.col("is_limit_up").shift(1, fill_value=False).over("symbol")
        ).alias("is_first_board"),
    )
