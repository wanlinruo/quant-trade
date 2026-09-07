"""除权除息处理。

回测使用不复权价——那才是真实可交易的价格，整手取整、涨跌停判定、
下单价格才能与实盘一致。代价是引擎必须在除权日显式调整持仓与现金，
本模块即是。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

import polars as pl
import structlog

from qsys.core.portfolio import Portfolio
from qsys.core.trading import to_cents
from qsys.core.types import Symbol

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CorporateAction:
    symbol: Symbol
    ex_date: date
    cash_per_share: float
    stock_per_share: float

    def __post_init__(self) -> None:
        if self.cash_per_share < 0 or self.stock_per_share < 0:
            raise ValueError("cash_per_share and stock_per_share must be >= 0")


def apply_corporate_actions(
    portfolio: Portfolio, actions: Iterable[CorporateAction]
) -> tuple[float, int]:
    """在除权日调整持仓与现金。返回 (派发现金总额, 送转股总数)。

    送转股到账即可卖——与当日买入不同，它不受交收约束。
    不足一股的送转部分舍去（真实规则是折现，此处简化）。
    """
    total_cash = 0.0
    total_shares = 0

    for action in actions:
        held = portfolio.positions.get(action.symbol)
        if held is None or held.total_qty == 0:
            continue

        if action.cash_per_share > 0:
            paid = to_cents(held.total_qty * action.cash_per_share)
            portfolio.cash = to_cents(portfolio.cash + paid)
            total_cash = to_cents(total_cash + paid)

        if action.stock_per_share > 0:
            bonus = int(held.total_qty * action.stock_per_share)
            if bonus > 0:
                held.total_qty += bonus
                held.available_qty += bonus
                total_shares += bonus

        log.info(
            "corporate_action.applied",
            symbol=str(action.symbol),
            ex_date=str(action.ex_date),
            cash_per_share=action.cash_per_share,
            stock_per_share=action.stock_per_share,
        )

    return total_cash, total_shares


def actions_from_frame(df: pl.DataFrame) -> dict[date, list[CorporateAction]]:
    """把仓库的 corporate_actions 表转成按除权日分组的事件。"""
    grouped: dict[date, list[CorporateAction]] = defaultdict(list)
    for row in df.iter_rows(named=True):
        grouped[row["ex_date"]].append(
            CorporateAction(
                symbol=Symbol.parse(row["symbol"]),
                ex_date=row["ex_date"],
                cash_per_share=float(row["cash_per_share"]),
                stock_per_share=float(row["stock_per_share"]),
            )
        )
    return dict(grouped)
