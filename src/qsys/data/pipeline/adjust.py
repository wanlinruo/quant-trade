"""复权因子落库与校验。

行情事实表存不复权价格永不重写，复权因子单独存储并可随时重算——
这样任意历史时点的复权价都能精确重现，避免前复权序列落盘后腐烂。

价格累计因子从 Tushare 直连日线的 ``factor`` 列直取。以下公式只用于校验，不再
参与 ``cum_factor`` 落库：除权日单日因子 = 前收盘价 / 除权除息参考价，其中
    参考价 = (前收 - 每股现金红利 + 配股价 x 每股配股数) / (1 + 每股送转 + 每股配股)
    cum_factor(t) = 所有 ex_date <= t 的单日因子连乘
    后复权价(t) = 不复权价(t) x cum_factor(t)

**成交量另有一套因子**：股数只随送转与配股变化，与派现无关，所以
    单日股本比例 = 1 + 每股送转 + 每股配股
    cum_share_factor(t) = 连乘
    前复权量(t) = 不复权量(t) x cum_share_factor(锚点) / cum_share_factor(t)

拿价格因子去调成交量会多调掉现金分红那一部分。实测 301379.SZ 的
2026-06-05（10 送 4 派 0.2）：价格因子倒数 1.4075，股本比例 1.4000，
差 0.5%——落在阈值上就能翻转判定。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.providers.base import ReferenceProvider
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

_EPOCH = date(1990, 1, 1)
_HORIZON = date(2100, 1, 1)


def daily_factor(
    prev_close: float,
    cash: float,
    stock: float,
    rights: float,
    rights_price: float,
) -> float:
    """除权日单日因子 = 前收盘价 / 除权除息参考价。"""
    reference = (prev_close - cash + rights_price * rights) / (1.0 + stock + rights)
    if reference <= 0:
        raise ValueError(f"non-positive reference price: {reference}")
    return prev_close / reference


def share_factor(stock: float, rights: float) -> float:
    """单日股本比例。派现不改变股数，故不参与。"""
    return 1.0 + stock + rights


def build_adj_factors(warehouse: Warehouse) -> int:
    """落库直取价格因子，并继续派生独立的成交量股本因子。"""
    query = WarehouseQuery(warehouse)
    direct = tushare_factors(query, None, _EPOCH, _HORIZON)
    if direct.height == 0:
        return 0

    actions = query.corporate_actions()
    if actions.height == 0:
        result = direct.select(
            "symbol", "dt",
            pl.col("factor").alias("cum_factor"),
            pl.lit(1.0).alias("cum_share_factor"),
        )
    else:
        result = (
            direct.join(
                actions,
                left_on=["symbol", "dt"],
                right_on=["symbol", "ex_date"],
                how="left",
            )
            .with_columns(
                (
                    1.0
                    + pl.col("stock_per_share").fill_null(0.0)
                    + pl.col("rights_per_share").fill_null(0.0)
                )
                .cum_prod()
                .over("symbol")
                .alias("cum_share_factor")
            )
            .select(
                "symbol",
                "dt",
                pl.col("factor").alias("cum_factor"),
                "cum_share_factor",
            )
        )

    result = result.sort(["symbol", "dt"])
    warehouse.write("adj_factors", result.to_arrow().cast(schema.ADJ_FACTORS))
    log.info("adj_factors.built", rows=result.height)
    return result.height


def tushare_factors(
    query: WarehouseQuery,
    symbols: Sequence[str] | None,
    start: date,
    end: date,
) -> pl.DataFrame:
    """从 Tushare 直连日线直取累计后复权因子。"""
    return query.tushare_daily(
        symbols, start, end, columns=["symbol", "dt", "factor"]
    )


_FACTOR_REPORT_SCHEMA = {
    "symbol": pl.Utf8,
    "dt": pl.Date,
    "direct": pl.Float64,
    "computed": pl.Float64,
    "rel_diff": pl.Float64,
}


def verify_computed_factors(
    query: WarehouseQuery,
    start: date,
    end: date,
) -> pl.DataFrame:
    """逐年比对直取与价格反推因子，只报告差异，不落库、不覆盖。

    比较的是相邻日累计因子的比值与 ``前收盘 / pre_close``，只把真正发生
    分歧的事件日写进报告，避免一次历史偏差污染其后所有日期。逐年读取避免
    全历史日线在生产节点上撑爆内存；跨年保留每只股票的最后收盘价与直取
    因子，因此年初首个交易日也不会漏检。
    """
    if start > end:
        return pl.DataFrame(schema=_FACTOR_REPORT_SCHEMA)

    # symbol -> (last close, last direct cumulative factor)
    state: dict[str, tuple[float, float]] = {}
    reports: list[pl.DataFrame] = []
    columns = ["symbol", "dt", "close", "pre_close", "factor"]

    for year in range(start.year, end.year + 1):
        lo = max(start, date(year, 1, 1))
        hi = min(end, date(year, 12, 31))
        direct_daily = query.tushare_daily(None, lo, hi, columns=columns)
        if direct_daily.height == 0:
            continue
        direct_daily = direct_daily.sort(["symbol", "dt"])

        if state:
            prior = pl.DataFrame(
                {
                    "symbol": list(state),
                    "_prior_close": [v[0] for v in state.values()],
                    "_prior_direct": [v[1] for v in state.values()],
                }
            )
            direct_daily = direct_daily.join(prior, on="symbol", how="left")
        else:
            direct_daily = direct_daily.with_columns(
                pl.lit(None, dtype=pl.Float64).alias("_prior_close"),
                pl.lit(None, dtype=pl.Float64).alias("_prior_direct"),
            )

        frame = (
            direct_daily.with_columns(
                pl.col("close")
                .shift(1)
                .over("symbol")
                .fill_null(pl.col("_prior_close"))
                .alias("_previous_close")
            )
            .with_columns(
                pl.col("factor")
                .shift(1)
                .over("symbol")
                .fill_null(pl.col("_prior_direct"))
                .alias("_previous_direct")
            )
            .with_columns(
                (pl.col("factor") / pl.col("_previous_direct"))
                .fill_null(1.0)
                .alias("direct"),
                (pl.col("_previous_close") / pl.col("pre_close"))
                .fill_null(1.0)
                .alias("computed"),
            )
        )

        report = (
            frame.select(
                "symbol",
                "dt",
                "direct",
                "computed",
                ((pl.col("computed") - pl.col("direct")).abs() / pl.col("direct"))
                .alias("rel_diff"),
            )
            .filter(pl.col("rel_diff") > 1e-9)
        )
        if report.height:
            reports.append(report)

        tails = frame.group_by("symbol", maintain_order=True).agg(
            pl.col("close").last(), pl.col("factor").last()
        )
        for symbol, close, direct in tails.iter_rows():
            if close is not None and direct is not None:
                state[symbol] = (close, direct)

    if not reports:
        return pl.DataFrame(schema=_FACTOR_REPORT_SCHEMA)
    return pl.concat(reports, how="vertical").sort("rel_diff", descending=True)


ACTION_COLUMNS = [
    "symbol",
    "ex_date",
    "cash_per_share",
    "stock_per_share",
    "rights_per_share",
    "rights_price",
]


def build_corporate_actions(
    provider: ReferenceProvider,
    warehouse: Warehouse,
    symbols: Sequence[str],
) -> int:
    """采集除权除息事件写入仓库。

    必须在 build_adj_factors 之前调用，供 `cum_share_factor` 计算成交量复权；
    价格 `cum_factor` 已从日线真值源直取，不再依赖本表。
    """
    collected: list[pl.DataFrame] = []
    for symbol in symbols:
        try:
            frame = provider.fetch_corporate_actions(symbol)
        except Exception as exc:  # 单个标的失败不影响其余
            log.warning("corporate_actions.fetch_failed", symbol=symbol, error=str(exc))
            continue
        if frame.height == 0:
            continue
        collected.append(
            frame.with_columns(pl.lit(symbol).alias("symbol")).select(ACTION_COLUMNS)
        )

    if not collected:
        return 0

    fresh = pl.concat(collected, how="vertical")
    existing = WarehouseQuery(warehouse).corporate_actions()
    merged = (
        pl.concat([existing, fresh], how="vertical")
        .unique(subset=["symbol", "ex_date"], keep="last", maintain_order=True)
        .sort(["symbol", "ex_date"])
        .select(ACTION_COLUMNS)
    )
    warehouse.write("corporate_actions", merged.to_arrow().cast(schema.CORPORATE_ACTIONS))
    log.info("corporate_actions.built", provider=provider.name, rows=merged.height)
    return merged.height
