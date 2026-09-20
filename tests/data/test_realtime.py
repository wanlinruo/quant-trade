from datetime import datetime, timedelta

import pytest

from qsys.data.realtime import RealtimeMarketState, RealtimeUnavailable
from qsys.live.messages import MarketQuote
from qsys.live.config import StreamConfig
from qsys.live.messages import QuoteSnapshot
from qsys.live.transport import MemoryTransport
from qsys.data.realtime import load_latest_snapshot


SYMBOL = "600036.SH"


def _quote(at: datetime, price: float, volume: int, amount: float) -> MarketQuote:
    return MarketQuote(
        symbol=SYMBOL, at=at, price=price, open=10.0,
        high=max(10.0, price), low=min(10.0, price), prev_close=9.8,
        volume=volume, amount=amount,
    )


def test_latest_and_daily_contract_expose_age_source_and_normalized_volume():
    state = RealtimeMarketState()
    at = datetime(2026, 9, 21, 10, 0, 10)
    state.ingest({SYMBOL: _quote(at, 10.2, 12_300, 125_000)}, received_at=at)
    latest = state.latest([SYMBOL], as_of=at + timedelta(seconds=2))[SYMBOL]
    assert latest.source == "qmt" and latest.age == timedelta(seconds=2)
    daily = state.daily_bars([SYMBOL], as_of=at + timedelta(seconds=2))[0]
    assert daily.close == 10.2 and daily.volume == 123.0


def test_stale_missing_empty_and_time_reversing_quotes_fail_explicitly():
    state = RealtimeMarketState()
    at = datetime(2026, 9, 21, 10, 0, 10)
    with pytest.raises(RealtimeUnavailable, match="为空"):
        state.ingest({}, received_at=at)
    state.ingest({SYMBOL: _quote(at, 10.0, 100, 1_000)}, received_at=at)
    with pytest.raises(RealtimeUnavailable, match="陈旧"):
        state.latest([SYMBOL], as_of=at + timedelta(seconds=6))
    with pytest.raises(RealtimeUnavailable, match="没有实时行情"):
        state.latest(["000001.SZ"], as_of=at)
    with pytest.raises(RealtimeUnavailable, match="未递增"):
        state.ingest({SYMBOL: _quote(at, 10.1, 200, 2_000)}, received_at=at)


def test_current_minute_never_masquerades_as_finalized():
    state = RealtimeMarketState()
    first = datetime(2026, 9, 21, 10, 0, 10)
    second = datetime(2026, 9, 21, 10, 0, 30)
    state.ingest({SYMBOL: _quote(first, 10.0, 100, 1_000)}, received_at=first)
    state.ingest({SYMBOL: _quote(second, 10.2, 300, 3_040)}, received_at=second)
    assert state.minute_bars([SYMBOL], as_of=second) == []
    current = state.minute_bars([SYMBOL], as_of=second, include_current=True)[0]
    assert current.finalized is False and current.partial is True


def test_completed_minute_is_published_only_after_its_close_and_full_observation():
    state = RealtimeMarketState()
    ticks = [
        (datetime(2026, 9, 21, 10, 0, 50), 10.0, 100, 1_000),
        (datetime(2026, 9, 21, 10, 1, 5), 10.1, 200, 2_010),
        (datetime(2026, 9, 21, 10, 1, 30), 10.3, 500, 5_100),
    ]
    for at, price, volume, amount in ticks:
        state.ingest({SYMBOL: _quote(at, price, volume, amount)}, received_at=at)
    before = datetime(2026, 9, 21, 10, 1, 59)
    assert state.minute_bars([SYMBOL], as_of=before) == []
    bars = state.minute_bars([SYMBOL], as_of=datetime(2026, 9, 21, 10, 2))[0]
    assert bars.finalized is True and bars.partial is False
    assert (bars.open, bars.high, bars.low, bars.close) == (10.1, 10.3, 10.1, 10.3)
    assert bars.volume == 4.0


def test_cross_day_first_tick_starts_partial_instead_of_using_yesterday_cumulative():
    state = RealtimeMarketState()
    yesterday = datetime(2026, 9, 20, 15, 0)
    today = datetime(2026, 9, 21, 9, 30)
    state.ingest({SYMBOL: _quote(yesterday, 10.0, 1_000, 10_000)}, received_at=yesterday)
    state.ingest({SYMBOL: _quote(today, 10.1, 100, 1_010)}, received_at=today)
    bar = state.minute_bars([SYMBOL], as_of=today, include_current=True)[0]
    assert bar.partial is True and bar.volume == 0


def test_latest_redis_snapshot_requires_present_valid_and_fresh_payload():
    transport, streams = MemoryTransport(), StreamConfig()
    now = datetime(2026, 9, 21, 10)
    with pytest.raises(RealtimeUnavailable, match="不存在"):
        load_latest_snapshot(transport, streams, as_of=now)
    transport.set_key(streams.quote_latest, "broken")
    with pytest.raises(RealtimeUnavailable, match="损坏"):
        load_latest_snapshot(transport, streams, as_of=now)
    snapshot = QuoteSnapshot(
        request_id="latest", published_at=now,
        quotes={SYMBOL: _quote(now, 10.0, 100, 1_000)},
    )
    transport.set_key(streams.quote_latest, snapshot.model_dump_json())
    assert load_latest_snapshot(transport, streams, as_of=now).quotes[SYMBOL].price == 10
    with pytest.raises(RealtimeUnavailable, match="陈旧"):
        load_latest_snapshot(transport, streams, as_of=now + timedelta(seconds=6))
