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

**ST 由价格行为反推**：ST 股涨跌幅是 5%，而免费源没有历史 ST 名单
（QMT 没有 ST 板块，`get_instrument_detail` 只给当日涨跌停价；akshare 的
东财接口在拒，新浪的曾用名接口不带日期）。

判定用两条，缺一不可：

1. 当日 `high` 恰好等于 5% 涨停价 `round(前收 x 1.05, 2)`，精确到分；
2. **该股回看 60 根内被第 1 条命中过至少 2 次**。

第 2 条是关键。只用第 1 条会大量误判：一只 ¥10 的股当日高点落在 +5% 那
一分上的概率约 1/10，全市场每天撞出十几个纯属巧合。而 ST 是持续数月的
状态，真 ST 会反复贴着 5% 板，巧合只命中一次。

**标定依据**（聚宽导出的 792 天 `regime_daily` 真值，见
`docs/reference/joinquant-alignment.md`）：

| 规则 | 触板数平均绝对误差 | Gate 全区间一致 | 近窗一致 | 近窗多放行 |
|---|---|---|---|---|
| 不判 ST | 19.1 | 83.3% | 77.9% | 0（多拒 53） |
| 只用第 1 条 | 11.2 | 90.7% | 83.3% | **40** |
| **加第 2 条（窗 60）** | **4.4** | **96.3%** | **93.8%** | **12** |

只用第 1 条时近窗口 40 天多放行、0 天多拒掉——**单边偏差**，正是移植版
比聚宽多选的直接来源。

窗口取 40 / 60 / 90 / 120 的一致率分别是 96.1% / 96.3% / 96.7% / 96.7%，
**都在噪声内，别再为小数点调它**。取 60 是因为它在两个区间上的中位偏差
都接近 0，且与策略里已有的 60 根形态窗口同一量级。

（早先曾用「回看 20 根涨幅从未超过 5%」做佐证并以 57 个样本否决。真值多
了 14 倍后重测，那条确实不如「命中 >=2 次」：|差|均 8.9 对 4.4。）

`limit_pct` 列记下每个股-日实际生效的涨跌幅（0.05 / 0.10 / 0.20），可回溯。

**涨跌停按除权除息参考价算，不是按前收盘价。** 除权日交易所用

    参考价 = (前收 - 每股现金红利 + 配股价 x 每股配股数) / (1 + 每股送转 + 每股配股)

例：000158.SZ 2026-06-09 前收 13.94、每股派 0.027，参考价 13.913，
涨停价 15.30 而非 13.94 x 1.1 = 15.33。当天它收在 15.30，是涨停。

漏了这一步的后果是**单向**的：参考价永远不高于前收（现金分红与送转都只会
压低它），算出的涨停价因此偏高，涨停只会漏判不会误判。全区间实测 144 个
股-日漏判涨停、217 个漏判触板，波及 106 个交易日——而候选池就是
「昨日首板涨停股」，漏一个就少一个候选。

`prev_close` 仍存**原始**前收（`market_regime` 的日收益用它再乘复权因子），
参考价单独存 `adj_prev_close`，两者在非除权日相等。
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
ST_LIMIT = 0.05
# ST 反推的第 2 条：回看多少根、至少命中几次
ST_CONFIRM_BARS = 60
ST_CONFIRM_HITS = 2
# 跨年回看窗口：既为补前收，也为 ST 判定的滚动计数。
# ST 判定要回看 60 根 K 线，约 90 个日历日；再留出假期余量。
# 取 30 的话每年 1 月前 60 根的回看窗口是残缺的，ST 会被系统性漏判。
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


_ACTION_COLUMNS = (
    "cash_per_share", "stock_per_share", "rights_per_share", "rights_price",
)


