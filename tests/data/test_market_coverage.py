from datetime import datetime, timedelta

import pytest

from qsys.live.messages import MarketOverviewSnapshot, MarketQuote
from qsys.paper.quotes import build_market_overview
from qsys.paper.quotes import MarketGateway, QuoteUnavailable
from qsys.live.config import StreamConfig
from qsys.live.transport import MemoryTransport

NOW = datetime(2026, 11, 3, 10)
SYMBOLS = ["600001.SH", "000920.SZ", "920026.BJ"]


def quotes():
    return {s: MarketQuote(symbol=s, at=NOW, price=11, open=10, high=11, low=10,
                           prev_close=10, volume=100, amount=1000) for s in SYMBOLS}


def snapshot(values, expected=SYMBOLS, session=NOW.date()):
    return build_market_overview(values, expected_symbols=expected,
                                universe_source="tushare.stk_limit",
                                universe_session=session, now=NOW)


def test_missing_beijing_quote_cannot_hide_in_a_smaller_breadth_denominator():
    values = quotes()
    values.pop("920026.BJ")
    result = snapshot(values)
    assert result.valid_stock_count == 2
    assert result.coverage.status == "incomplete"
    bj = result.coverage.by_market[2]
    assert (bj.expected, bj.received, bj.valid, bj.missing) == (1, 0, 0, 1)
    assert bj.missing_samples == ["920026.BJ"]
    assert bj.source_at_min is None


def test_all_markets_are_observed_without_applying_the_trading_pool():
    result = snapshot(quotes())
    assert result.valid_stock_count == 3
    assert result.coverage.status == "complete"
    assert [r.valid for r in result.coverage.by_market] == [1, 1, 1]
    assert result.coverage.universe_sha256 is not None


def test_missing_or_old_universe_binding_remains_unknown():
    assert snapshot(quotes(), expected=None).coverage.expected is None
    assert snapshot(quotes(), expected=None).coverage.status == "unknown"
    assert snapshot(quotes(), session=None).coverage.status == "unknown"
    assert snapshot(quotes(), session=NOW.date() - timedelta(days=1)).coverage.status == "unknown"


def test_old_producer_has_no_invented_historical_coverage():
    result = snapshot(quotes()).model_dump()
    result.pop("coverage")
    assert MarketOverviewSnapshot.model_validate(result).coverage is None


def test_other_session_invalid_identity_and_future_times_are_not_complete():
    for update in ({"at": NOW - timedelta(days=1)}, {"at": NOW + timedelta(seconds=1)},
                   {"price": float("nan")}, {"symbol": "600001.SH"}, {"suspended": True}):
        values = quotes()
        values["920026.BJ"] = values["920026.BJ"].model_copy(update=update)
        assert snapshot(values).coverage.status == "incomplete"
    assert snapshot(quotes(), expected=SYMBOLS + ["unknown"]).coverage.status == "incomplete"


def test_known_b_share_identity_is_explicitly_excluded_and_unexpected_quotes_not_aggregated():
    values = quotes()
    values["600002.SH"] = values["600001.SH"].model_copy(update={"symbol": "600002.SH"})
    result = snapshot(values, expected=SYMBOLS + ["900901.BJ"])
    assert result.valid_stock_count == 3
    assert result.coverage.excluded_non_a == 1
    assert result.coverage.unexpected_count == 1
    assert result.coverage.status == "incomplete"


def test_quiet_same_day_security_is_not_labeled_stale_from_lack_of_trades():
    values = quotes()
    values["920026.BJ"] = values["920026.BJ"].model_copy(update={"at": NOW - timedelta(minutes=20)})
    assert snapshot(values).coverage.status == "complete"
    assert snapshot(values).coverage.by_market[2].source_at_min == NOW - timedelta(minutes=20)


@pytest.mark.parametrize("bound,failure", [
    (True, "empty"), (True, "other_day"), (True, "invalid"), (True, "suspended"),
    (True, "indices_only"), (False, "empty"), (False, "invalid"),
    (False, "suspended"), (False, "indices_only"),
])
def test_no_valid_equities_cannot_refresh_the_last_market_snapshot(bound, failure):
    transport = MemoryTransport()
    streams = StreamConfig()
    gateway = MarketGateway(transport, streams, lambda symbols: {})
    gateway.publish_overview(quotes(), now=NOW, expected_symbols=SYMBOLS,
                             universe_source="tushare.stk_limit", universe_session=NOW.date())
    original = transport.get_key(streams.market_snapshot)
    updates = {"other_day": {"at": NOW - timedelta(days=1)},
               "invalid": {"prev_close": 0}, "suspended": {"suspended": True}}
    if failure == "empty":
        bad = {}
    elif failure == "indices_only":
        bad = {"000001.SH": quotes()["600001.SH"].model_copy(update={"symbol": "000001.SH"})}
    else:
        bad = {s: q.model_copy(update=updates[failure]) for s, q in quotes().items()}
    with pytest.raises(QuoteUnavailable):
        gateway.publish_overview(bad, now=NOW + timedelta(seconds=15),
                                 expected_symbols=SYMBOLS if bound else None,
                                 universe_source="tushare.stk_limit", universe_session=NOW.date())
    assert transport.get_key(streams.market_snapshot) == original
