"""真实 xtquant 适配器。仅 Windows 可导入。

!! 已知待办：xtquant 的成交回报是**异步**的 —— order_stock 只返回委托号，
   真实成交价与数量由回调推送。当前 ExecutionAgent 在下单成功后立即发布
   FillEvent（价格取自限价单的 limit_price，市价单为 0）。接真实券商时
   必须改为「下单成功 → 等回调 → 发布带真实成交价的 FillEvent」，
   否则回测-实盘对账的第三层会拿到错误的实盘价格。

依赖尽可能少：只用 xtquant 与标准库。
方向常量与字段名以官方文档为准，需在 Windows 上实测校正。
"""

from __future__ import annotations

from qsys.live.messages import PositionSnapshot

_SIDE_TO_XT = {"buy": 23, "sell": 24}  # xtquant 委托方向常量，接入时以官方文档为准


class XtQuantAdapter:
    def __init__(self, account_id: str, mini_qmt_path: str) -> None:
        from xtquant.xttrader import XtQuantTrader
        from xtquant.xttype import StockAccount

        self._trader = XtQuantTrader(mini_qmt_path, session_id=int(account_id[-6:]))
        self._account = StockAccount(account_id)
        self._trader.start()
        if self._trader.connect() != 0:
            raise RuntimeError("miniQMT 连接失败")
        self._trader.subscribe(self._account)

    def place_order(
        self, symbol: str, side: str, qty: int, order_type: str,
        limit_price: float | None,
    ) -> str:
        from xtquant import xtconstant

        price_type = (
            xtconstant.FIX_PRICE if order_type == "limit" else xtconstant.LATEST_PRICE
        )
        order_id = self._trader.order_stock(
            self._account, symbol, _SIDE_TO_XT[side], qty,
            price_type, limit_price or 0.0,
        )
        if order_id < 0:
            raise RuntimeError(f"下单被拒: order_id={order_id}")
        return str(order_id)

    def cancel_all(self) -> None:
        for order in self._trader.query_stock_orders(self._account, cancelable_only=True):
            self._trader.cancel_order_stock(self._account, order.order_id)

    def query_account(self) -> tuple[float, dict[str, PositionSnapshot]]:
        asset = self._trader.query_stock_asset(self._account)
        positions = {
            p.stock_code: PositionSnapshot(
                total_qty=p.volume, available_qty=p.can_use_volume,
                avg_cost=p.open_price,
            )
            for p in self._trader.query_stock_positions(self._account)
        }
        return float(asset.cash), positions
