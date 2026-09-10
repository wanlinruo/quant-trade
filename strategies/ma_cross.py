"""一个完整的策略，一个文件。

用来证明 qsys 的策略协议**不要求拆成模块**——结构策略拆成 8 个文件是为了
逐条对照聚宽原文的行号，不是框架的要求。

规则（刻意简单，重点是接口而非 alpha）：
  · 20 日均线上穿 60 日均线，等权买入，最多 5 只
  · 跌破 20 日均线卖出
  · 只在收盘后决策，次日开盘成交

跑：
  uv run python scripts/run_backtest.py --strategy strategies/ma_cross.py \
      --start 2025-09-10 --end 2026-09-08 --cash 100000
"""

from datetime import timedelta

from qsys.core.trading import Side
from qsys.strategy.base import Strategy

FAST, SLOW = 20, 60
MAX_POSITIONS = 5
LOT = 100
# 取 SLOW 根日线要往前留出足够的日历日（含周末与假期）
LOOKBACK_DAYS = 130


class MaCross(Strategy):
    def on_init(self, ctx) -> None:
        # 不声明 auction/checkpoint：只用收盘决策、次日开盘成交的默认节奏
        ctx.log.info("ma_cross.init", fast=FAST, slow=SLOW, max_positions=MAX_POSITIONS)

    def on_bar(self, ctx, bars) -> None:
        today = ctx.now.date()
        held = set(ctx.portfolio.symbols)

        # **一次取全部标的**，不要逐只调 ctx.data.get()——
        # 那是 5,302 x 241 次查询，回测会跑一整天。
        closes = self._closes(ctx, list(bars), today)

        wanted: list = []
        for symbol in bars:
            series = closes.get(str(symbol), [])
            if len(series) < SLOW:
                continue
            fast = sum(series[-FAST:]) / FAST
            slow = sum(series[-SLOW:]) / SLOW

            if symbol in held:
                if series[-1] < fast:
                    qty = ctx.portfolio.position(symbol).available_qty
                    if qty > 0:
                        ctx.order.submit(symbol, Side.SELL, qty)
                continue

            if fast > slow and series[-1] > fast:
                wanted.append((symbol, fast / slow))

        room = MAX_POSITIONS - len(held)
        if room <= 0 or not wanted:
            return

        # 上穿幅度大的优先
        wanted.sort(key=lambda x: x[1], reverse=True)
        budget = ctx.portfolio.cash / room
        for symbol, _ in wanted[:room]:
            price = bars[symbol].close
            qty = int(budget / price / LOT) * LOT
            if qty >= LOT:
                ctx.order.submit(symbol, Side.BUY, qty)

    def _closes(self, ctx, symbols, today) -> dict[str, list[float]]:
        """一次批量取，返回 {标的: 收盘序列}。"""
        frame = ctx.data.get(
            [str(s) for s in symbols], today - timedelta(days=LOOKBACK_DAYS), today
        )
        if frame.height == 0:
            return {}
        out: dict[str, list[float]] = {}
        for symbol, series in frame.sort("dt").group_by("symbol", maintain_order=True):
            out[symbol[0] if isinstance(symbol, tuple) else symbol] = (
                series["close"].to_list()
            )
        return out
