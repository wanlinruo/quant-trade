"""#651：全市场快照盘口覆盖的共享判定。"""

from datetime import datetime

from qsys.live.messages import MarketQuote
from qsys.live.order_book import in_continuous_session, order_book_counts

AT = datetime(2026, 11, 12, 13, 44, 40)


def _q(symbol, *, bid=10.0, ask=10.01, price=10.0, suspended=False):
    return MarketQuote(symbol=symbol, at=AT, price=price, open=price, high=price, low=price,
                       prev_close=price, bid1=bid, ask1=ask, suspended=suspended)


def test_counts_exclude_indices_suspended_and_unpriced():
    quotes = {q.symbol: q for q in (
        _q("600000.SH"), _q("600001.SH", ask=0), _q("600002.SH", bid=0, ask=0),
        _q("600003.SH", bid=0, ask=0, suspended=True), _q("600004.SH", price=0, bid=0, ask=0),
        _q("000300.SH", bid=0, ask=0),
    )}
    assert order_book_counts(quotes) == (2, 3)


def test_continuous_session_bounds():
    day = datetime(2026, 11, 12)
    assert in_continuous_session(day.replace(hour=9, minute=30))
    assert in_continuous_session(day.replace(hour=14, minute=59, second=59))
    assert not in_continuous_session(day.replace(hour=9, minute=25))
    assert not in_continuous_session(day.replace(hour=12))
    assert not in_continuous_session(day.replace(hour=15, minute=0, second=1))


# ---- 观察器用的当日黏性判定 ----
from datetime import timedelta

from qsys.live.order_book import OrderBookWatch


def test_watch_flags_low_ratio_during_session_and_holds_it_after_close():
    w = OrderBookWatch()
    at = datetime(2026, 11, 12, 10, 30)
    low, detail = w.update(0.0, at, at + timedelta(seconds=10))
    assert low is True and "0.0%" in detail
    # 收盘后快照不适用：保持当日最后一次有效判定，不制造“恢复”
    close = datetime(2026, 11, 12, 15, 29, 56)
    assert w.update(0.0, close, close + timedelta(seconds=10))[0] is True


def test_watch_recovers_only_on_a_fresh_in_session_measurement():
    w = OrderBookWatch()
    at = datetime(2026, 11, 12, 13, 40)
    w.update(0.0, at, at)
    assert w.update(0.99, at + timedelta(minutes=5), at + timedelta(minutes=5))[0] is False


def test_watch_ignores_stale_or_missing_measurements_and_resets_next_day():
    w = OrderBookWatch()
    at = datetime(2026, 11, 12, 10, 30)
    assert w.update(None, None, at)[0] is None
    assert w.update(0.0, at, at + timedelta(minutes=10))[0] is None  # 心跳里的快照已陈旧
    w.update(0.0, at, at)
    next_morning = datetime(2026, 11, 13, 9, 0)
    assert w.update(0.0, at, next_morning)[0] is None
