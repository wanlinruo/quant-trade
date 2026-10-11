"""竞价入场的撮合口径。

回测默认让竞价时刻提交的买单按**当日开盘价**（加通用滑点）成交，这要求委托在
09:25 撮合前进场。实盘若在 09:25 撮合后才用最终数据决策，委托只能进 09:30 的
连续竞价，拿不到开盘价（研究见 docs/reference/auction-order-timing-study.md）。

三种口径：

- ``open``：现状，开盘价 + 通用滑点。不配本模块时引擎就是这个行为。
- ``slippage``：开盘价上浮固定比例，替换通用滑点。只改价格、不改成交率，作参照。
- ``continuous``：按第一根连续竞价分钟线（09:31，bar 以收盘时刻标注）判定——
  最低价高于限价则当天买不到；否则成交价取该分钟开盘价（加通用滑点）与限价中
  较低者。没有该分钟线时退回 ``slippage`` 口径并计数，不静默当成开盘成交。

只作用于竞价时刻提交的买单；隔夜卖单与盘中检查点的撮合不受影响。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from enum import Enum

from qsys.backtest.slippage import SlippageModel
from qsys.core.trading import Order, Side, to_cents

_MIN_PRICE = 0.01


class EntryFillMode(str, Enum):
    OPEN = "open"
    SLIPPAGE = "slippage"
    CONTINUOUS = "continuous"


@dataclass(frozen=True, slots=True)
class EntryFill:
    mode: EntryFillMode
    # slippage 口径的上浮比例，也是 continuous 缺分钟线时的退回口径
    slippage: float = 0.0
    # continuous 口径用的分钟线标注时刻
    minute_at: time = time(9, 31)
    # 研究压力测试可在通用滑点之上再抬高成交价；生产默认保持 0。
    price_stress: float = 0.0
    # 历史研究必须能选择缺分钟线即拒单，网页旧口径仍保留回退行为。
    fallback_on_missing: bool = True

    def __post_init__(self) -> None:
        if self.slippage < 0 or self.price_stress < 0:
            raise ValueError(
                "entry slippage and price stress must be >= 0: "
                f"{self.slippage}, {self.price_stress}"
            )

    def label(self) -> str:
        if self.mode is EntryFillMode.OPEN:
            return "开盘价成交"
        if self.mode is EntryFillMode.SLIPPAGE:
            return f"开盘价上浮 {self.slippage:.2%} 成交"
        missing = (
            f"缺分钟线退回开盘价上浮 {self.slippage:.2%}"
            if self.fallback_on_missing else "缺分钟线拒单"
        )
        stress = f"，额外成本压力 {self.price_stress:.2%}" if self.price_stress else ""
        return f"{self.minute_at:%H:%M} 分钟线连续竞价判定（{missing}{stress}）"


@dataclass(frozen=True, slots=True)
class MinuteBar:
    open: float
    low: float


@dataclass(frozen=True, slots=True)
class EntryDecision:
    """price 为 None 表示当天买不到；outcome 用于统计。"""

    price: float | None
    outcome: str
    reason: str = ""


def decide(
    rule: EntryFill,
    order: Order,
    session_open: float,
    minute: MinuteBar | None,
    slippage: SlippageModel,
) -> EntryDecision:
    if order.side is not Side.BUY:
        raise ValueError("entry fill only applies to buy orders")
    if rule.mode is EntryFillMode.OPEN:
        return EntryDecision(slippage.adjust(Side.BUY, session_open), "open")
    if rule.mode is EntryFillMode.SLIPPAGE:
        return EntryDecision(_shifted(session_open, rule.slippage), "slippage")

    limit = order.limit_price
    if minute is None:
        if not rule.fallback_on_missing:
            return EntryDecision(
                None, "missing_minute",
                f"required entry minute is missing: {rule.minute_at:%H:%M}",
            )
        price = _shifted(session_open, rule.slippage)
        if limit is not None:
            price = min(price, to_cents(limit))
        return EntryDecision(price, "fallback_no_minute")
    if limit is not None and minute.low > limit:
        return EntryDecision(
            None, "missed",
            f"entry limit not reached: {rule.minute_at:%H:%M} low {minute.low:.2f} "
            f"> limit {limit:.2f}",
        )
    price = _shifted(
        slippage.adjust(Side.BUY, minute.open), rule.price_stress,
    )
    if limit is not None:
        price = min(price, to_cents(limit))
    return EntryDecision(price, "continuous")


def _shifted(price: float, rate: float) -> float:
    return max(_MIN_PRICE, to_cents(price * (1.0 + rate)))
