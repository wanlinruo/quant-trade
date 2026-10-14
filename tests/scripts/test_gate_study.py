from datetime import date, time

import polars as pl
import pytest

from scripts import gate_study as study

SESSIONS = [date(2026, 3, d) for d in (2, 3, 4, 5, 6)]


class _Quote:
    def __init__(self, price):
        self.price = price


class _Bound:
    def __init__(self, prices, limit_up=None):
        self._prices, self._limit_up = prices, limit_up

    def intraday(self, symbols, day, at):
        price = self._prices.get((day, at))
        return {symbols[0]: _Quote(price)} if price else {}

    def limit_up_price(self, symbols, day):
        return {symbols[0]: self._limit_up} if self._limit_up else {}


class _Market:
    def __init__(self, bound):
        self._bound = bound

    def bind(self, at):
        return self._bound


def test_a_high_branch_lot_takes_profit_at_the_first_checkpoint():
    prices = {(SESSIONS[1], time(11, 25)): 11.0}
    market = _Market(_Bound(prices))
    got = study.replay(market, {}, SESSIONS, "600001.SH", "HIGH_MAIN",
                       SESSIONS[0], 10.0, horizon=3)
    assert got["reason"] == "TAKE_PROFIT"
    assert got["return_pct"] == pytest.approx(10.0)
    assert got["exit_day"] == SESSIONS[1]


def test_a_low_branch_lot_exits_unconditionally_at_1425():
    prices = {(SESSIONS[1], time(11, 25)): 9.5, (SESSIONS[1], time(14, 25)): 9.6}
    market = _Market(_Bound(prices))
    got = study.replay(market, {}, SESSIONS, "600001.SH", "LOW_MAIN",
                       SESSIONS[0], 10.0, horizon=3)
    assert got["exit_at"] == "14:25:00"
    assert got["return_pct"] == pytest.approx(-4.0)


def test_without_any_quote_the_horizon_close_is_used_and_labelled():
    closes = {("600001.SH", SESSIONS[2]): 10.5}
    market = _Market(_Bound({}))
    got = study.replay(market, closes, SESSIONS, "600001.SH", "HIGH_MAIN",
                       SESSIONS[0], 10.0, horizon=2)
    assert got["reason"] == "HORIZON"
    assert got["return_pct"] == pytest.approx(5.0)


def test_a_lot_with_no_price_at_all_is_not_scored():
    market = _Market(_Bound({}))
    got = study.replay(market, {}, SESSIONS, "600001.SH", "HIGH_MAIN",
                       SESSIONS[0], 10.0, horizon=2)
    assert got["reason"] == "NO_PRICE" and got["return_pct"] is None


def test_incomplete_followup_is_right_censored_not_horizon_scored():
    closes = {("600001.SH", SESSIONS[-1]): 12.0}
    market = _Market(_Bound({}))
    got = study.replay(
        market, closes, SESSIONS, "600001.SH", "HIGH_MAIN",
        SESSIONS[-2], 10.0, horizon=3,
    )
    assert got["reason"] == "INCOMPLETE_FOLLOWUP"
    assert got["return_pct"] is None
    assert got["outcome_quality"] == "right_censored"


class _BatchBound:
    def __init__(self, prices):
        self._prices = prices

    def intraday(self, symbols, day, at):
        return {
            symbol: _Quote(self._prices[(symbol, day, at)])
            for symbol in symbols if (symbol, day, at) in self._prices
        }

    def limit_up_price(self, symbols, day):
        return {}


def test_batch_replay_preserves_independent_trade_outcomes():
    prices = {
        ("600001.SH", SESSIONS[1], time(11, 25)): 11.0,
        ("600002.SH", SESSIONS[1], time(11, 25)): 9.5,
        ("600002.SH", SESSIONS[1], time(14, 25)): 9.6,
    }
    requests = [
        {"id": 0, "symbol": "600001.SH", "tag": "HIGH_MAIN",
         "entry_day": SESSIONS[0], "entry_price": 10.0},
        {"id": 1, "symbol": "600002.SH", "tag": "LOW_MAIN",
         "entry_day": SESSIONS[0], "entry_price": 10.0},
    ]
    got = study.replay_many(
        _Market(_BatchBound(prices)), {}, SESSIONS, requests, horizon=3
    )
    assert got[0]["return_pct"] == pytest.approx(10.0)
    assert got[0]["exit_at"] == "11:25:00"
    assert got[1]["return_pct"] == pytest.approx(-4.0)
    assert got[1]["exit_at"] == "14:25:00"


def test_describe_summarises_returns():
    got = study.describe([-2.0, 1.0, 3.0])
    assert got["n"] == 3 and got["win_rate"] == pytest.approx(0.667, abs=1e-3)
    assert study.describe([])["n"] == 0
