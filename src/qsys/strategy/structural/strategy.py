"""Structural Strategy V1.2 的 qsys 组装版。

买入（原文 `run_daily(buy, '09:26')`）：选股 -> S8 权重分配 -> 按当日开盘价
下单。在引擎里放在 `on_session_start`，成交价即当日开盘价。

退出（三个检查点）：

| 时刻 | 作用于 | 规则 |
|---|---|---|
| 11:25 | HIGH | `legacy_exit`（止盈 / MA5 止损） |
| 11:25 | LOW | `low_1125` |
| 14:25 | LOW | `low_1425` |
| 14:50 | HIGH | `legacy_exit` |

**本策略是隔夜策略**：T 日开盘买入，T+1 才可卖（原文 `get_sellable_lots`）。
检查点只卖 `available_qty`——引擎的 `unfreeze()` 与 `SettlementConstraint`
已经实现了这条规则。

**已知偏差**：退出按**持仓均价**判盈亏，而非每笔买入的入场价。原策略有
lot 级账本，我们的 `Portfolio` 按标的聚合——同一标的跨分支重复买入时会有
偏差。lot 级账本归入后续。
"""

from __future__ import annotations

from datetime import date, time, timedelta

import structlog

from qsys.core.trading import Side
from qsys.core.types import Symbol
from qsys.strategy.base import Strategy
from qsys.strategy.structural.allocation import allocate
from qsys.strategy.structural.exits import (
    dynamic_ma5,
    legacy_exit,
    low_1125,
    low_1425,
)

log = structlog.get_logger(__name__)

# 原文 L188-L204 的 run_daily 时刻
CHECKPOINTS = (time(11, 25), time(14, 25), time(14, 50))

# dynamic_ma5 要最近 4 根收盘
_MA_BARS = 4
_MA_LOOKBACK_DAYS = 30


class StructuralStrategy(Strategy):
    def __init__(self, selection) -> None:
        self._selection = selection

    # ---- 买入 ----

    def on_session_start(self, ctx) -> None:
        today = ctx.now.date()
        previous = ctx.state.get("last_session")
        ctx.state["last_session"] = today
        if previous is None:
            # 没有前一交易日就无从选股
            return

        picks = self._selection.select(today, previous)
        if not picks:
            return

        opens = ctx.market.session_open([p.symbol for p in picks], today)
        budgets = allocate(
            [(p.symbol, p.tag) for p in picks], ctx.portfolio.cash, opens
        )
        tags = ctx.state.setdefault("tags", {})
        for budget in budgets:
            if budget.qty <= 0:
                log.info(
                    "structural.skip", symbol=budget.symbol, status=budget.status
                )
                continue
            tags[budget.symbol] = budget.tag
            ctx.order.submit(Symbol.parse(budget.symbol), Side.BUY, budget.qty)

    # ---- 退出 ----

    def on_checkpoint(self, ctx, at: time) -> None:
        tags = ctx.state.get("tags", {})
        held = [
            s for s in ctx.portfolio.symbols
            if ctx.portfolio.position(s).available_qty > 0
        ]
        if not held:
            return

        today = ctx.now.date()
        symbols = [str(s) for s in held]
        quotes = ctx.market.intraday(symbols, today, at)
        limits = ctx.market.limit_up_price(symbols, today)

        for symbol in held:
            key = str(symbol)
            tag = tags.get(key)
            if tag is None:
                # 不知道该按哪条规则退，宁可不动
                continue
            quote = quotes.get(key)
            if quote is None:
                # 拿不到当时价格就不能判，绝不用别的价格顶替
                continue

            position = ctx.portfolio.position(symbol)
            decision = self._decide(
                ctx, at, tag, key, today, quote.price,
                limits.get(key), position.avg_cost,
            )
            if decision is None or not decision.should_exit:
                continue
            ctx.order.submit(symbol, Side.SELL, position.available_qty)
            log.info(
                "structural.exit", symbol=key, tag=tag, at=str(at),
                reason=decision.reason, ret=decision.ret,
            )

    def _decide(self, ctx, at, tag, symbol, today, price, limit_up, entry):
        branch = tag.split("_", 1)[0]
        if at == time(11, 25):
            if branch == "LOW":
                return low_1125(price, limit_up, entry)
            return legacy_exit(price, limit_up, entry, self._ma5(ctx, symbol, today, price))
        if at == time(14, 25):
            return low_1425(price, limit_up, entry) if branch == "LOW" else None
        if at == time(14, 50):
            if branch != "HIGH":
                return None
            return legacy_exit(price, limit_up, entry, self._ma5(ctx, symbol, today, price))
        return None

    def _ma5(self, ctx, symbol: str, today: date, price: float) -> float | None:
        """动态 MA5：最近 4 根收盘 + 当前价。"""
        frame = ctx.data.get(
            [symbol], today - timedelta(days=_MA_LOOKBACK_DAYS), today
        )
        if frame.height == 0:
            return None
        closes = frame.sort("dt")["close"].to_list()[-_MA_BARS:]
        return dynamic_ma5(closes, price)
