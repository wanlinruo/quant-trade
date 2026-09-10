"""复权因子计算。

行情事实表存不复权价格永不重写，复权因子单独存储并可随时重算——
这样任意历史时点的复权价都能精确重现，避免前复权序列落盘后腐烂。

除权日单日因子 = 前收盘价 / 除权除息参考价，其中
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
    query = WarehouseQuery(warehouse)
    bars = query.daily_bars(None, _EPOCH, _HORIZON)
    if bars.height == 0:
        return 0

    actions = query.corporate_actions()
    bars = bars.sort(["symbol", "dt"]).with_columns(
        pl.col("close").shift(1).over("symbol").alias("prev_close")
    )

    if actions.height == 0:
        result = bars.select(
            "symbol", "dt",
            pl.lit(1.0).alias("cum_factor"),
            pl.lit(1.0).alias("cum_share_factor"),
        )
    else:
        joined = bars.join(
            actions, left_on=["symbol", "dt"], right_on=["symbol", "ex_date"], how="left"
        ).with_columns(
            [
                pl.col(c).fill_null(0.0)
                for c in (
                    "cash_per_share",
                    "stock_per_share",
                    "rights_per_share",
                    "rights_price",
                )
            ]
        )

        ratios: list[float] = []
        shares: list[float] = []
        for row in joined.iter_rows(named=True):
            has_action = (
                row["cash_per_share"] or row["stock_per_share"] or row["rights_per_share"]
            )
            if not has_action or row["prev_close"] is None:
                ratios.append(1.0)
                shares.append(1.0)
                continue
            ratios.append(
                daily_factor(
                    row["prev_close"],
                    row["cash_per_share"],
                    row["stock_per_share"],
                    row["rights_per_share"],
                    row["rights_price"],
                )
            )
            shares.append(
                share_factor(row["stock_per_share"], row["rights_per_share"])
            )

        result = (
            joined.with_columns(
                pl.Series("ratio", ratios), pl.Series("share_ratio", shares)
            )
            .with_columns(
                pl.col("ratio").cum_prod().over("symbol").alias("cum_factor"),
                pl.col("share_ratio").cum_prod().over("symbol")
                .alias("cum_share_factor"),
            )
            .select("symbol", "dt", "cum_factor", "cum_share_factor")
        )

    result = result.sort(["symbol", "dt"])
    warehouse.write("adj_factors", result.to_arrow().cast(schema.ADJ_FACTORS))
    log.info("adj_factors.built", rows=result.height)
    return result.height


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

    必须在 build_adj_factors 之前调用——缺了这一步，复权因子会全部等于 1.0
    且不会报任何错，复权静默失效。
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
