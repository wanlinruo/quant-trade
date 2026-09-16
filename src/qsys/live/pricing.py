"""基于实时盘口的保守委托保护价。"""

from __future__ import annotations

import math
from datetime import datetime

from qsys.core.trading import Side
from qsys.live.messages import MarketQuote


class QuoteError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def protected_limit_price(
    quote: MarketQuote,
    side: Side,
    *,
    now: datetime | None = None,
    max_age_seconds: float = 3.0,
    protection: float = 0.002,
) -> float:
    moment = now or datetime.now()
    age = (moment - quote.at).total_seconds()
    if age < -1.0 or age > max_age_seconds:
        raise QuoteError("STALE_QUOTE", f"行情年龄 {age:.3f}s")
    if quote.suspended:
        raise QuoteError("SUSPENDED", "标的停牌")
    opponent = quote.ask1 if side is Side.BUY else quote.bid1
    if opponent <= 0:
        raise QuoteError("NO_OPPONENT", "没有有效对手价")
    if side is Side.BUY and quote.upper_limit and opponent >= quote.upper_limit:
        raise QuoteError("LIMIT_UP", "涨停买单无法保证成交")
    if side is Side.SELL and quote.lower_limit and opponent <= quote.lower_limit:
        raise QuoteError("LIMIT_DOWN", "跌停卖单无法保证成交")

    raw = opponent * (1 + protection if side is Side.BUY else 1 - protection)
    if side is Side.BUY and quote.upper_limit:
        raw = min(raw, quote.upper_limit)
    if side is Side.SELL and quote.lower_limit:
        raw = max(raw, quote.lower_limit)
    tick = quote.tick_size if quote.tick_size > 0 else 0.01
    units = math.floor(raw / tick + 1e-9) if side is Side.BUY else math.ceil(
        raw / tick - 1e-9
    )
    return round(max(tick, units * tick), 4)
