from datetime import datetime, timedelta

from qsys.live.messages import Heartbeat
from scripts.live_preflight import agent_live_posture, heartbeat_freshness


NOW = datetime(2026, 9, 20, 12)


def test_off_hours_market_heartbeat_uses_declared_cadence():
    beat = Heartbeat(
        source="market", at=NOW - timedelta(seconds=29),
        expected_interval_s=30, phase="off_hours",
    )

    assert heartbeat_freshness(beat, NOW) == (True, 29.0, 90.0)
    assert heartbeat_freshness(
        beat.model_copy(update={"at": NOW - timedelta(seconds=91)}), NOW
    )[0] is False


def test_active_heartbeat_keeps_strict_ten_second_threshold():
    beat = Heartbeat(
        source="market", at=NOW - timedelta(seconds=10),
        expected_interval_s=1, phase="morning",
    )

    assert heartbeat_freshness(beat, NOW) == (True, 10.0, 10.0)
    assert heartbeat_freshness(
        beat.model_copy(update={"at": NOW - timedelta(seconds=10.1)}), NOW
    )[0] is False


def test_heartbeat_rejects_missing_or_future_timestamp():
    assert heartbeat_freshness(None, NOW)[0] is False
    future = Heartbeat(source="agent", at=NOW + timedelta(seconds=1.1))
    assert heartbeat_freshness(future, NOW)[0] is False


def test_agent_live_posture_requires_windows_to_announce_every_guard():
    live = Heartbeat(
        source="agent", at=NOW, trading_mode="live", orders_enabled=True,
        account_protected=True, local_kill_engaged=False,
    )
    assert all(ok for ok, _ in agent_live_posture(live))

    shadow = live.model_copy(update={"trading_mode": "shadow"})
    results = agent_live_posture(shadow)
    assert results[0][0] is False
    assert all(ok for ok, _ in results[1:])
    assert not any(ok for ok, _ in agent_live_posture(None))


def test_agent_live_posture_accepts_reported_engaged_kill_for_safe_service_start():
    engaged = Heartbeat(
        source="agent", at=NOW, trading_mode="live", orders_enabled=True,
        account_protected=True, local_kill_engaged=True,
    )

    checks = agent_live_posture(engaged)

    assert all(ok for ok, _ in checks)
    assert "已生效" in checks[-1][1]
    strict_checks = agent_live_posture(engaged, require_kill_released=True)
    assert strict_checks[-1] == (False, "Windows agent 本地停机闸未生效")


def test_agent_live_posture_rejects_unknown_kill_state():
    unknown = Heartbeat(
        source="agent", at=NOW, trading_mode="live", orders_enabled=True,
        account_protected=True, local_kill_engaged=None,
    )

    assert agent_live_posture(unknown)[-1][0] is False


# ---- #651：全市场快照带盘口比例 ----
from qsys.live.messages import MarketQuote, QuoteSnapshot
from qsys.live.preflight_cli import order_book_coverage

SESSION = datetime(2026, 11, 12, 13, 44, 40)


def _quote(symbol, *, bid=10.0, ask=10.01, price=10.0, suspended=False):
    return MarketQuote(symbol=symbol, at=SESSION, price=price, open=price, high=price,
                       low=price, prev_close=price, bid1=bid, ask1=ask, suspended=suspended)


def _snapshot(quotes, at=SESSION):
    return QuoteSnapshot(request_id="latest", published_at=at,
                         quotes={q.symbol: q for q in quotes})


def test_order_book_coverage_passes_when_most_quotes_carry_a_book():
    quotes = [_quote(f"{i:06d}.SZ") for i in range(95)] + \
             [_quote(f"9{i:05d}.SZ", bid=0, ask=0) for i in range(5)]
    status, label = order_book_coverage(_snapshot(quotes), SESSION + timedelta(seconds=5))
    assert status is True and "95/100" in label


def test_order_book_coverage_fails_when_full_push_lost_the_book():
    """11-12 实况：全推为「最新价」时全部盘口为 0，必须失败。"""
    quotes = [_quote(f"{i:06d}.SZ", bid=0, ask=0) for i in range(100)]
    status, label = order_book_coverage(_snapshot(quotes), SESSION + timedelta(seconds=5))
    assert status is False and "0/100" in label


def test_one_sided_limit_books_count_as_carrying_a_book():
    sealed = [_quote(f"{i:06d}.SZ", ask=0) for i in range(10)]  # 涨停只剩买盘
    status, _ = order_book_coverage(_snapshot(sealed), SESSION + timedelta(seconds=5))
    assert status is True


def test_suspended_and_unpriced_quotes_are_excluded_from_the_denominator():
    quotes = [_quote("000001.SZ"), _quote("000002.SZ", bid=0, ask=0, suspended=True),
              _quote("000003.SZ", bid=0, ask=0, price=0)]
    status, label = order_book_coverage(_snapshot(quotes), SESSION + timedelta(seconds=5))
    assert status is True and "1/1" in label


def test_order_book_coverage_is_not_applicable_outside_continuous_trading():
    quotes = [_quote(f"{i:06d}.SZ", bid=0, ask=0) for i in range(10)]
    for at in (datetime(2026, 11, 12, 9, 20), datetime(2026, 11, 12, 12, 0),
               datetime(2026, 11, 12, 15, 29, 56)):
        status, label = order_book_coverage(_snapshot(quotes, at), at + timedelta(seconds=5))
        assert status is None and "不适用" in label


def test_order_book_coverage_is_not_applicable_for_a_stale_or_missing_snapshot():
    quotes = [_quote(f"{i:06d}.SZ", bid=0, ask=0) for i in range(10)]
    status, label = order_book_coverage(_snapshot(quotes), SESSION + timedelta(minutes=5))
    assert status is None and "不适用" in label
    status, label = order_book_coverage(None, SESSION)
    assert status is None and "不适用" in label
