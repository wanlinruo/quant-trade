from __future__ import annotations

import sys
from types import ModuleType

from qsys.agent.xtquant_adapter import XtQuantAdapter


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

    assert calls == [(r"C:\\qmt\\userdata_mini", 456789)]
    assert adapter._account.account_id == "123456789"
