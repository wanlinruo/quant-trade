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


def test_describe_summarises_returns():
    got = study.describe([-2.0, 1.0, 3.0])
    assert got["n"] == 3 and got["win_rate"] == pytest.approx(0.667, abs=1e-3)
    assert study.describe([])["n"] == 0
