from __future__ import annotations

import sys
from types import ModuleType

import pytest

from qsys.agent.xtquant_adapter import XtQuantAdapter, _ours


def test_adapter_collects_strategy_and_commissioning_reports_only() -> None:
    assert _ours("q2610120010123456789ab")
    assert _ours("commission-20261012-100000-abcdef")
    assert not _ours("manual-order")
    assert not _ours("commission-20261012-100000-abcdef-extra")


def test_xtquant_trader_session_is_compatible_with_positional_only_api(
    monkeypatch,
) -> None:
    calls: list[tuple[str, int]] = []

    class FakeTrader:
        def __init__(self, path: str, session: int, /) -> None:
            calls.append((path, session))

        def start(self) -> None:
            pass

        def connect(self) -> int:
            return 0

        def subscribe(self, account: object) -> None:
            pass

    class FakeAccount:
        def __init__(self, account_id: str) -> None:
            self.account_id = account_id

    xtquant = ModuleType("xtquant")
    xttrader = ModuleType("xtquant.xttrader")
    xttype = ModuleType("xtquant.xttype")
    xttrader.XtQuantTrader = FakeTrader  # type: ignore[attr-defined]
    xttype.StockAccount = FakeAccount  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "xtquant", xtquant)
    monkeypatch.setitem(sys.modules, "xtquant.xttrader", xttrader)
    monkeypatch.setitem(sys.modules, "xtquant.xttype", xttype)

    adapter = XtQuantAdapter("123456789", r"C:\\qmt\\userdata_mini")

    assert len(calls) == 1
    assert calls[0][0] == r"C:\\qmt\\userdata_mini"
    assert 1 <= calls[0][1] < 2**31
    assert adapter._account.account_id == "123456789"


class _Position:
    def __init__(self, code: str, volume: int, can_use: int, open_price: float,
                 **extra: object) -> None:
        self.stock_code = code
        self.volume = volume
        self.can_use_volume = can_use
        self.open_price = open_price
        for key, value in extra.items():
            setattr(self, key, value)


class _Asset:
    cash = 1.31
    frozen_cash = 0.0
    market_value = 100_000.0
    total_asset = 100_001.31


def _adapter_with_positions(monkeypatch, positions: list[_Position]) -> XtQuantAdapter:
    adapter = XtQuantAdapter.__new__(XtQuantAdapter)

    class FakeTrader:
        def query_stock_asset(self, account: object) -> _Asset:
            return _Asset()

        def query_stock_positions(self, account: object) -> list[_Position]:
            return positions

    adapter._trader = FakeTrader()          # type: ignore[attr-defined]
    adapter._account = object()             # type: ignore[attr-defined]
    return adapter


def test_position_market_value_is_taken_straight_from_the_broker(monkeypatch) -> None:
    """对账要用现金等价物的市值算可比现金，所以代理必须把它报上来（#134）。

    不能让核心用 `qty × avg_cost` 反推：生产实测两笔逆回购各 1000 张、
    成本 100，反推得 20 万，而券商直取的市值合计只有 10 万。
    """
    adapter = _adapter_with_positions(monkeypatch, [
        _Position("131990.SZ", 1000, 0, 100.0, market_value=99_998.69),
    ])

    _, positions = adapter.query_account()

    assert positions["131990.SZ"].market_value == 99_998.69


def test_a_position_without_a_market_value_field_reports_null(monkeypatch) -> None:
    """券商没给市值就报 null。报 0 会让对账把逆回购里的钱当凭空消失。"""
    adapter = _adapter_with_positions(monkeypatch, [
        _Position("600036.SH", 500, 500, 10.0),
    ])

    _, positions = adapter.query_account()

    assert positions["600036.SH"].market_value is None


def test_active_order_count_is_account_wide_and_unfiltered() -> None:
    adapter = XtQuantAdapter.__new__(XtQuantAdapter)

    class FakeTrader:
        def query_stock_orders(self, account: object, cancelable_only=False):
            assert cancelable_only is True
            return [object(), object()]

    adapter._trader = FakeTrader()  # type: ignore[attr-defined]
    adapter._account = object()  # type: ignore[attr-defined]

    assert adapter.query_active_order_count() == 2


def test_active_order_count_query_failure_is_not_treated_as_zero() -> None:
    adapter = XtQuantAdapter.__new__(XtQuantAdapter)

    class FakeTrader:
        def query_stock_orders(self, account: object, cancelable_only=False):
            assert cancelable_only is True
            return None

    adapter._trader = FakeTrader()  # type: ignore[attr-defined]
    adapter._account = object()  # type: ignore[attr-defined]

    with pytest.raises(RuntimeError, match="failed to query"):
        adapter.query_active_order_count()
