"""涨跌停与首板判定。

策略的候选池完全建立在这上面：
  涨停   close >= limit_up
  炸板   high >= limit_up 但 close < limit_up
  首板   今日涨停且昨日未涨停（连板不是首板）

派生自 tushare_daily + instruments.board，可完全重算——重算必须得到相同结果。

**全表用 polars 表达式算，不逐行 iter_rows**：全市场 4 年约 500 万行，
每行造一个 dict 会占几个 GB。

**ST 只认 Tushare `stock_st`**（D-11，用户 2026-09-17 定）：逐日布尔真值
按 (symbol, dt) 精确连接，缺哪行那行的 `is_st` / `st_source` 就是 null，
**不从名称推断、不默认成「不是 ST」**。早先的价格启发式与名称兜底链路
都已删除。

**涨跌停价只认 Tushare `stk_limit`**：没有直取价的行，涨跌停价、档位与
涨停/炸板/首板标记全部为 null，不按板块档位或 ST 档位自算。

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

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.markets.ashare import board_of

log = structlog.get_logger(__name__)

# 已知的日涨跌幅档位，用于从涨停价反解实际生效的那一档。
_KNOWN_TIERS = (0.05, 0.10, 0.20, 0.30)
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


def _decode_tier(cents: pl.Expr, limit_up: pl.Expr) -> pl.Expr:
    """从直取涨停价反解出当日实际生效的涨跌幅档位。

    涨停价是 `(前收, 档位)` 的量化函数，档位取值稀疏（5/10/20/30%），
    所以可以逐档试算再比对——**这是可验证的解码，不是猜**：只有精确
    还原出 `limit_up` 的那一档才算数。

    解不出（无涨跌幅限制的新股首日等）或解出多档（一元以下的低价股上
    5% 与 10% 会round到同一分）一律记 null。宁可没有，不可编造。

    **不要拿它判 ST。** 2026-07-06 起主板 ST 与普通股同为 10%，档位已经
    区分不出 ST；ST 只认 `stock_st` 直取名单。
    """
    hits = [
        (tier, _limit_expr(cents, pl.lit(round((1.0 + tier) * 1000))) == limit_up)
        for tier in _KNOWN_TIERS
    ]
    matched = sum(
        (cond.fill_null(False).cast(pl.Int32) for _, cond in hits),
        pl.lit(0, dtype=pl.Int32),
    )
    decoded = pl.lit(None, dtype=pl.Float64)
    for tier, cond in reversed(hits):
        decoded = pl.when(cond.fill_null(False)).then(pl.lit(tier)).otherwise(decoded)
    return pl.when(matched == 1).then(decoded).otherwise(None)


def build_daily_flags(
    warehouse: Warehouse,
    start: date,
    end: date,
    boards: set[str] | None = None,
) -> int:
    """从 Tushare 日线、`stk_limit` 与 `stock_st` 派生涨跌停及首板标记。返回写入行数。

    `boards` 给定时只保留该板块的标的。付费源含北交所 343 只而旧表没有，
    不过滤就会让 market_regime 的广度因子静默变化。
    """
    query = WarehouseQuery(warehouse)
    instruments = query.instruments()
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

        # ST 与涨跌停价只取各自的直取事实表；日线只提供价格与 pre_close。
        bars = query.tushare_daily(None, lo - timedelta(days=_LOOKBACK_DAYS), hi)
        if bars.height == 0:
            continue
        back = lo - timedelta(days=_LOOKBACK_DAYS)
        st_truth = query.stock_st(None, back, hi).select(
            "symbol", "dt", pl.col("is_st").alias("_direct_is_st")
        )
        bars = bars.join(st_truth, on=["symbol", "dt"], how="left").with_columns(
            pl.col("_direct_is_st").alias("is_st"),
            pl.when(pl.col("_direct_is_st").is_not_null())
            .then(pl.lit("stock_st")).otherwise(None).alias("st_source"),
        ).drop("_direct_is_st")
        direct_limits = query.price_limits(None, back, hi).select(
            "symbol", "dt",
            pl.col("pre_close").alias("_direct_pre_close"),
            pl.col("limit_up").alias("_direct_limit_up"),
            pl.col("limit_down").alias("_direct_limit_down"),
        )
        bars = bars.join(direct_limits, on=["symbol", "dt"], how="left")
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

        flags = _compute(bars).filter(pl.col("dt").is_between(lo, hi))
        if flags.height == 0:
            continue
        # 缺直取值不填，但要让人看见：覆盖率可直接从 daily_flags 查出
        for column, event in (("is_st", "daily_flags.stock_st_missing"),
                              ("limit_up", "daily_flags.stk_limit_missing")):
            missing = flags[column].null_count()
            if missing:
                log.warning(event, missing_rows=missing, total_rows=flags.height,
                            start=str(lo), end=str(hi))

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


def _compute(bars: pl.DataFrame) -> pl.DataFrame:
    cents = (pl.col("adj_prev_close") * 100).round(0).cast(pl.Int64)
    frame = bars.sort(["symbol", "dt"]).with_columns(
        pl.col("close").shift(1).over("symbol").alias("prev_close"),
        # pre_close 直取即除权参考价，非除权日等于前收。
        pl.coalesce("_direct_pre_close", "pre_close").alias("adj_prev_close"),
        pl.col("_direct_limit_up").alias("limit_up"),
        pl.col("_direct_limit_down").alias("limit_down"),
    )
    # limit_pct 是**这一行的 limit_up 实际对应的那一档**，由直取价解码，两者恒自洽。
    frame = frame.with_columns(
        _decode_tier(cents, pl.col("limit_up")).alias("limit_pct"),
        (pl.col("close") >= pl.col("limit_up")).alias("is_limit_up"),
        (pl.col("close") <= pl.col("limit_down")).alias("is_limit_down"),
        (pl.col("high") >= pl.col("limit_up")).alias("touched_limit_up"),
    )
    # 没有直取涨跌停价的行，上面三个标记自然为 null——不知道就是不知道。
    return frame.with_columns(
        (pl.col("touched_limit_up") & ~pl.col("is_limit_up")).alias("is_broken_board"),
        (
            pl.col("is_limit_up")
            & ~pl.col("is_limit_up").shift(1, fill_value=False).over("symbol")
        ).alias("is_first_board"),
    )
