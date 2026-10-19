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
    limits: tuple[float, float] | None,
    now: datetime | None = None,
    max_age_seconds: float = 3.0,
    source_not_before: datetime | None = None,
    protection: float = 0.002,
) -> float:
    """保护价以当日直取涨跌停价封顶/托底。

    `limits` 是 price_limits_daily 里的 stk_limit (up, down)。QMT get_full_tick
    不给涨跌停价，`quote.upper_limit` 恒为空，不再读它（D-19）。缺价时买单拒绝、
    卖单不设跌停托底照常定价——真实跌停由交易所拦截。
    """
    moment = now or datetime.now()
    source_age = (moment - quote.at).total_seconds()
    if source_age < -1.0:
        raise QuoteError("STALE_QUOTE", f"行情源时间超前 {-source_age:.3f}s")
    if source_not_before is None:
        if source_age > max_age_seconds:
            raise QuoteError("STALE_QUOTE", f"行情年龄 {source_age:.3f}s")
    else:
        # 09:25 撮合后到 09:30 连续竞价前，交易所不会持续生成新 tick；最终
        # 集合竞价快照的源时间可以停在 09:25:00。这里只放宽源时间，响应仍
        # 必须来自刚完成的按需请求，且不得退回到 09:25 以前的盘中快照。
        if quote.at < source_not_before:
            raise QuoteError(
                "STALE_QUOTE",
                f"行情源时间 {quote.at.isoformat()} 早于 {source_not_before.isoformat()}",
            )
        if quote.received_at is None:
            raise QuoteError("STALE_QUOTE", "行情响应时间缺失")
        response_age = (moment - quote.received_at).total_seconds()
        if response_age < -1.0 or response_age > max_age_seconds:
            raise QuoteError("STALE_QUOTE", f"行情响应年龄 {response_age:.3f}s")
    if quote.suspended:
        raise QuoteError("SUSPENDED", "标的停牌")
    opponent = quote.ask1 if side is Side.BUY else quote.bid1
    if opponent <= 0:
        raise QuoteError("NO_OPPONENT", "没有有效对手价")
    upper, lower = limits or (None, None)
    if side is Side.BUY and upper is None:
        raise QuoteError("NO_PRICE_LIMIT", "缺当日直取涨跌停价，拒绝买入")
    if side is Side.BUY and opponent >= upper:
        raise QuoteError("LIMIT_UP", "涨停买单无法保证成交")
    if side is Side.SELL and lower is not None and opponent <= lower:
        raise QuoteError("LIMIT_DOWN", "跌停卖单无法保证成交")

    raw = opponent * (1 + protection if side is Side.BUY else 1 - protection)
    if side is Side.BUY:
        raw = min(raw, upper)
    if side is Side.SELL and lower is not None:
        raw = max(raw, lower)
    tick = quote.tick_size if quote.tick_size > 0 else 0.01
    units = math.floor(raw / tick + 1e-9) if side is Side.BUY else math.ceil(
        raw / tick - 1e-9
    )
    return round(max(tick, units * tick), 4)
