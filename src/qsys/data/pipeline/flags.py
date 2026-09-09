"""涨跌停与首板判定。

策略的候选池完全建立在这上面：
  涨停   close >= limit_up
  炸板   high >= limit_up 但 close < limit_up
  首板   今日涨停且昨日未涨停（连板不是首板）

派生自 bars_daily + instruments.board，可完全重算——重算必须得到相同结果。

**全表用 polars 表达式算，不逐行 iter_rows**：全市场 4 年约 500 万行，
每行造一个 dict 会占几个 GB，Linux 核心节点只有 870 MB 可用。

**逐年处理**，并向前多读 `_LOOKBACK_DAYS` 天日历日只为给年初第一根 bar
补上前收——否则每年 1 月的第一个交易日会因缺前收而被判成「无涨跌停」。

**已知缺口**：ST 股涨跌幅为 5%，但一期无 ST 历史数据，此处一律按板块判定，
ST 股的涨停会被漏判。补 ST 历史后需重算本表。
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import structlog

from qsys.core.constraints import PriceLimitConstraint
from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.markets.ashare import PRICE_LIMITS

log = structlog.get_logger(__name__)

_DEFAULT_LIMIT = 0.10
# 跨年补前收的回看窗口。元旦假期远短于此；停牌超过这么久的标的
# 复牌首日会缺前收，标记全 False。
_LOOKBACK_DAYS = 30

_FLAG_COLUMNS = [
    "dt",
    "symbol",
    "prev_close",
    "limit_up",
    "limit_down",
    "is_limit_up",
    "is_limit_down",
    "touched_limit_up",
    "is_broken_board",
    "is_first_board",
]


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


def build_daily_flags(warehouse: Warehouse, start: date, end: date) -> int:
    """从日线派生涨跌停与首板标记。返回写入行数。"""
    query = WarehouseQuery(warehouse)
    instruments = query.instruments()
    boards = (
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

        bars = query.daily_bars(None, lo - timedelta(days=_LOOKBACK_DAYS), hi)
        if bars.height == 0:
            continue

        flags = _compute(bars, boards).filter(pl.col("dt").is_between(lo, hi))
        if flags.height == 0:
            continue

        chunk = flags.sort(["dt", "symbol"]).select(_FLAG_COLUMNS)
        # 分区是整年覆盖写的，必须与同年既有数据合并：只重算一段区间时
        # 直接写会把同年其余日期的标记抹掉。新行在后，keep="last" 让重算
        # 覆盖旧值。
        merged = (
            pl.concat([query.daily_flags(lo_year, hi_year), chunk], how="vertical")
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
    board_of = pl.col("symbol").replace_strict(
        boards, default="main", return_dtype=pl.Utf8
    )
    ratio = board_of.replace_strict(limits, default=_DEFAULT_LIMIT, return_dtype=pl.Float64)

    frame = bars.sort(["symbol", "dt"]).with_columns(
        pl.col("close").shift(1).over("symbol").alias("prev_close"),
        ratio.alias("_ratio"),
    )

    cents = (pl.col("prev_close") * 100).round(0).cast(pl.Int64)
    up_milli = ((1.0 + pl.col("_ratio")) * 1000).round(0).cast(pl.Int64)
    down_milli = ((1.0 - pl.col("_ratio")) * 1000).round(0).cast(pl.Int64)

    frame = frame.with_columns(
        _limit_expr(cents, up_milli).alias("limit_up"),
        _limit_expr(cents, down_milli).alias("limit_down"),
    )

    # 价格与分位涨跌停价都已取整到分，直接比较即可，无需容差
    is_up = (pl.col("close") >= pl.col("limit_up")).fill_null(False)
    touched = (pl.col("high") >= pl.col("limit_up")).fill_null(False)

    frame = frame.with_columns(
        is_up.alias("is_limit_up"),
        (pl.col("close") <= pl.col("limit_down")).fill_null(False).alias("is_limit_down"),
        touched.alias("touched_limit_up"),
        (touched & ~is_up).alias("is_broken_board"),
    )

    return frame.with_columns(
        (
            pl.col("is_limit_up")
            & ~pl.col("is_limit_up").shift(1, fill_value=False).over("symbol")
        ).alias("is_first_board")
    )
