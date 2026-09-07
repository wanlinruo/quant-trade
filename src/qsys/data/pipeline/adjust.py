"""复权因子计算。

行情事实表存不复权价格永不重写，复权因子单独存储并可随时重算——
这样任意历史时点的复权价都能精确重现，避免前复权序列落盘后腐烂。

除权日单日因子 = 前收盘价 / 除权除息参考价，其中
    参考价 = (前收 - 每股现金红利 + 配股价 x 每股配股数) / (1 + 每股送转 + 每股配股)
    cum_factor(t) = 所有 ex_date <= t 的单日因子连乘
    后复权价(t) = 不复权价(t) x cum_factor(t)
"""

from __future__ import annotations

from datetime import date

import polars as pl
import structlog

from qsys.data import schema
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
        result = bars.select("symbol", "dt", pl.lit(1.0).alias("cum_factor"))
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
        for row in joined.iter_rows(named=True):
            has_action = (
                row["cash_per_share"] or row["stock_per_share"] or row["rights_per_share"]
            )
            if not has_action or row["prev_close"] is None:
                ratios.append(1.0)
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

        result = (
            joined.with_columns(pl.Series("ratio", ratios))
            .with_columns(pl.col("ratio").cum_prod().over("symbol").alias("cum_factor"))
            .select("symbol", "dt", "cum_factor")
        )

    result = result.sort(["symbol", "dt"])
    warehouse.write("adj_factors", result.to_arrow().cast(schema.ADJ_FACTORS))
    log.info("adj_factors.built", rows=result.height)
    return result.height
