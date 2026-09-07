"""账户与持仓记账。

本模块只管记账，不判断能否交易——那是 constraints 的职责。
它提供冻结/解冻机制但不知道 T+1 是什么：买入只增加 total_qty，
unfreeze() 才补齐可卖数量。解冻时机由调用方决定（T+1 市场在每个交易日
开始时调用；T+0 市场可在每次成交后调用），这是多市场预留的关键。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from qsys.core.trading import Fill, Side, to_cents
from qsys.core.types import Symbol


@dataclass(slots=True)
class Position:
    symbol: Symbol
    total_qty: int = 0
    available_qty: int = 0  # 已解冻、可卖出的数量
    cost_basis: float = 0.0  # 累计买入成本（含费用）

    @property
    def avg_cost(self) -> float:
        if self.total_qty == 0:
            return 0.0
        return to_cents(self.cost_basis / self.total_qty)

    @property
    def is_empty(self) -> bool:
        return self.total_qty == 0


class Portfolio:
    def __init__(self, initial_cash: float) -> None:
        self.cash = to_cents(initial_cash)
        self.initial_cash = self.cash
        self.positions: dict[Symbol, Position] = {}

    def position(self, symbol: Symbol) -> Position:
        """返回持仓快照。修改快照不影响账户内部状态。"""
        held = self.positions.get(symbol)
        return replace(held) if held is not None else Position(symbol)

    def apply_fill(self, fill: Fill) -> None:
        held = self.positions.get(fill.symbol) or Position(fill.symbol)

        if fill.side is Side.BUY:
            held.total_qty += fill.qty
            held.cost_basis = to_cents(held.cost_basis + fill.gross + fill.fee)
            # 不增加 available_qty —— 解冻由调用方通过 unfreeze() 决定
        else:
            if fill.qty > held.total_qty:
                raise ValueError(
                    f"cannot sell {fill.qty} of {fill.symbol}: only {held.total_qty} held"
                )
            ratio = (held.total_qty - fill.qty) / held.total_qty
            held.cost_basis = to_cents(held.cost_basis * ratio)
            held.total_qty -= fill.qty
            held.available_qty = max(0, held.available_qty - fill.qty)

        self.cash = to_cents(self.cash + fill.cash_delta)

        if held.is_empty:
            self.positions.pop(fill.symbol, None)
        else:
            self.positions[fill.symbol] = held

    def unfreeze(self) -> None:
        """把全部持仓标记为可卖。"""
        for held in self.positions.values():
            held.available_qty = held.total_qty

    def market_value(self, prices: Mapping[Symbol, float]) -> float:
        total = 0.0
        for symbol, held in self.positions.items():
            if symbol not in prices:
                raise KeyError(f"no price for held symbol {symbol}")
            total += held.total_qty * prices[symbol]
        return to_cents(total)

    def total_value(self, prices: Mapping[Symbol, float]) -> float:
        return to_cents(self.cash + self.market_value(prices))
