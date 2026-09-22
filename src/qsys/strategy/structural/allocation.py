"""S8 权重分配 + CASH-SAFE V2 定量，对照原文 L976-L1010 与 `_cash_safe_v2_sizing`。

权重在**当日通过 Gate 的候选内部**归一化——只有一只候选时它可以拿到
全部可用资金。这是原策略明确写的行为，不是遗漏。

**CASH-SAFE V2**（原策略后来加的执行保护，我们跟着改）：

1. 按 `开盘价 x 1.002` 而非开盘价定量，并以此价挂**限价单**；
   上限是当日涨停价——买不到涨停价之上。
2. 先扣一笔费用准备金再算股数，避免"按未滑点价用满现金、
   实际按滑点价成交"导致的透支。

不加这两条时，聚宽实测 241 天里有 **44 天现金为负**（最深 -117,458）；
加了之后 **0 天**。透支的次日 `max(0, cash)` 归零，当天什么都买不了——
这正是两边买入集合分叉的一个来源。

费用准备金用的是原文写死的常数，与回测实际计费的成本模型无关——
照抄，否则定量对不上。

`budget / protect_price >= 100` 才下单：不足一手就跳过并记
`BELOW_BOARD_LOT`。跳过的候选也返回一行——否则事后说不清是没选中还是买不起。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from qsys.strategy.structural.params import BRANCH_WEIGHTS

LOT = 100

# 原文 CASH_SAFE_V2_PRICE_BUFFER。策略参数 entry_price_buffer 可覆盖：09:25 撮合后
# 才下单时委托进 09:30 连续竞价，0.2% 的保护价挂不上的比例明显变高。
PRICE_BUFFER = 0.002

# 原文 _cash_safe_v2_buy_fee_reserve 读的 S6_* 常数。写死是原策略的行为：
# 准备金不随实际成本模型变化，照抄才对得上定量。
RESERVE_COMMISSION_RATE = 0.0003
RESERVE_MIN_COMMISSION = 5.0
RESERVE_TRANSFER_RATE = 0.00001


@dataclass(frozen=True, slots=True)
class Budget:
    symbol: str
    tag: str
    weight: float
    budget: float
    qty: int
    status: str
    # 限价保护价。下单要用它，不是开盘价。
    protect_price: float | None = None


def fee_reserve(budget: float) -> float:
    """买入前预留的费用。对照原文 `_cash_safe_v2_buy_fee_reserve`。"""
    if budget <= 0:
        return 0.0
    commission = max(RESERVE_MIN_COMMISSION, budget * RESERVE_COMMISSION_RATE)
    return commission + budget * RESERVE_TRANSFER_RATE


def protect_price(
    open_price: float, limit_up: float | None, buffer: float = PRICE_BUFFER
) -> float:
    """限价保护价：开盘价上浮 `buffer`（默认 `PRICE_BUFFER`），但不超过涨停价。"""
    if buffer < 0:
        raise ValueError(f"price buffer must be >= 0: {buffer}")
    protected = open_price * (1.0 + buffer)
    if limit_up is not None and limit_up > 0:
        return min(protected, limit_up)
    return protected


def allocate(
    candidates: Sequence[tuple[str, str]],
    cash: float,
    opens: Mapping[str, float],
    limit_ups: Mapping[str, float] | None = None,
    max_budget: float | None = None,
    price_buffer: float = PRICE_BUFFER,
    sizing_buffer: float | None = None,
) -> list[Budget]:
    """把可用资金按分支权重分给当日候选，再按 CASH-SAFE V2 定量。

    `price_buffer` 决定限价（保护价），`sizing_buffer` 决定定量用的价格；后者
    不给时与前者相同，即原策略"按保护价定量"。09:25 撮合后才下单时保护价要放宽到
    +3% 才能全部成交，但按 +3% 定量会让每笔少买约 3%，故允许两者分开。
    """
    if sizing_buffer is None:
        sizing_buffer = price_buffer
    if not candidates:
        return []

    weights = [(s, tag, float(BRANCH_WEIGHTS.get(tag, 1.0))) for s, tag in candidates]
    weights = [(s, tag, w if w > 0 else 1.0) for s, tag, w in weights]
    total = sum(w for _, _, w in weights) or float(len(weights))
    limits = limit_ups or {}

    allocatable = max(0.0, cash)
    out: list[Budget] = []
    for symbol, tag, weight in weights:
        budget = allocatable * weight / total
        if max_budget is not None:
            budget = min(budget, max(0.0, max_budget))
        if allocatable <= 0:
            out.append(Budget(symbol, tag, weight, 0.0, 0, "NO_AVAILABLE_CASH"))
            continue

        price = opens.get(symbol)
        if price is None or price <= 0:
            out.append(Budget(symbol, tag, weight, budget, 0, "INVALID_OPEN_PRICE"))
            continue

        protected = protect_price(price, limits.get(symbol), price_buffer)
        if protected <= 0:
            out.append(Budget(symbol, tag, weight, budget, 0, "INVALID_OPEN_PRICE"))
            continue
        sizing = protect_price(price, limits.get(symbol), sizing_buffer)

        principal = max(0.0, budget - fee_reserve(budget))
        qty = int(principal / sizing / LOT) * LOT
        if qty < LOT:
            out.append(
                Budget(symbol, tag, weight, budget, 0, "BELOW_BOARD_LOT", protected)
            )
            continue

        out.append(
            Budget(symbol, tag, weight, budget, qty, "ORDER_SUBMITTED", protected)
        )
    return out
