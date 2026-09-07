"""券商适配器协议与测试替身。

真实的 xtquant 适配器在 xtquant_adapter.py（仅 Windows 可导入）。
单元测试一律用 FakeAdapter——不得导入 xtquant。
"""

from __future__ import annotations

from typing import Protocol

from qsys.live.messages import PositionSnapshot


class BrokerAdapter(Protocol):
    def place_order(
        self, symbol: str, side: str, qty: int, order_type: str,
        limit_price: float | None,
    ) -> str:
        """下单，返回券商订单号。失败时抛异常。"""

    def cancel_all(self) -> None: ...

    def query_account(self) -> tuple[float, dict[str, PositionSnapshot]]:
        """返回 (可用现金, 持仓字典)。"""


class FakeAdapter:
    """测试替身：记录调用，可配置失败。"""

    def __init__(
        self,
        *,
        cash: float = 100_000.0,
        positions: dict[str, tuple[int, int, float]] | None = None,
        fail_with: str | None = None,
    ) -> None:
        self.cash = cash
        self._positions = positions or {}
        self.fail_with = fail_with
        self.placed: list[tuple[str, str, int, str, float | None]] = []
        self.cancelled = False
        self._seq = 0

    def place_order(
        self, symbol: str, side: str, qty: int, order_type: str,
        limit_price: float | None,
    ) -> str:
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        self.placed.append((symbol, side, qty, order_type, limit_price))
        self._seq += 1
        return f"B{self._seq}"

    def cancel_all(self) -> None:
        self.cancelled = True

    def query_account(self) -> tuple[float, dict[str, PositionSnapshot]]:
        return self.cash, {
            symbol: PositionSnapshot(total_qty=t, available_qty=a, avg_cost=c)
            for symbol, (t, a, c) in self._positions.items()
        }
