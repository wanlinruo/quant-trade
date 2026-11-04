"""#588：实时报价请求集合的覆盖记录；不推定市场事实或交易许可。"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
import hashlib
import json
import math
import re

from qsys.live.messages import ExchangeCoverage, MarketCoverage, MarketQuote


def equity_market(symbol: str) -> str | None:
    match = re.fullmatch(r"([0-9]{6})\.(SH|SZ|BJ)", symbol)
    if match is None or match[1].startswith(("900", "200")):
        return None
    return match[2]


def quote_state(symbol: str, q: MarketQuote, now: datetime) -> str:
    if q.symbol != symbol or any(not math.isfinite(v) for v in (
        q.price, q.prev_close, q.open, q.high, q.low, q.amount,
    )) or q.amount < 0 or q.volume < 0:
        return "invalid"
    if (q.at.tzinfo is None) != (now.tzinfo is None):
        return "other_session"
    session = q.at.astimezone(now.tzinfo).date() if now.tzinfo else q.at.date()
    if session != now.date():
        return "other_session"
    if q.at > now:
        return "invalid"
    if q.suspended:
        return "suspended"
    return "valid" if q.price > 0 and q.prev_close > 0 else "invalid"


def market_coverage(
    quotes: dict[str, MarketQuote], expected_symbols: Sequence[str] | None, *,
    universe_source: str | None, universe_session: date | None, now: datetime,
) -> MarketCoverage:
    if expected_symbols is None:
        return MarketCoverage(
            status="unknown", universe_source=universe_source,
            universe_session=universe_session, universe_sha256=None, expected=None,
            excluded_non_a=None, invalid_identity_count=None, unexpected_count=None, by_market=[],
        )
    requested = sorted(set(expected_symbols))
    digest = hashlib.sha256(json.dumps(requested, separators=(",", ":")).encode()).hexdigest()
    non_a = {s for s in requested if re.fullmatch(r"(?:900|200)[0-9]{3}\.(?:SH|SZ|BJ)", s)}
    invalid = {s for s in requested if equity_market(s) is None} - non_a
    expected = set(requested) - non_a - invalid
    rows = []
    for market in ("SH", "SZ", "BJ"):
        wanted = {s for s in expected if equity_market(s) == market}
        received = wanted & quotes.keys()
        valid = suspended = invalid_rows = other_session = 0
        stamps = []
        for symbol in received:
            q = quotes[symbol]
            # 老的源时刻可以是低流动性证券的真实最后一笔，保留min/max供消费者判断。
            # 这里只核会话及未来时刻，不用45秒阈值猜测“没有成交”。
            if (q.at.tzinfo is None) == (now.tzinfo is None):
                stamps.append(q.at)
            state = quote_state(symbol, q, now)
            if state == "other_session":
                other_session += 1
            elif state == "invalid":
                invalid_rows += 1
            elif state == "suspended":
                suspended += 1
            else:
                valid += 1
        rows.append(ExchangeCoverage(
            market=market, expected=len(wanted), received=len(received), valid=valid,
            suspended=suspended, missing=len(wanted - received), invalid=invalid_rows,
            other_session=other_session, missing_samples=sorted(wanted - received)[:20],
            source_at_min=min(stamps) if stamps else None,
            source_at_max=max(stamps) if stamps else None,
        ))
    unexpected = set(quotes) - set(requested) - {"000001.SH", "399001.SZ", "399006.SZ", "000300.SH"}
    status = "complete"
    if not universe_source or universe_session != now.date() or not expected:
        status = "unknown"
    elif invalid or unexpected or any(
        r.missing or r.invalid or r.other_session or r.suspended for r in rows
    ):
        status = "incomplete"
    return MarketCoverage(
        status=status, universe_source=universe_source, universe_session=universe_session,
        universe_sha256=digest, expected=len(expected), excluded_non_a=len(non_a),
        invalid_identity_count=len(invalid), unexpected_count=len(unexpected), by_market=rows,
    )
