import sys
from datetime import date, datetime
from types import SimpleNamespace

from scripts import run_market_gateway
from scripts.run_market_gateway import TradingSessionCache


def test_calendar_retries_an_empty_midnight_result_and_recovers_before_open():
    today = date(2026, 9, 18)
    responses = iter([[], [today]])
    calls = []

    def provider(start, end):
        calls.append((start, end))
        return next(responses)

    cache = TradingSessionCache(refresh_interval=300)
    midnight = datetime(2026, 9, 18, 0, 0)

    assert cache.refresh_if_due(midnight, 0, provider) == []
    assert cache.refresh_if_due(datetime(2026, 9, 18, 0, 4), 240, provider) == []
    assert cache.refresh_if_due(datetime(2026, 9, 18, 0, 5), 300, provider) == [today]
    assert len(calls) == 2


def test_calendar_keeps_last_good_result_during_a_transient_empty_refresh():
    today = date(2026, 9, 18)
    responses = iter([[today], []])
    cache = TradingSessionCache(refresh_interval=300)

    assert cache.refresh_if_due(
        datetime(2026, 9, 18, 8, 55), 0, lambda *_: next(responses)
    ) == [today]
    assert cache.refresh_if_due(
        datetime(2026, 9, 18, 9, 0), 300, lambda *_: next(responses)
    ) == [today]


def test_calendar_clears_previous_day_before_refreshing_the_new_day():
    first = date(2026, 9, 18)
    cache = TradingSessionCache(refresh_interval=300)
    assert cache.refresh_if_due(
        datetime(2026, 9, 18, 14), 0, lambda *_: [first]
    ) == [first]

    assert cache.refresh_if_due(
        datetime(2026, 9, 19, 0), 36_000, lambda *_: []
    ) == []


def test_market_overview_unions_qmt_sector_with_premarket_universe(monkeypatch):
    sectors = []
    xtdata = SimpleNamespace(
        get_stock_list_in_sector=lambda name: sectors.append(name) or [
            "600001.SH", "688001.SH", "920001.BJ",
        ]
    )
    monkeypatch.setitem(sys.modules, "xtquant", SimpleNamespace(xtdata=xtdata))
    requested = []
    monkeypatch.setattr(
        run_market_gateway, "qmt_provider",
        lambda symbols: requested.extend(symbols) or {},
    )

    run_market_gateway.qmt_market_overview(["920298.BJ"])

    assert sectors == ["沪深京A股"]
    assert "688001.SH" in requested
    assert "920001.BJ" in requested
    assert "920298.BJ" in requested


def test_published_limit_symbols_supply_new_listings_missing_from_qmt_sector():
    transport = SimpleNamespace(get_key=lambda key: '{"920298.BJ": [90.48, 48.72]}')
    streams = SimpleNamespace(price_limits="state:price_limits")

    assert run_market_gateway.published_limit_symbols(
        transport, streams, date(2026, 9, 18)
    ) == ["920298.BJ"]