def _with_reference_price(
    frame: pl.DataFrame, actions: pl.DataFrame | None
) -> pl.DataFrame:
    """补上 `adj_prev_close` 列：除权除息参考价。

    交易所公式：(前收 - 每股现金红利 + 配股价 x 每股配股数)
                / (1 + 每股送转 + 每股配股)

    非除权日 join 不上，四项全为 0，公式化为前收本身。
    没有公司行动数据时整列退化为前收——早期仓库可能还没采过分红。
    """
    if actions is None or actions.height == 0:
        return frame.with_columns(pl.col("prev_close").alias("adj_prev_close"))

    ex = (
        actions.rename({"ex_date": "dt"})
        .select(["symbol", "dt", *_ACTION_COLUMNS])
        # 同一天同一只有多行时取合计——分次派息在部分数据源里会拆开
        .group_by(["symbol", "dt"])
        .agg([pl.col(c).fill_null(0.0).sum() for c in _ACTION_COLUMNS])
    )
    cash, stock, rights, rprice = (
        pl.col(c).fill_null(0.0) for c in _ACTION_COLUMNS
    )
    return (
        frame.join(ex, on=["symbol", "dt"], how="left")
        .with_columns(
            ((pl.col("prev_close") - cash + rprice * rights) / (1.0 + stock + rights))
            .alias("adj_prev_close")
        )
        .drop(_ACTION_COLUMNS)
    )


def build_daily_flags(warehouse: Warehouse, start: date, end: date) -> int:
    """从日线派生涨跌停与首板标记。返回写入行数。"""
    query = WarehouseQuery(warehouse)
    instruments = query.instruments()
    boards = (
        dict(zip(instruments["symbol"].to_list(), instruments["board"].to_list()))
        if instruments.height
        else {}
    )

    # 涨跌停按除权参考价算，故必须带上公司行动。全表一次读入：
    # 全市场四年约 1.7 万条，比逐年过滤便宜。
    actions = query.corporate_actions()

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

        flags = _compute(bars, boards, actions).filter(
            pl.col("dt").is_between(lo, hi)
        )
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


def _compute(
    bars: pl.DataFrame, boards: dict[str, str], actions: pl.DataFrame | None = None
) -> pl.DataFrame:
    limits = {board: PRICE_LIMITS.get(board, _DEFAULT_LIMIT) for board in PRICE_LIMITS}
    board_of = pl.col("symbol").replace_strict(
        boards, default="main", return_dtype=pl.Utf8
    )
    ratio = board_of.replace_strict(limits, default=_DEFAULT_LIMIT, return_dtype=pl.Float64)

    frame = bars.sort(["symbol", "dt"]).with_columns(
        pl.col("close").shift(1).over("symbol").alias("prev_close"),
        ratio.alias("_ratio"),
    )
    frame = _with_reference_price(frame, actions)

    # 涨跌停按除权参考价算。非除权日它等于 prev_close，故这里无需分支。
    cents = (pl.col("adj_prev_close") * 100).round(0).cast(pl.Int64)
    up_milli = ((1.0 + pl.col("_ratio")) * 1000).round(0).cast(pl.Int64)
    down_milli = ((1.0 - pl.col("_ratio")) * 1000).round(0).cast(pl.Int64)

    st_up_milli = pl.lit(int(round((1.0 + ST_LIMIT) * 1000)), dtype=pl.Int64)
    st_down_milli = pl.lit(int(round((1.0 - ST_LIMIT) * 1000)), dtype=pl.Int64)

    frame = frame.with_columns(
        _limit_expr(cents, up_milli).alias("_board_up"),
        _limit_expr(cents, down_milli).alias("_board_down"),
        _limit_expr(cents, st_up_milli).alias("_st_up"),
        _limit_expr(cents, st_down_milli).alias("_st_down"),
    )

    # ST 反推第 1 条：当日最高价恰好等于 5% 涨停价（精确到分）。
    hit_st = (pl.col("high") == pl.col("_st_up")).fill_null(False)
    # 第 2 条：该股回看 ST_CONFIRM_BARS 根内命中过 >= ST_CONFIRM_HITS 次。
    # 只回看不前瞻——rolling_sum 是尾窗，含当根。ST 持续数月，真 ST 会反复
    # 贴板；单次命中多半是巧合（详见模块 docstring 的标定表）。
    confirmed = (
        hit_st.cast(pl.Int32)
        .rolling_sum(window_size=ST_CONFIRM_BARS, min_samples=1)
        .over("symbol")
        >= ST_CONFIRM_HITS
    ).fill_null(False)
    looks_st = hit_st & confirmed
    frame = frame.with_columns(
        pl.when(looks_st).then(pl.lit(ST_LIMIT)).otherwise(pl.col("_ratio"))
        .alias("limit_pct"),
        pl.when(looks_st).then(pl.col("_st_up")).otherwise(pl.col("_board_up"))
        .alias("limit_up"),
        pl.when(looks_st).then(pl.col("_st_down")).otherwise(pl.col("_board_down"))
        .alias("limit_down"),
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
