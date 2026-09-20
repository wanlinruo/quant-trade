"""实时行情逻辑契约。

实时状态不写历史仓库；只有显式收盘发布流程通过门禁后才允许进入规范表。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from qsys.live.messages import MarketQuote
from qsys.live.config import StreamConfig
from qsys.live.messages import QuoteSnapshot
from qsys.live.transport import Transport


class RealtimeUnavailable(RuntimeError):
    """实时来源缺失、陈旧或时间倒退，调用方必须明确失败。"""


@dataclass(frozen=True, slots=True)
class LatestQuote:
    symbol: str
    session: date
    market_at: datetime
    received_at: datetime
    source: str
    price: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: int
    amount: float
    age: timedelta


@dataclass(frozen=True, slots=True)
class RealtimeDailyBar:
    symbol: str
    dt: date
    open: float
    high: float
    low: float
    close: float
    prev_close: float
    volume: float
    amount: float
    source: str
    as_of: datetime


@dataclass(frozen=True, slots=True)
class RealtimeMinuteBar:
    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    source: str
    visible_at: datetime
    finalized: bool
    partial: bool


@dataclass(slots=True)
class _MinuteAccumulator:
    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    visible_at: datetime
    partial: bool


class RealtimeMarketState:
    """由递增的 QMT 累计快照维护实时逻辑表；不提供历史回退。"""

    def __init__(self, *, source: str = "qmt", max_tick_gap: timedelta = timedelta(seconds=45)) -> None:
        self._source = source
        self._max_tick_gap = max_tick_gap
        self._latest: dict[str, tuple[MarketQuote, datetime]] = {}
        self._minutes: dict[tuple[str, datetime], _MinuteAccumulator] = {}

    @staticmethod
    def _minute_end(moment: datetime) -> datetime:
        return moment.replace(second=0, microsecond=0) + timedelta(minutes=1)

    def ingest(self, quotes: dict[str, MarketQuote], *, received_at: datetime) -> None:
        if not quotes:
            raise RealtimeUnavailable("实时行情响应为空")
        for symbol, quote in quotes.items():
            if quote.symbol != symbol:
                raise ValueError(f"行情键与 symbol 不一致：{symbol} != {quote.symbol}")
            if quote.at > received_at:
                raise RealtimeUnavailable(f"{symbol} 行情时间晚于接收时间")
            previous = self._latest.get(symbol)
            if previous is not None and quote.at <= previous[0].at:
                raise RealtimeUnavailable(f"{symbol} 行情时间未递增")
            if previous is not None and quote.at.date() != previous[0].at.date():
                previous = None

            key = (symbol, self._minute_end(quote.at))
            prior_volume = previous[0].volume if previous is not None else quote.volume
            prior_amount = previous[0].amount if previous is not None else quote.amount
            delta_volume = quote.volume - prior_volume
            delta_amount = quote.amount - prior_amount
            if delta_volume < 0 or delta_amount < -1e-6:
                raise RealtimeUnavailable(f"{symbol} 累计量额倒退")
            partial = previous is None or quote.at - previous[0].at > self._max_tick_gap
            bar = self._minutes.get(key)
            if bar is None:
                self._minutes[key] = _MinuteAccumulator(
                    symbol, key[1], quote.price, quote.price, quote.price, quote.price,
                    delta_volume / 100.0, delta_amount, received_at, partial,
                )
            else:
                bar.high = max(bar.high, quote.price)
                bar.low = min(bar.low, quote.price)
                bar.close = quote.price
                bar.volume += delta_volume / 100.0
                bar.amount += delta_amount
                bar.visible_at = received_at
                bar.partial = bar.partial or partial
            self._latest[symbol] = (quote, received_at)

    def latest(
        self, symbols: list[str] | None, *, as_of: datetime,
        max_age: timedelta = timedelta(seconds=5),
    ) -> dict[str, LatestQuote]:
        selected = sorted(self._latest) if symbols is None else symbols
        out: dict[str, LatestQuote] = {}
        for symbol in selected:
            item = self._latest.get(symbol)
            if item is None:
                raise RealtimeUnavailable(f"{symbol} 没有实时行情")
            quote, received_at = item
            age = as_of - quote.at
            if age < timedelta(0):
                raise RealtimeUnavailable(f"{symbol} 行情晚于 as_of")
            if age > max_age:
                raise RealtimeUnavailable(f"{symbol} 行情陈旧：{age.total_seconds():.3f}s")
            out[symbol] = LatestQuote(
                symbol, quote.at.date(), quote.at, received_at, self._source,
                quote.price, quote.open, quote.high, quote.low, quote.prev_close,
                quote.volume, quote.amount, age,
            )
        return out

    def daily_bars(
        self, symbols: list[str] | None, *, as_of: datetime,
        max_age: timedelta = timedelta(seconds=5),
    ) -> list[RealtimeDailyBar]:
        return [
            RealtimeDailyBar(
                row.symbol, row.session, row.open, row.high, row.low, row.price,
                row.prev_close, row.volume / 100.0, row.amount, row.source, as_of,
            )
            for row in self.latest(symbols, as_of=as_of, max_age=max_age).values()
        ]

    def minute_bars(
        self, symbols: list[str] | None, *, as_of: datetime, include_current: bool = False,
    ) -> list[RealtimeMinuteBar]:
        selected = set(self._latest) if symbols is None else set(symbols)
        out: list[RealtimeMinuteBar] = []
        for (symbol, _), bar in sorted(self._minutes.items()):
            if symbol not in selected or bar.ts.date() != as_of.date():
                continue
            finalized = bar.ts <= as_of and not bar.partial
            if not finalized and not include_current:
                continue
            out.append(RealtimeMinuteBar(
                bar.symbol, bar.ts, bar.open, bar.high, bar.low, bar.close,
                bar.volume, bar.amount, self._source, bar.visible_at,
                finalized, bar.partial,
            ))
        return out


def load_latest_snapshot(
    transport: Transport, streams: StreamConfig, *, as_of: datetime,
    max_age: timedelta = timedelta(seconds=5),
) -> QuoteSnapshot:
    """读取 Redis 原子最新快照；缺失、错误、陈旧均明确失败。"""
    raw = transport.get_key(streams.quote_latest)
    if raw is None:
        raise RealtimeUnavailable("实时行情快照不存在")
    try:
        snapshot = QuoteSnapshot.model_validate_json(raw)
    except Exception as exc:
        raise RealtimeUnavailable("实时行情快照损坏") from exc
    if snapshot.error:
        raise RealtimeUnavailable(f"实时行情快照错误：{snapshot.error}")
    if not snapshot.quotes:
        raise RealtimeUnavailable("实时行情快照为空")
    age = as_of - snapshot.published_at
    if age < timedelta(0) or age > max_age:
        raise RealtimeUnavailable(f"实时行情快照陈旧或晚于 as_of：{age.total_seconds():.3f}s")
    return snapshot
