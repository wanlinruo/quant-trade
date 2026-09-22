from datetime import date, datetime, time

import polars as pl

from qsys.backtest.entry_fill import MinuteBar
from qsys.backtest.runner import entry_minute_loader
from qsys.core.types import Symbol

DAY = date(2024, 1, 2)


class FakeMarketData:
    def __init__(self, frame):
        self.frame, self.calls = frame, []

    def minute_bars(self, symbols, start, end):
        self.calls.append((symbols, start, end))
        return self.frame.filter(pl.col("symbol").is_in(symbols))


def _bar(symbol, hhmm, open_, low):
    return {"symbol": symbol, "dt": datetime.combine(DAY, hhmm), "open": open_,
            "high": open_, "low": low, "close": open_, "volume": 10.0, "amount": 1.0}


def test_loader_returns_only_the_bar_labelled_at_the_moment():
    frame = pl.DataFrame([
        _bar("600001.SH", time(9, 31), 10.03, 9.99),
        _bar("600001.SH", time(9, 32), 10.50, 10.40),   # 不拿后一分钟冒充
        _bar("000001.SZ", time(9, 32), 5.0, 5.0),
        _bar("600002.SH", time(9, 31), float("nan"), 1.0),
    ])
    data = FakeMarketData(frame)
    load = entry_minute_loader(data)

    got = load(DAY, time(9, 31), [Symbol.parse("600001.SH"), Symbol.parse("000001.SZ"),
                                  Symbol.parse("600002.SH")])

    assert got == {Symbol.parse("600001.SH"): MinuteBar(open=10.03, low=9.99)}
    moment = datetime.combine(DAY, time(9, 31))
    assert data.calls == [(["600001.SH", "000001.SZ", "600002.SH"], moment, moment)]


def test_loader_skips_the_query_without_symbols():
    data = FakeMarketData(pl.DataFrame())
    assert entry_minute_loader(data)(DAY, time(9, 31), []) == {}
    assert data.calls == []


def test_web_entry_fill_modes():
    from qsys.backtest.entry_fill import EntryFillMode
    from qsys.backtest.runner import entry_fill_rule

    assert entry_fill_rule("open") is None          # 原口径：不配规则，run_id 不变
    rule = entry_fill_rule("continuous")
    assert rule.mode is EntryFillMode.CONTINUOUS and rule.slippage == 0.005
    import pytest
    with pytest.raises(ValueError):
        entry_fill_rule("slippage")
