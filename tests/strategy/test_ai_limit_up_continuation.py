from datetime import date, datetime, time
from pathlib import Path

import polars as pl
import pytest

from qsys.strategy.ai_limit_up_continuation import (
    AiLimitUpContinuationStrategy,
    ContinuationSelection,
    ContinuationSignalBook,
)
from qsys.strategy.schedule import Schedule


def _ranking(path: Path) -> Path:
    pl.DataFrame({
        "prediction_date": [date(2026, 9, 29)] * 4,
        "target_session": [date(2026, 9, 30)] * 4,
        "symbol": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"],
        "model": ["lightgbm"] * 4,
        "rank": [1, 2, 3, 4],
        "p_close": [0.7, 0.6, 0.5, 0.4],
        # Deliberately contradictory outcomes: the signal reader must not expose them.
        "actual_close": [False, False, False, True],
        "split": ["OBS"] * 4,
    }).write_parquet(path)
    return path


def test_signal_book_projects_only_frozen_signal_columns(tmp_path):
    book = ContinuationSignalBook(_ranking(tmp_path / "rank.parquet"), top_k=3)
    got = book.for_target(date(2026, 9, 30))
    assert [row.symbol for row in got] == ["000001.SZ", "000002.SZ", "000003.SZ"]
    assert [row.probability for row in got] == [0.7, 0.6, 0.5]
    assert not hasattr(got[0], "actual_close")


def test_signal_book_rejects_non_contiguous_ranking(tmp_path):
    path = _ranking(tmp_path / "rank.parquet")
    frame = pl.read_parquet(path).filter(pl.col("rank") != 2)
    frame.write_parquet(path)
    with pytest.raises(ValueError, match="not contiguous"):
        ContinuationSignalBook(path, top_k=3).for_target(date(2026, 9, 30))


def test_strategy_declares_auction_and_selected_exit(tmp_path):
    book = ContinuationSignalBook(_ranking(tmp_path / "rank.parquet"), top_k=3)
    strategy = AiLimitUpContinuationStrategy(
        ContinuationSelection(book, gap_min=0, gap_max=0.02), exit_policy="11:25"
    )
    schedule = Schedule()
    strategy.on_init(type("Ctx", (), {"schedule": schedule})())
    assert schedule.auction_at == time(9, 25, 10)
    assert schedule.checkpoints == (time(11, 25),)


class _Data:
    def get(self, symbols, start, end):
        return pl.DataFrame({"symbol": symbols, "close": [10.0] * len(symbols)})


class _Market:
    def session_open(self, symbols, day):
        return {symbols[0]: 10.10, symbols[1]: 10.30, symbols[2]: 11.0}

    def limit_up_price(self, symbols, day):
        return {symbol: 11.0 for symbol in symbols}

    def suspended_symbols(self, symbols, day):
        return set()

    def security_eligibility(self, symbols, day):
        return {symbol: (False, False) for symbol in symbols}


def test_auction_gate_keeps_only_mild_non_limit_gap(tmp_path):
    book = ContinuationSignalBook(_ranking(tmp_path / "rank.parquet"), top_k=3)
    selection = ContinuationSelection(book, gap_min=0, gap_max=0.02)
    ctx = type("Ctx", (), {"market": _Market(), "data": _Data()})()
    got = selection.select(ctx, date(2026, 9, 30))
    assert [signal.symbol for signal, _, _ in got] == ["000001.SZ"]
    reasons = {row.symbol: row.reason for row in selection.history}
    assert "outside range" in reasons["000002.SZ"]
    assert reasons["000003.SZ"] == "opened at limit up"


def test_auction_gate_fails_closed_for_missing_or_blocked_security_status(tmp_path):
    class Market(_Market):
        def security_eligibility(self, symbols, day):
            return {symbols[0]: (True, False), symbols[1]: (False, True)}

    book = ContinuationSignalBook(_ranking(tmp_path / "rank.parquet"), top_k=3)
    selection = ContinuationSelection(book, gap_min=0, gap_max=0.20)
    selection.select(type("Ctx", (), {"market": Market(), "data": _Data()})(),
                     date(2026, 9, 30))
    reasons = {row.symbol: row.reason for row in selection.history}
    assert reasons == {
        "000001.SZ": "ST",
        "000002.SZ": "delisting",
        "000003.SZ": "missing ST/delisting status",
    }
