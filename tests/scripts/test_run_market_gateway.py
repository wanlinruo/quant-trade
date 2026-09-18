from datetime import date, datetime

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
