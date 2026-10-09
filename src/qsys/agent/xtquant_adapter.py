"""真实 xtquant 适配器。仅 Windows 可导入。

xtquant 的成交回报是异步的：order_stock 只返回委托号。本适配器轮询券商
委托与成交真值，由 ExecutionAgent 发布真实的部分成交、终态和拒单事件；
绝不把“委托已受理”伪装成成交。

依赖尽可能少：只用 xtquant 与标准库。
方向常量与字段名以官方文档为准，需在 Windows 上实测校正。
"""

from __future__ import annotations

from datetime import datetime
import re
import secrets

from qsys.agent.adapter import BrokerAccount
from qsys.agent.fill_tracker import BrokerOrder, BrokerTrade
from qsys.live.commissioning import is_commissioning_order_id
from qsys.live.messages import PositionSnapshot

_SIDE_TO_XT = {"buy": 23, "sell": 24}  # xtquant 委托方向常量，接入时以官方文档为准
_STRATEGY_ORDER_ID = re.compile(r"q\d{9}[0-9a-f]{12}")


def _ours(client_order_id: str) -> bool:
    """只接管策略或验证单委托，隔离旧 bridge 与人工委托。"""
    return bool(_STRATEGY_ORDER_ID.fullmatch(client_order_id)) or is_commissioning_order_id(
        client_order_id
    )


def _optional_float(value: object) -> float | None:
    """缺字段就是 None。别把「券商没给」写成 0——那是编数，不是取数。"""
    return None if value is None else float(value)


class XtQuantAdapter:
    def __init__(self, account_id: str, mini_qmt_path: str) -> None:
        from xtquant.xttrader import XtQuantTrader
        from xtquant.xttype import StockAccount

        # Older xtquant releases expose ``session`` only as a positional
        # argument, while newer releases also document it as ``session_id``.
        # The session identifies this client connection, not the account, so
        # generate a fresh value on every process start to avoid collisions
        # with another gateway or a stale MiniQMT session.
        session = secrets.randbelow(2**31 - 1) + 1
        self._trader = XtQuantTrader(mini_qmt_path, session)
        self._account = StockAccount(account_id)
        self._trader.start()
        if self._trader.connect() != 0:
            raise RuntimeError("miniQMT 连接失败")
        self._trader.subscribe(self._account)

    def place_order(
        self, symbol: str, side: str, qty: int, order_type: str,
        limit_price: float | None, client_order_id: str = "",
    ) -> str:
        from xtquant import xtconstant

        price_type = (
            xtconstant.FIX_PRICE if order_type == "limit" else xtconstant.LATEST_PRICE
        )
        order_id = self._trader.order_stock(
            self._account, symbol, _SIDE_TO_XT[side], qty,
            price_type, limit_price or 0.0, "qsys", client_order_id,
        )
        if order_id < 0:
            raise RuntimeError(f"下单被拒: order_id={order_id}")
        return str(order_id)

    def query_trades(self) -> list[BrokerTrade]:
        out = []
        for trade in self._trader.query_stock_trades(self._account) or []:
            client_id = str(getattr(trade, "order_remark", "") or "")
            if not _ours(client_id):
                continue
            side = "buy" if int(trade.order_type) == _SIDE_TO_XT["buy"] else "sell"
            stamp = int(trade.traded_time)
            at = datetime.fromtimestamp(stamp) if stamp > 0 else datetime.now()
            out.append(BrokerTrade(
                traded_id=str(trade.traded_id), client_order_id=client_id,
                broker_order_id=str(trade.order_id), symbol=str(trade.stock_code),
                side=side, price=float(trade.traded_price),
                qty=int(trade.traded_volume), at=at,
            ))
        return out

    def query_orders(self) -> list[BrokerOrder]:
        out = []
        for order in self._trader.query_stock_orders(self._account) or []:
            client_id = str(getattr(order, "order_remark", "") or "")
            if not _ours(client_id):
                continue
            side = "buy" if int(order.order_type) == _SIDE_TO_XT["buy"] else "sell"
            out.append(BrokerOrder(
                broker_order_id=str(order.order_id), client_order_id=client_id,
                symbol=str(order.stock_code), side=side,
                ordered_qty=int(order.order_volume), traded_qty=int(order.traded_volume),
                status=int(order.order_status), status_msg=str(order.status_msg or ""),
            ))
        return out

    def find_order(self, client_order_id: str) -> str | None:
        for order in self.query_orders():
            if order.client_order_id == client_order_id:
                return order.broker_order_id
        return None

    def cancel_all(self) -> None:
        for order in self._trader.query_stock_orders(self._account, cancelable_only=True):
            self._trader.cancel_order_stock(self._account, order.order_id)

    def query_account(self) -> tuple[float, dict[str, PositionSnapshot]]:
        asset = self._trader.query_stock_asset(self._account)
        positions = {
            p.stock_code: PositionSnapshot(
                total_qty=p.volume, available_qty=p.can_use_volume,
                avg_cost=p.open_price,
                # 直取券商市值。对账用它算现金等价物占用的可比现金；
                # 字段缺失就报 None，不补 0、不用 qty×avg_cost 反推（#134）。
                market_value=_optional_float(getattr(p, "market_value", None)),
            )
            for p in self._trader.query_stock_positions(self._account)
        }
        return float(asset.cash), positions

    def query_account_details(self) -> BrokerAccount:
        asset = self._trader.query_stock_asset(self._account)
        _, positions = self.query_account()
        return BrokerAccount(
            cash=float(asset.cash), positions=positions,
            frozen_cash=float(getattr(asset, "frozen_cash", 0.0) or 0.0),
            market_value=float(getattr(asset, "market_value", 0.0) or 0.0),
            total_asset=float(getattr(asset, "total_asset", 0.0) or 0.0),
        )
