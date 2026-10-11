from datetime import date, datetime, time
from pathlib import Path

import polars as pl

from qsys.core.trading import Side
from qsys.research.first_board_intraday import (
    FirstMinuteResearchStrategy,
    FrozenSelectionBook,
    TWO_STAGE_EXIT,
    daily_select,
    visible_minute_pair,
)
from qsys.strategy.schedule import Schedule


DAY = date(2025, 7, 1)


def _minute(ts, visible, source="paid"):
    return {
        "symbol": "600001.SH", "ts": datetime.combine(DAY, ts),
        "visible_at": datetime.combine(DAY, visible), "source": source,
        "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.1,
        "volume": 100.0, "amount": 100_000.0,
    }


def test_visible_pair_uses_visibility_not_provider_timestamp():
    pair = visible_minute_pair([
        _minute(time(9, 30), time(9, 31)),
        _minute(time(9, 31), time(9, 32)),
        _minute(time(9, 32), time(9, 33)),
    ], DAY)
    assert pair.observation["ts"].time() == time(9, 30)
    assert pair.execution["ts"].time() == time(9, 31)


def test_visible_pair_skips_qmt_auction_bar_and_uses_next_bar_for_execution():
    pair = visible_minute_pair([
        _minute(time(9, 30), time(9, 30), "qmt"),
        _minute(time(9, 31), time(9, 31), "qmt"),
        _minute(time(9, 32), time(9, 32), "qmt"),
    ], DAY)
    assert pair.observation["ts"].time() == time(9, 31)
    assert pair.execution["ts"].time() == time(9, 32)


def test_threshold_selection_allows_zero_to_three_per_day():
    rows = []
    for offset, score in enumerate((0.9, 0.8, 0.7, 0.6)):
        rows.append({
            "target_session": DAY, "symbol": f"60000{offset}.SH",
            "rank": offset + 1, "gate_score": score, "invalid_reason": "",
        })
    later = date(2025, 7, 2)
    rows.append({
        "target_session": later, "symbol": "000001.SZ", "rank": 1,
        "gate_score": 0.2, "invalid_reason": "",
    })
    got = daily_select(pl.DataFrame(rows), policy="gate_threshold", threshold=0.65)
    assert got.filter(pl.col("target_session") == DAY).height == 3
    assert got.filter(pl.col("target_session") == later).height == 0
    assert got["selection_rank"].to_list() == [1, 2, 3]


def test_frozen_book_projects_no_outcomes_and_schedules_0931(tmp_path: Path):
    path = tmp_path / "selection.parquet"
    pl.DataFrame({
        "target_session": [DAY], "symbol": ["600001.SH"], "split": ["TEST"],
        "policy": ["gate_threshold"], "selection_rank": [1],
        "decision_price": [10.0], "limit_up": [11.0], "gate_score": [0.8],
        "actual_close": [True],
    }).write_parquet(path)
    book = FrozenSelectionBook(path, policy="gate_threshold", split="TEST")
    signal = book.for_target(DAY)[0]
    assert not hasattr(signal, "actual_close")
    schedule = Schedule()
    FirstMinuteResearchStrategy(book).on_init(
        type("Ctx", (), {"schedule": schedule})()
    )
    assert schedule.auction_at == time(9, 31)


class _Position:
    available_qty = 100


class _Portfolio:
    symbols = frozenset({"600001.SH"})

    def position(self, symbol):
        return _Position()


class _Order:
    def __init__(self):
        self.orders = []

    def submit(self, symbol, side, qty):
        self.orders.append((str(symbol), side, qty))


class _Quote:
    def __init__(self, price):
        self.price = price


class _Market:
    def __init__(self, price, limit=11.0):
        self.price = price
        self.limit = limit

    def intraday(self, symbols, day, at):
        return {"600001.SH": _Quote(self.price)}

    def limit_up_price(self, symbols, day):
        return {"600001.SH": self.limit}


class _Data:
    def get(self, symbols, start, end):
        return pl.DataFrame({
            "symbol": ["600001.SH"], "dt": [date(2025, 6, 30)],
            "close": [10.0],
        })


class _ExitCtx:
    def __init__(self, at, price, limit=11.0):
        self.now = datetime.combine(DAY, at)
        self.portfolio = _Portfolio()
        self.market = _Market(price, limit)
        self.data = _Data()
        self.order = _Order()
        self.notes = []

    def report(self, **kwargs):
        self.notes.append(kwargs)


def _two_stage_strategy():
    return FirstMinuteResearchStrategy(object(), exit_policy=TWO_STAGE_EXIT)


def test_two_stage_exit_schedules_both_checks():
    schedule = Schedule()
    _two_stage_strategy().on_init(type("Ctx", (), {"schedule": schedule})())
    assert schedule.checkpoints == (time(11, 25), time(14, 25))


def test_two_stage_exit_sells_red_non_limit_at_1125():
    ctx = _ExitCtx(time(11, 25), 10.01)
    _two_stage_strategy().on_checkpoint(ctx, time(11, 25))
    assert ctx.order.orders == [("600001.SH", Side.SELL, 100)]


def test_two_stage_exit_holds_flat_or_limit_at_1125():
    for price in (10.0, 11.0):
        ctx = _ExitCtx(time(11, 25), price)
        _two_stage_strategy().on_checkpoint(ctx, time(11, 25))
        assert ctx.order.orders == []


def test_two_stage_exit_sells_any_non_limit_at_1425_and_holds_limit():
    ctx = _ExitCtx(time(14, 25), 9.5)
    _two_stage_strategy().on_checkpoint(ctx, time(14, 25))
    assert len(ctx.order.orders) == 1
    ctx = _ExitCtx(time(14, 25), 11.0)
    _two_stage_strategy().on_checkpoint(ctx, time(14, 25))
    assert ctx.order.orders == []
