"""Real-time virtual broker driven only by read-only market quotes.

Unlike ``ShadowBroker`` this broker creates fills and maintains a virtual
portfolio.  It never owns a Transport and therefore cannot accidentally write
to the real order stream.
"""

from __future__ import annotations

from datetime import datetime

from qsys.backtest.broker import Broker
from qsys.backtest.fees import FeeModel
from qsys.backtest.slippage import SlippageModel
from qsys.core.constraints import ConstraintContext, ConstraintPipeline
from qsys.core.events import MarketSnapshot, Reject
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, Order, OrderIntent, OrderType, Side, to_cents
from qsys.live.messages import MarketQuote


class PaperBroker(Broker):
    """Match pending virtual orders against strictly newer quote snapshots."""

    def __init__(
        self,
        portfolio: Portfolio,
        pipeline: ConstraintPipeline,
        fees: FeeModel,
        slippage: SlippageModel,
        *,
        volume_cap: float = 0.05,
    ) -> None:
        if not 0 < volume_cap <= 1:
            raise ValueError("volume_cap must be in (0, 1]")
        self.portfolio = portfolio
        self._pipeline = pipeline
        self._fees = fees
        self._slippage = slippage
        self._volume_cap = volume_cap
        self._pending: dict[str, tuple[Order, int]] = {}
        self._fills: list[Fill] = []
        self._rejects: list[Reject] = []
        self._last_volume: dict[str, int] = {}

    def submit(self, order: Order) -> None:
        self._pending[order.order_id] = (order, order.qty)

    def restore(self, order: Order, remaining: int) -> None:
        if remaining > 0:
            self._pending[order.order_id] = (order, remaining)

    def pending(self) -> tuple[Order, ...]:
        return tuple(order for order, _ in self._pending.values())

    def remaining(self, order_id: str) -> int:
        item = self._pending.get(order_id)
        return item[1] if item else 0

    def cancel_all(self, at: datetime | None = None, reason: str = "cancelled") -> None:
        moment = at or datetime.now()
        for order, remaining in self._pending.values():
            self._rejects.append(
                Reject(order.order_id, order.symbol, order.side, remaining, reason, moment)
            )
        self._pending.clear()

    def poll_fills(self) -> tuple[Fill, ...]:
        out, self._fills = tuple(self._fills), []
        return out

    def poll_rejects(self) -> tuple[Reject, ...]:
        out, self._rejects = tuple(self._rejects), []
        return out

    def on_market_data(self, snapshot: MarketSnapshot) -> None:
        """Compatibility path used by Broker callers and simple tests."""
        for symbol, bar in snapshot.bars.items():
            self.on_quote(MarketQuote(
                symbol=str(symbol), at=datetime.combine(snapshot.session, datetime.min.time()),
                price=bar.open, open=bar.open, high=bar.high, low=bar.low,
                prev_close=snapshot.prev_close_of(symbol) or 0.0,
                volume=max(0, int(bar.volume * 100)), amount=bar.amount,
                suspended=False,
            ), limits=snapshot.limit_of(symbol), board=snapshot.board_of(symbol))

    def on_quote(
        self, quote: MarketQuote, *, limits: tuple[float, float] | None,
        board: str = "main",
    ) -> None:
        """`limits`：当日直取涨跌停价 (up, down)；None 表示拿不到——买单拒绝、卖单放行（D-19）。"""
        limit_up, limit_down = limits or (None, None)
        previous = self._last_volume.get(quote.symbol)
        delta = quote.volume if previous is None else max(0, quote.volume - previous)
        self._last_volume[quote.symbol] = max(quote.volume, previous or 0)

        for order_id, (order, remaining) in list(self._pending.items()):
            if str(order.symbol) != quote.symbol or quote.at <= order.created_at:
                continue
            if quote.suspended or quote.price <= 0:
                continue
            price = self._slippage.adjust(order.side, quote.price)
            if order.order_type is OrderType.LIMIT:
                if order.side is Side.BUY and price > float(order.limit_price):
                    continue
                if order.side is Side.SELL and price < float(order.limit_price):
                    continue

            result = self._pipeline.apply(
                OrderIntent(order.symbol, order.side, remaining, order.order_type,
                            order.limit_price),
                ConstraintContext(
                    portfolio=self.portfolio, reference_price=price,
                    limit_up=limit_up, limit_down=limit_down, board=board,
                    is_suspended=quote.suspended,
                ),
            )
            if result.is_rejected:
                self._rejects.append(Reject(
                    order.order_id, order.symbol, order.side, remaining,
                    result.binding_reason, quote.at,
                ))
                self._pending.pop(order_id, None)
                continue

            # QMT volume is normalized to shares by the gateway.  If no new
            # volume is observable, wait instead of inventing liquidity.
            cap = int(delta * self._volume_cap)
            qty = min(remaining, result.allowed_qty, cap)
            if order.side is Side.BUY:
                qty = qty // 100 * 100
            if qty <= 0:
                continue
            fee = self._fees.fee_for(order.side, to_cents(price * qty))
            fill = Fill(order.order_id, order.symbol, order.side, qty, price, fee, quote.at)
            self.portfolio.apply_fill(fill)
            self._fills.append(fill)
            left = remaining - qty
            if left:
                self._pending[order_id] = (order, left)
            else:
                self._pending.pop(order_id, None)
