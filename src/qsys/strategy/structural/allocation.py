"""S8 权重分配，对照原文 L976-L1010。

权重在**当日通过 Gate 的候选内部**归一化——只有一只候选时它可以拿到
全部可用资金。这是原策略明确写的行为（原文 L83-L84 的注释），
不是遗漏。

`budget / open_price >= 100` 才下单：不足一手就跳过并记 BELOW_BOARD_LOT。
跳过的候选也返回一行——否则事后说不清是没选中还是买不起。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from qsys.strategy.structural.params import BRANCH_WEIGHTS

LOT = 100


@dataclass(frozen=True, slots=True)
class Budget:
    symbol: str
    tag: str
    weight: float
    budget: float
    qty: int
    status: str


def allocate(
    candidates: Sequence[tuple[str, str]],
    cash: float,
    opens: Mapping[str, float],
) -> list[Budget]:
    """把可用资金按分支权重分给当日候选。"""
    if not candidates:
        return []

    weights = [(s, tag, float(BRANCH_WEIGHTS.get(tag, 1.0))) for s, tag in candidates]
    weights = [(s, tag, w if w > 0 else 1.0) for s, tag, w in weights]
    total = sum(w for _, _, w in weights) or float(len(weights))

    allocatable = max(0.0, cash)
    out: list[Budget] = []
    for symbol, tag, weight in weights:
        budget = allocatable * weight / total
        if allocatable <= 0:
            out.append(Budget(symbol, tag, weight, 0.0, 0, "NO_AVAILABLE_CASH"))
            continue

        price = opens.get(symbol)
        if price is None or price <= 0:
            out.append(Budget(symbol, tag, weight, budget, 0, "INVALID_OPEN_PRICE"))
            continue

        if budget / price < LOT:
            out.append(Budget(symbol, tag, weight, budget, 0, "BELOW_BOARD_LOT"))
            continue

        qty = int(budget / price / LOT) * LOT
        out.append(Budget(symbol, tag, weight, budget, qty, "ORDER_SUBMITTED"))
    return out
