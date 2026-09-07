from datetime import date, datetime

from qsys.core.events import MarketSnapshot, Reject
from qsys.core.trading import Side
from qsys.core.types import Bar, Market, Symbol

CMB = Symbol("600036", Market.SH)
PAB = Symbol("000001", Market.SZ)
D = date(2024, 1, 3)


def bar(symbol: Symbol, close: float) -> Bar:
    return Bar(symbol, D, close, close, close, close, 1000, close * 1000)


def snap(**kw) -> MarketSnapshot:
    defaults = dict(
        session=D,
        bars={CMB: bar(CMB, 10.0)},
        prev_closes={CMB: 9.8, PAB: 20.0},
        boards={CMB: "main", PAB: "gem"},
    )
    return MarketSnapshot(**{**defaults, **kw})


def test_symbols_lists_traded_instruments():
    assert snap().symbols == frozenset({CMB})


def test_bar_of_returns_the_bar():
    assert snap().bar_of(CMB).close == 10.0


def test_bar_of_missing_symbol_is_none():
    assert snap().bar_of(PAB) is None


def test_symbol_without_a_bar_is_suspended():
    """当日无 bar 即视为停牌——这是引擎判定停牌的唯一依据。"""
    assert snap().is_suspended(PAB) is True
    assert snap().is_suspended(CMB) is False


def test_prev_close_lookup():
    assert snap().prev_close_of(CMB) == 9.8
    assert snap().prev_close_of(Symbol("300750", Market.SZ)) is None


def test_board_lookup_defaults_to_main():
    s = snap()
    assert s.board_of(PAB) == "gem"
    assert s.board_of(Symbol("300750", Market.SZ)) == "main"


def test_snapshot_is_frozen():
    import pytest

    with pytest.raises(Exception):
        snap().session = date(2024, 1, 4)


def test_reject_carries_reason():
    r = Reject("o1", CMB, Side.BUY, 100, "suspension: halted", datetime(2024, 1, 3, 9, 30))
    assert r.reason.startswith("suspension")
    assert r.qty == 100
