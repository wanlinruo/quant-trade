"""Request/response bridge between paper workers and the Windows QMT gateway."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, time as dt_time
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

    def request(self, symbols: Sequence[str]) -> dict[str, MarketQuote]:
        unique = sorted(set(symbols))
        if not unique:
            return {}
        # This stream fans every client's responses out to every consumer
        # group.  A long-idle/restarted client must not spend its five-second
        # request budget replaying thousands of responses that can no longer
        # belong to an outstanding call.  Reset before publishing our request,
        # so only responses created after this point are eligible.
        self._t.reset_group(self._s.quote_snapshots, self._group)
        request_id = uuid.uuid4().hex
        self._t.append(self._s.quote_requests, QuoteRequest(
            request_id=request_id, symbols=unique, requested_at=datetime.now()
        ).to_payload())
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            for message in self._t.read_group(
                self._s.quote_snapshots, self._group, self._consumer, count=100
            ):
                self._t.ack(self._s.quote_snapshots, self._group, message.id)
                snapshot = QuoteSnapshot.from_payload(message.payload)
                if snapshot.request_id == request_id:
                    return self._unwrap(snapshot)
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
        # 当日 stk_limit 与 QMT 的核对进度：(交易日, 待核对标的, 已核对结果)
        self._limit_check: tuple[date, list[str], list[dict]] | None = None
        self._limit_checked: date | None = None

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

    def check_limits_step(
        self, detail: Callable[[Sequence[str]], Mapping[str, tuple]], day: date,
        *, batch: int = 500,
    ) -> bool:
        """把盘前发布的 Tushare stk_limit 与 QMT 合约详情逐只核对，每次只做一批。

        分批是为了不卡住行情请求：`get_instrument_detail` 实测约 0.6 ms/只，
        全市场一次做完约 3 秒，逼近模拟盘的 5 秒行情超时。QMT 只作核对，
        不替代直取价（D-19）。返回当日核对是否已完成。
        """
        if self._limit_checked == day:
            return True
        if self._limit_check is None or self._limit_check[0] != day:
            raw = self._t.get_key(f"{self._s.price_limits}:{day:%Y%m%d}")
            if not raw:
                return False
            published = json.loads(raw)
            self._limit_check = (day, sorted(published), [])
            self._limit_published = published
        _, pending, results = self._limit_check
        chunk, pending[:] = pending[:batch], pending[batch:]
        qmt = detail(chunk)
        for symbol in chunk:
            up, down = self._limit_published[symbol]
            got = qmt.get(symbol)
            results.append({"symbol": symbol, "tushare": [up, down],
                            "qmt": list(got) if got else None})
        if pending:
            return False
        missing = [r for r in results if r["qmt"] is None or None in r["qmt"]]
        mismatched = [
            r for r in results if r not in missing
            and (abs(r["qmt"][0] - r["tushare"][0]) > 1e-6 or abs(r["qmt"][1] - r["tushare"][1]) > 1e-6)
        ]
        report = {"session": str(day), "checked_at": datetime.now().isoformat(timespec="seconds"),
                  "checked": len(results), "missing_in_qmt": len(missing),
                  "mismatched": len(mismatched), "samples": mismatched[:20]}
        self._t.set_key(f"{self._s.price_limits}_check:{day:%Y%m%d}", json.dumps(report))
        self._limit_checked, self._limit_check = day, None
        return True

    def beat(self, *, expected_interval_s: float | None = None, phase: str | None = None) -> None:
        self._t.set_key(
            f"{self._s.heartbeat}:market",
            Heartbeat(
                source="market", at=datetime.now(),
                expected_interval_s=expected_interval_s, phase=phase,
            ).model_dump_json(),
        )

    def publish_overview(
        self, quotes: dict[str, MarketQuote], *, now: datetime | None = None,
    ) -> MarketOverviewSnapshot:
        moment = now or datetime.now()
        raw = self._t.get_key(f"{self._s.price_limits}:{moment:%Y%m%d}")
        limits = json.loads(raw) if raw else {}
        snapshot = build_market_overview(quotes, limits=limits, now=moment)
        self._t.set_key(self._s.market_snapshot, snapshot.model_dump_json())
        return snapshot

    def publish_latest_quotes(
        self, quotes: dict[str, MarketQuote], *, now: datetime | None = None,
    ) -> QuoteSnapshot:
        """原子覆盖稳定实时快照；空响应不覆盖最后一份可审计状态。"""
        if not quotes:
            raise QuoteUnavailable("QMT 最新行情为空，保留上一快照")
        moment = now or datetime.now()
        snapshot = QuoteSnapshot(
            request_id="latest", published_at=moment, quotes=quotes,
        )
        self._t.set_key(self._s.quote_latest, snapshot.model_dump_json())
        return snapshot

    def publish_schedule(self, schedule: MarketSchedule) -> None:
        self._t.set_key(self._s.market_schedule, schedule.model_dump_json())


def build_market_schedule(
    now: datetime, sessions: Sequence[date],
) -> MarketSchedule:
    """Return the market-overview refresh window and its next useful start."""
    trading_days = sorted(set(sessions))
    today = now.date()
    is_trading_day = today in trading_days
    # Start before call auction so operators can confirm that the full market
    # feed is ready, and keep refreshing briefly after the close so the final
    # 15:00 ticks are guaranteed to replace the last intraday snapshot.
    morning = (datetime.combine(today, dt_time(9, 0)),
               datetime.combine(today, dt_time(11, 30)))
    afternoon = (datetime.combine(today, dt_time(13, 0)),
                 datetime.combine(today, dt_time(15, 30)))
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
                next_open = datetime.combine(next_day, dt_time(9, 0))
    return MarketSchedule(
        checked_at=now, is_trading_day=is_trading_day,
        is_open=is_open, next_open_at=next_open,
    )


def build_market_overview(
    quotes: dict[str, MarketQuote], *,
    limits: Mapping[str, Sequence[float]] | None = None,
    now: datetime | None = None,
) -> MarketOverviewSnapshot:
    """Aggregate one QMT full-market tick without persisting intraday facts.

    涨跌停价只认 `limits`——盘前发布的当日 Tushare stk_limit（D-19）。
    QMT get_full_tick 不给涨跌停价；缺直取价的标的不计入涨停/跌停/触板/炸板，
    只计入 `limit_price_missing_count`，不按板块档位自算。
    """
    published_at = now or datetime.now()
    limits = limits or {}
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
        if quote.price <= 0 or quote.prev_close <= 0:
            continue
        stocks.append(quote)

    returns = [q.price / q.prev_close - 1.0 for q in stocks]
    advance = sum(value > 0 for value in returns)
    decline = sum(value < 0 for value in returns)
    flat = len(returns) - advance - decline
    priced = [((float(limits[q.symbol][0]), float(limits[q.symbol][1])), q)
              for q in stocks if q.symbol in limits]
    # 价格落在涨停价但卖一仍有余量，不是封板（002631.SZ 2026-09-18）。
    # QMT 未提供盘口量时字段默认 0，此时仍退化为价格口径；一旦有五档数据，
    # 封板必须同时满足卖一无待成交量。跌停对称要求买一无待成交量。
    sealed_up = [q.price >= upper - 1e-6 and q.ask1_volume <= 0
                 for (upper, _), q in priced]
    sealed_down = [q.price <= lower + 1e-6 and q.bid1_volume <= 0
                   for (_, lower), q in priced]
    limit_up = sum(sealed_up)
    limit_down = sum(sealed_down)
    touched = sum(q.high >= upper - 1e-6 for (upper, _), q in priced)
    broken = sum(
        q.high >= upper - 1e-6 and not is_sealed
        for ((upper, _), q), is_sealed in zip(priced, sealed_up, strict=True)
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
        limit_price_missing_count=len(stocks) - len(priced),
    )
