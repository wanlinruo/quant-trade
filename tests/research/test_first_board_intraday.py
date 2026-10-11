from datetime import date, datetime, time
from pathlib import Path

import polars as pl

from qsys.research.first_board_intraday import (
    FirstMinuteResearchStrategy,
    FrozenSelectionBook,
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
