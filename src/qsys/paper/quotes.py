"""Request/response bridge between paper workers and the Windows QMT gateway."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from datetime import date, datetime, time as dt_time
from decimal import Decimal, ROUND_HALF_UP
from statistics import median

from qsys.live.config import StreamConfig
from qsys.live.messages import (
    Heartbeat,
    MarketIndexQuote,
    MarketOverviewSnapshot,
    MarketQuote,
    MarketSchedule,
    QuoteRequest,
    QuoteSnapshot,
)
from qsys.live.transport import Transport
from qsys.markets.ashare import PRICE_LIMITS, board_of


MARKET_INDICES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000300.SH": "沪深300",
}


class QuoteUnavailable(RuntimeError):
    """QMT 行情暂时拿不到：网关超时或网关回报错误。调用方可重试。"""


class QuoteTimeout(QuoteUnavailable, TimeoutError):
    pass


class QuoteClient:
    def __init__(
        self, transport: Transport, streams: StreamConfig, *,
        consumer: str, timeout: float = 5.0,
    ) -> None:
        self._t, self._s = transport, streams
        self._consumer, self._timeout = consumer, timeout
        self._group = f"paper-quotes-{consumer}"
        self._t.ensure_group(streams.quote_snapshots, self._group)
        self._deferred: dict[str, QuoteSnapshot] = {}

    def request(self, symbols: Sequence[str]) -> dict[str, MarketQuote]:
        unique = sorted(set(symbols))
        if not unique:
            return {}
        request_id = uuid.uuid4().hex
        self._t.append(self._s.quote_requests, QuoteRequest(
            request_id=request_id, symbols=unique, requested_at=datetime.now()
        ).to_payload())
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            cached = self._deferred.pop(request_id, None)
            if cached is not None:
                return self._unwrap(cached)
            for message in self._t.read_group(
                self._s.quote_snapshots, self._group, self._consumer, count=100
            ):
                self._t.ack(self._s.quote_snapshots, self._group, message.id)
                snapshot = QuoteSnapshot.from_payload(message.payload)
                if snapshot.request_id == request_id:
                    return self._unwrap(snapshot)
                self._deferred[snapshot.request_id] = snapshot
            time.sleep(0.05)
        raise QuoteTimeout(f"QMT 行情请求超时（{len(unique)} 个标的）")

    @staticmethod
    def _unwrap(snapshot: QuoteSnapshot) -> dict[str, MarketQuote]:
        if snapshot.error:
            raise QuoteUnavailable(f"QMT 行情错误: {snapshot.error}")
        return snapshot.quotes


class MarketGateway:
    """Read-only gateway. The provider callable is the only QMT-specific seam."""

    def __init__(
        self, transport: Transport, streams: StreamConfig,
        provider: Callable[[Sequence[str]], dict[str, MarketQuote]], *,
        group: str = "market-gateway", consumer: str = "market-1",
    ) -> None:
        self._t, self._s, self._provider = transport, streams, provider
        self._group, self._consumer = group, consumer
        self._t.ensure_group(streams.quote_requests, group)

    def run_once(self) -> int:
        handled = 0
        for message in self._t.read_group(
            self._s.quote_requests, self._group, self._consumer, count=50
        ):
            handled += 1
            try:
                request = QuoteRequest.from_payload(message.payload)
                try:
                    quotes = self._provider(request.symbols)
                    response = QuoteSnapshot(
                        request_id=request.request_id, published_at=datetime.now(),
                        quotes=quotes,
                    )
                except Exception as exc:
                    response = QuoteSnapshot(
                        request_id=request.request_id, published_at=datetime.now(),
                        quotes={}, error=str(exc),
                    )
                self._t.append(self._s.quote_snapshots, response.to_payload())
            finally:
                self._t.ack(self._s.quote_requests, self._group, message.id)
        return handled

    def beat(self) -> None:
        self._t.set_key(
            f"{self._s.heartbeat}:market",
            Heartbeat(source="market", at=datetime.now()).model_dump_json(),
        )

    def publish_overview(self, quotes: dict[str, MarketQuote]) -> MarketOverviewSnapshot:
        snapshot = build_market_overview(quotes)
        self._t.set_key(self._s.market_snapshot, snapshot.model_dump_json())
        return snapshot

    def publish_schedule(self, schedule: MarketSchedule) -> None:
        self._t.set_key(self._s.market_schedule, schedule.model_dump_json())


def build_market_schedule(
    now: datetime, sessions: Sequence[date],
) -> MarketSchedule:
    """Return continuous-auction state and the next time live polling is useful."""
    trading_days = sorted(set(sessions))
    today = now.date()
    is_trading_day = today in trading_days
    morning = (datetime.combine(today, dt_time(9, 30)),
               datetime.combine(today, dt_time(11, 30)))
    afternoon = (datetime.combine(today, dt_time(13, 0)),
                 datetime.combine(today, dt_time(15, 0)))
    is_open = is_trading_day and (
        morning[0] <= now <= morning[1] or afternoon[0] <= now <= afternoon[1]
    )
    next_open: datetime | None = None
    if not is_open:
        if is_trading_day and now < morning[0]:
            next_open = morning[0]
        elif is_trading_day and morning[1] < now < afternoon[0]:
            next_open = afternoon[0]
        else:
            next_day = next((day for day in trading_days if day > today), None)
            if next_day is not None:
                next_open = datetime.combine(next_day, dt_time(9, 30))
    return MarketSchedule(
        checked_at=now, is_trading_day=is_trading_day,
        is_open=is_open, next_open_at=next_open,
    )


def build_market_overview(
    quotes: dict[str, MarketQuote], *, now: datetime | None = None,
) -> MarketOverviewSnapshot:
    """Aggregate one QMT full-market tick without persisting intraday facts."""
    published_at = now or datetime.now()
    indices: list[MarketIndexQuote] = []
    for symbol, name in MARKET_INDICES.items():
        quote = quotes.get(symbol)
        if quote is None or quote.price <= 0 or quote.prev_close <= 0:
            continue
        indices.append(MarketIndexQuote(
            symbol=symbol, name=name, price=quote.price,
            change_pct=quote.price / quote.prev_close - 1.0,
            amount=quote.amount,
        ))

    stocks = []
    for symbol, quote in quotes.items():
        if symbol in MARKET_INDICES or quote.suspended:
            continue
        code = symbol.partition(".")[0]
        if board_of(code) not in {"main", "gem"}:
            continue
        if quote.price <= 0 or quote.prev_close <= 0:
            continue
        stocks.append(quote)

    returns = [q.price / q.prev_close - 1.0 for q in stocks]
    advance = sum(value > 0 for value in returns)
    decline = sum(value < 0 for value in returns)
    flat = len(returns) - advance - decline
    limits = [(_live_limits(q), q) for q in stocks]
    limit_up = sum(q.price >= upper - 1e-6 for (upper, _), q in limits)
    limit_down = sum(q.price <= lower + 1e-6 for (_, lower), q in limits)
    touched = sum(q.high >= upper - 1e-6 for (upper, _), q in limits)
    broken = sum(
        q.high >= upper - 1e-6 and q.price < upper - 1e-6
        for (upper, _), q in limits
    )
    directional = advance + decline
    return MarketOverviewSnapshot(
        session=published_at.date(), published_at=published_at, indices=indices,
        valid_stock_count=len(stocks), advance_count=advance,
        decline_count=decline, flat_count=flat, limit_up_count=limit_up,
        limit_down_count=limit_down, touched_limit_up_count=touched,
        broken_board_count=broken,
        broken_board_rate=broken / touched if touched else 0.0,
        market_advance_ratio=advance / directional if directional else 0.0,
        market_median_return_1d=median(returns) if returns else 0.0,
        market_total_money=sum(q.amount for q in stocks),
    )


def _live_limits(quote: MarketQuote) -> tuple[float, float]:
    """Use direct QMT limits when available, otherwise apply board fallback.

    ``get_full_tick`` on the deployed MiniQMT omits limit fields.  The live
    breadth universe only contains main-board and ChiNext stocks, whose current
    fallback tiers are defined centrally in ``PRICE_LIMITS``.
    """
    board = board_of(quote.symbol.partition(".")[0])
    rate = Decimal(str(PRICE_LIMITS[board]))
    previous = Decimal(str(quote.prev_close))
    cent = Decimal("0.01")
    upper = quote.upper_limit
    lower = quote.lower_limit
    if upper is None:
        upper = float((previous * (Decimal("1") + rate)).quantize(
            cent, rounding=ROUND_HALF_UP
        ))
    if lower is None:
        lower = float((previous * (Decimal("1") - rate)).quantize(
            cent, rounding=ROUND_HALF_UP
        ))
    return upper, lower
