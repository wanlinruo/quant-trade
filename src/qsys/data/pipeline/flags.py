"""涨跌停与首板判定。

策略的候选池完全建立在这上面：
  涨停   close >= limit_up
  炸板   high >= limit_up 但 close < limit_up
  首板   今日涨停且昨日未涨停（连板不是首板）

派生自 tushare_daily + instruments.board，可完全重算——重算必须得到相同结果。

**全表用 polars 表达式算，不逐行 iter_rows**：全市场 4 年约 500 万行，
每行造一个 dict 会占几个 GB。

**ST 是真值，不是推断**：优先使用 Tushare `stock_st` 的完整每日布尔表；
迁移期仅在该窗口尚未完整回填时使用旧名称链路。此前的三条价格启发式（贴 5% 板 + 回看确认 +
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
# **只在没有直取涨跌停价时用来算价格的兜底档位，不是 limit_pct 的答案。**
# 2026-07-06 起主板 ST 的上限由 5% 改为 10%（实测：07-03 全市场主板 ST
# 仍是 5%，07-06 起全部 10%，一刀切无过渡），任何写死「ST 一律 5%」的
# 规则从那天起就是错的。真实档位只能从直取涨停价解码，见 `_decode_tier`。
ST_LIMIT = 0.05
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


def resolve_names(
    bars: pl.DataFrame,
    paid: pl.DataFrame,
    history: pl.DataFrame,
    snapshots: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """解析每个股票日 PIT 可见的名称，并记下它来自哪个源。

    三个候选**都是直取字段**，没有任何推断；优先级按「观测强度」排：

    1. ``tushare_daily.name``——同一接口的当日字段（生产中恒为 null，
       `compose_daily` 把 name 留给 namechange 单独成表）；
    2. ``paid_daily.name``——**逐日观测到的名称**，覆盖 1990 至 2026-09-11；
    3. ``name_snapshots.name``——每日 ``stock_basic`` 快照，**只对取快照那一天
       成立**，覆盖付费源够不到的增量日；
    4. ``namechange`` 区间——交易所公告的改名事件。

    **为什么逐日观测要排在区间前面**：`namechange` 只记录变更事件，不是逐日
    快照。2006 年股改 G 股摘牌之后，名称恢复原状的那次变更没有进
    `namechange`，于是大量标的的最后一个区间停在 2006-10-08、`end_date`
    非 null 且无后继，此后全部没有区间覆盖。实测单日 96 只、2023-2026 全区间
    114 只因此没有名称，顺着 is_st → limit_pct → limit_up → fill_null(False)
    一路下去静默丢掉 867 个首板。详见
    `artifacts/第4段回归_付费源到Tushare换源_2026-09-14.md` 与 D-10。

    三个都没有就记 **null**，绝不拿当前名称回填历史——`instruments.name`
    是快照，用它填历史就是结转。
    """
    resolved = bars
    if "name" not in resolved.columns:
        resolved = resolved.with_columns(pl.lit(None, dtype=pl.Utf8).alias("name"))
    resolved = resolved.with_columns(
        pl.when(pl.col("name").is_not_null())
        .then(pl.lit("tushare_daily"))
        .otherwise(None)
        .alias("name_source")
    )

    # 两个逐日观测源都按 (symbol, dt) 精确连接，不是 asof——只对它实际有行的
    # 那一天生效，向前后蔓延就成了结转。
    for frame, label in ((paid, "paid_daily"), (snapshots, "name_snapshot")):
        if frame is None or frame.height == 0:
            continue
        resolved = resolved.join(
            frame.select("symbol", "dt", pl.col("name").alias("_other")),
            on=["symbol", "dt"],
            how="left",
        ).with_columns(
            pl.when(pl.col("name").is_null() & pl.col("_other").is_not_null())
            .then(pl.lit(label))
            .otherwise(pl.col("name_source"))
            .alias("name_source"),
            pl.coalesce("name", "_other").alias("name"),
        ).drop("_other")

    intervals = history.filter(pl.col("start_date").is_not_null()).select(
        "symbol", pl.col("name").alias("_hist_name"), "start_date", "end_date"
    ).sort(["symbol", "start_date"]) if history.height else history
    if history.height and intervals.height:
        resolved = resolved.sort(["symbol", "dt"]).join_asof(
            intervals,
            left_on="dt",
            right_on="start_date",
            by="symbol",
            strategy="backward",
        )
        in_range = pl.col("_hist_name").is_not_null() & (
            pl.col("end_date").is_null() | (pl.col("dt") <= pl.col("end_date"))
        )
        resolved = resolved.with_columns(
            pl.when(pl.col("name").is_null() & in_range)
            .then(pl.lit("namechange"))
            .otherwise(pl.col("name_source"))
            .alias("name_source"),
            pl.when(pl.col("name").is_null() & in_range)
            .then(pl.col("_hist_name"))
            .otherwise(pl.col("name"))
            .alias("name"),
        ).drop("_hist_name", "start_date", "end_date")

    return resolved


def _assert_name_coverage(bars: pl.DataFrame) -> None:
    """拒绝大面积缺名称的生产窗口；零星历史缺口仍保持 null。

    **这道闸门只挡得住全面失效，挡不住系统性缺口。** 2026-09-14 那次
    114 只标的全区间没有名称，全局占比只有 2.05%，这里一声不响就放过了，
    下游静默少了 867 个首板。所以另外把「整只标的全窗口无名称」的数量
    记进结构化日志，并让 st_source 在名称为 null 时也为 null——
    覆盖率就能直接从 daily_flags 表里查出来，不必靠这道阈值。
    """
    if bars.height < 100:
        return
    missing = bars["name"].null_count()
    ratio = missing / bars.height
    if ratio > 0.10:
        raise RuntimeError(
            f"名称覆盖不足：{missing}/{bars.height} ({ratio:.1%}) 为 null；"
            "拒绝生成可能让候选池静默归零的 daily_flags"
        )
    if missing and "symbol" in bars.columns:
        per_symbol = bars.group_by("symbol").agg(
            pl.len().alias("rows"), pl.col("name").null_count().alias("missing")
        )
        blind = per_symbol.filter(pl.col("rows") == pl.col("missing")).height
        log.info(
            "daily_flags.name_coverage",
            missing_rows=missing,
            total_rows=bars.height,
            blind_symbols=blind,
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

        # 取 28 列供迁移兼容；ST 和涨跌停优先使用各自的直取事实表。
        bars = query.tushare_daily(None, lo - timedelta(days=_LOOKBACK_DAYS), hi)
        if bars.height == 0:
            continue
        # 付费源只投影三列：全历史 1,800 万行 × 28 列会把节点内存撑爆。
        back = lo - timedelta(days=_LOOKBACK_DAYS)
        st_truth = query.stock_st(None, back, hi)
        if st_truth.height:
            bars = bars.join(
                st_truth.select(
                    "symbol", "dt", pl.col("is_st").alias("_direct_is_st")
                ), on=["symbol", "dt"], how="left",
            )
            missing_st = bars["_direct_is_st"].null_count()
        else:
            missing_st = bars.height

        if missing_st == 0:
            bars = bars.with_columns(
                pl.col("_direct_is_st").alias("is_st"),
                pl.lit("stock_st").alias("st_source"),
            ).drop("_direct_is_st")
        else:
            if "_direct_is_st" in bars.columns:
                bars = bars.drop("_direct_is_st")
            # 迁移兼容：stock_st 全窗口回填并验收后，此分支不再触发，届时
            # paid_daily 才可物理清理。部分覆盖绝不与旧源逐行混用。
            paid_names = query.paid_daily(
                None, back, hi, columns=["symbol", "dt", "name"],
            )
            snapshots = query.name_snapshots(None, back, hi)
            bars = resolve_names(bars, paid_names, name_history, snapshots)
            _assert_name_coverage(bars)
            log.warning("daily_flags.stock_st_incomplete", missing_rows=missing_st,
                        total_rows=bars.height, start=str(back), end=str(hi))

        # 直取涨跌停价优先；尚未回填的历史行保留确定性派生值，便于分段迁移。
        direct_limits = query.price_limits(None, back, hi).select(
            "symbol", "dt",
            pl.col("pre_close").alias("_direct_pre_close"),
            pl.col("limit_up").alias("_direct_limit_up"),
            pl.col("limit_down").alias("_direct_limit_down"),
        )
        if direct_limits.height:
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

    # 完成 stock_st 回填的窗口已有显式布尔真值；迁移期才从名称直取值派生。
    is_st = (pl.col("is_st") if "is_st" in bars.columns else
             pl.when(pl.col("name").is_null()).then(None)
             .otherwise(pl.col("name").str.contains("ST")))

    frame = bars.sort(["symbol", "dt"]).with_columns(
        pl.col("close").shift(1).over("symbol").alias("prev_close"),
        # pre_close 直取即除权参考价，非除权日等于前收。
        (pl.coalesce("_direct_pre_close", "pre_close")
         if "_direct_pre_close" in bars.columns else pl.col("pre_close"))
        .alias("adj_prev_close"),
        ratio.alias("_ratio"),
        is_st.alias("is_st"),
        # 来源随名称走；名称为 null 就没有 ST 真值，来源也为 null——
        # 恒为一个字面量会让 2% 没有名称的行看起来也「有来源」。
        (pl.col("st_source") if "st_source" in bars.columns else
         pl.col("name_source") if "name_source" in bars.columns else
         pl.lit(None, dtype=pl.Utf8)).alias("st_source"),
    )

    # 兜底档位：**只在没有直取涨跌停价时用来算价格**。它不是 limit_pct
    # 的答案——见 ST_LIMIT 处关于 2026-07-06 新规的说明。
    frame = frame.with_columns(
        pl.when(pl.col("is_st"))
        .then(pl.lit(ST_LIMIT))
        .otherwise(pl.when(pl.col("is_st").is_null()).then(None).otherwise(pl.col("_ratio")))
        .alias("_fallback_pct")
    )

    cents = (pl.col("adj_prev_close") * 100).round(0).cast(pl.Int64)
    up_milli = ((1.0 + pl.col("_fallback_pct")) * 1000).round(0).cast(pl.Int64)
    down_milli = ((1.0 - pl.col("_fallback_pct")) * 1000).round(0).cast(pl.Int64)

    derived_up = _limit_expr(cents, up_milli)
    derived_down = _limit_expr(cents, down_milli)
    has_direct = "_direct_limit_up" in frame.columns
    frame = frame.with_columns(
        (pl.coalesce("_direct_limit_up", derived_up)
         if has_direct else derived_up).alias("limit_up"),
        (pl.coalesce("_direct_limit_down", derived_down)
         if "_direct_limit_down" in frame.columns else derived_down).alias("limit_down"),
    )

    # limit_pct 是**这一行的 limit_up 实际对应的那一档**，两者恒自洽。
    # 直取价按解码取档；派生价本就由 _fallback_pct 算出，直接沿用即可，
    # 走一遍解码反而会在低价股上因档位不可辨而丢掉已知的事实。
    frame = frame.with_columns(
        (pl.when(pl.col("_direct_limit_up").is_not_null())
         .then(_decode_tier(cents, pl.col("limit_up")))
         .otherwise(pl.col("_fallback_pct"))
         if has_direct else pl.col("_fallback_pct")).alias("limit_pct")
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
