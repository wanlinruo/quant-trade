"""券商适配器协议与测试替身。

真实的 xtquant 适配器在 xtquant_adapter.py（仅 Windows 可导入）。
单元测试一律用 FakeAdapter——不得导入 xtquant。
"""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from typing import Protocol

from qsys.agent.fill_tracker import BrokerOrder, BrokerTrade
from qsys.live.messages import PositionSnapshot


@dataclass(frozen=True, slots=True)
class BrokerAccount:
    cash: float
    positions: dict[str, PositionSnapshot]
    frozen_cash: float = 0.0
    market_value: float = 0.0
    total_asset: float | None = None


class BrokerAdapter(Protocol):
    def place_order(
        self, symbol: str, side: str, qty: int, order_type: str,
        limit_price: float | None, client_order_id: str = "",
    ) -> str:
        """下单，返回券商订单号。失败时抛异常。"""

    def cancel_all(self) -> None: ...

    def query_account(self) -> tuple[float, dict[str, PositionSnapshot]]:
        """返回 (可用现金, 持仓字典)。"""

    def query_account_details(self) -> BrokerAccount: ...

    def query_trades(self) -> list[BrokerTrade]: ...

    def query_orders(self) -> list[BrokerOrder]: ...

    def find_order(self, client_order_id: str) -> str | None: ...


class FakeAdapter:
    """测试替身：记录调用，可配置失败。"""

    def __init__(
        self,
        *,
        cash: float = 100_000.0,
        positions: dict[str, tuple[int, int, float]] | None = None,
        fail_with: str | None = None,
        auto_fill: bool = True,
    ) -> None:
        self.cash = cash
        self._positions = positions or {}
        self.fail_with = fail_with
        self.placed: list[tuple[str, str, int, str, float | None]] = []
        self.cancelled = False
        self._seq = 0
        self.auto_fill = auto_fill
        self.trades: list[BrokerTrade] = []
        self.orders: list[BrokerOrder] = []
        self._by_client: dict[str, str] = {}

    def place_order(
        self, symbol: str, side: str, qty: int, order_type: str,
        limit_price: float | None, client_order_id: str = "",
    ) -> str:
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        self.placed.append((symbol, side, qty, order_type, limit_price))
        self._seq += 1
        broker_id = f"B{self._seq}"
        client_id = client_order_id or f"legacy-{broker_id}"
        self._by_client[client_id] = broker_id
        traded = qty if self.auto_fill else 0
        status = 56 if self.auto_fill else 50
        self.orders.append(BrokerOrder(
            broker_id, client_id, symbol, side, qty, traded, status,
        ))
        if self.auto_fill:
            self.trades.append(BrokerTrade(
                f"T{self._seq}", client_id, broker_id, symbol, side,
                float(limit_price or 10.0), qty, datetime.now(),
            ))
        return broker_id

    def cancel_all(self) -> None:
        self.cancelled = True

    def query_account(self) -> tuple[float, dict[str, PositionSnapshot]]:
        return self.cash, {
            symbol: PositionSnapshot(total_qty=t, available_qty=a, avg_cost=c)
            for symbol, (t, a, c) in self._positions.items()
        }

    def query_trades(self) -> list[BrokerTrade]:
        return list(self.trades)

    def query_orders(self) -> list[BrokerOrder]:
        return list(self.orders)

    def find_order(self, client_order_id: str) -> str | None:
        return self._by_client.get(client_order_id)

    def query_account_details(self) -> BrokerAccount:
        cash, positions = self.query_account()
        market_value = sum(p.total_qty * p.avg_cost for p in positions.values())
        return BrokerAccount(
            cash=cash, positions=positions, market_value=market_value,
            total_asset=cash + market_value,
        )
