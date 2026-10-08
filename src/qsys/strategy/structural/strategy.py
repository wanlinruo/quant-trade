"""Structural Strategy V1.2 的 qsys 组装版。

买入（原文 `run_daily(buy, '09:26')`）：选股 -> S8 权重分配 -> 按当日开盘价
下单。放在 `on_auction`（引擎的竞价时刻，默认 09:30）而**不是**
`on_session_start`——后者在 09:15，那时竞价尚未结算、当日开盘价不可见，
`allocate` 会拿不到价格而把每一单都跳过。

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

买入用 **CASH-SAFE V2** 定量：按 `开盘价 x 1.002` 挂限价单并预留费用，
避免"按未滑点价用满现金、实际按滑点价成交"导致的透支。详见
`allocation.py`。

策略状态维护 lot 级账本：每次真实买入成交记录入场价、数量和分支标签，
退出时逐 lot 判定后汇总卖出量。由旧状态或人工持仓导入、没有 lot 信息的
仓位仍使用聚合持仓与 `tags` 兼容处理。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import structlog

from qsys.core.trading import Fill, OrderType, Side
from qsys.core.types import Symbol
from qsys.strategy.base import Strategy
from qsys.strategy.structural.allocation import PRICE_BUFFER, allocate
from qsys.strategy.structural.exits import (
    dynamic_ma5,
    legacy_exit,
    low_1125,
    low_1425,
)

log = structlog.get_logger(__name__)

# 竞价结算后下单。原策略是 09:26；我们的竞价 bar 以 09:30 标注（收盘时刻
# 约定），故取 09:30——两者都在开盘撮合之前，成交价同为当日开盘价。
AUCTION_AT = time(9, 30)

# 原文 L188-L204 的 run_daily 时刻
CHECKPOINTS = (time(11, 25), time(14, 25), time(14, 50))

# dynamic_ma5 要最近 4 根收盘
_MA_BARS = 4
_MA_LOOKBACK_DAYS = 30


def auction_time(value) -> time:
    """参数里的竞价时刻；不给时为 09:30。早于 09:25:05 撮合结果不可见，拒绝。"""
    if value in (None, ""):
        return AUCTION_AT
    parsed = value if isinstance(value, time) else datetime.strptime(
        str(value), "%H:%M:%S" if str(value).count(":") == 2 else "%H:%M"
    ).time()
    if parsed < time(9, 25, 5) or parsed > time(9, 30):
        raise ValueError(f"auction_at 必须在 09:25:05–09:30 之间：{value}")
    return parsed


def _holding(symbol: str, tag: str, price: float, entry: float,
             *, applies: bool = True) -> str:
    """「看了，没卖」这一条也要说清楚。

    退出规则是 FROZEN 的，`_HOLD` 不带原因，所以这里只陈述判定用到的事实：
    走哪条分支、现价多少、相对成本多少。某些时刻只对特定分支生效
    （14:25 只管 LOW，14:50 只管 HIGH），那种情况直接说「本时刻不适用」。
    """
    move = f"{price:,.2f}" + (f"（{price / entry - 1:+.2%}）" if entry else "")
    return (f"{symbol} {tag} {move} " +
            ("继续持有" if applies else "本时刻规则不适用，不动"))


class StructuralStrategy(Strategy):
    def __init__(self, selection, calendar=None) -> None:
        self._selection = selection
        # 交易日历：状态里还没攒到"上一交易日"时（新账户第一天、状态重置、
        # 回测区间首日）用它查，而不是白白空转一天。
        self._calendar = calendar

    # ---- 买入 ----

    def on_init(self, ctx) -> None:
        """自己声明调度——对应原文 L188-L204 的那几条 run_daily。

        调度是策略的一部分，不是运行器的：检查点改到 14:40 应该改这里，
        而不是改跑它的脚本。竞价时刻可由参数 `auction_at`（HH:MM[:SS]）覆盖：
        09:25 撮合后决策、09:30 前挂单时用 09:25:10（研究文档第 6 节）。
        """
        ctx.schedule.auction(auction_time(ctx.params.get("auction_at")))
        ctx.schedule.checkpoint(*CHECKPOINTS)

    def on_session_start(self, ctx) -> None:
        """只记交易日推进。真正的选股下单在 on_auction。"""
        today = ctx.now.date()
        ctx.state["previous_session"] = ctx.state.get("last_session")
        ctx.state["last_session"] = today

    def on_auction(self, ctx) -> None:
        today = ctx.now.date()
        previous, stale = self._resolve_previous(ctx, today)
        if previous is None:
            ctx.report(note="没有上一交易日，无从选股")
            return
        if stale:
            log.warning("strategy.stale_previous_session",
                        remembered=str(stale), used=str(previous), today=str(today))

        picks = self._selection.select(today, previous)
        funnel = getattr(self._selection, "funnel", None)
        if funnel is not None:
            # 入档前与闸门的拒绝原因分开标注：没买是哪一步决定的，页面要看得出
            reasons = {f"候选池·{k}": v for k, v in funnel.excluded.items()}
            reasons.update({f"入档前·{k}": v for k, v in funnel.skipped.items()})
            reasons.update({f"闸门·{k}": v for k, v in funnel.reasons.items()})
            ctx.report(
                candidates=funnel.pool,
                routed=funnel.routed,
                passed=funnel.allowed,
                reasons=reasons,
            )
        if not picks:
            return

        symbols = [p.symbol for p in picks]
        opens = ctx.market.session_open(symbols, today)
        # CASH-SAFE V2 的限价保护价以涨停价封顶，故要取当日涨停价
        limit_ups = ctx.market.limit_up_price(symbols, today)
        budgets = allocate(
            [(p.symbol, p.tag) for p in picks], ctx.portfolio.cash, opens, limit_ups,
            max_budget=ctx.params.get("max_position_value"),
            price_buffer=float(ctx.params.get("entry_price_buffer", PRICE_BUFFER)),
            sizing_buffer=(
                float(ctx.params["sizing_price_buffer"])
                if ctx.params.get("sizing_price_buffer") is not None else None
            ),
        )
        tags = ctx.state.setdefault("tags", {})
        for budget in budgets:
            if budget.qty <= 0:
                log.info(
                    "structural.skip", symbol=budget.symbol, status=budget.status
                )
                ctx.report(reasons={f"预算不足（{budget.status}）": 1})
                continue
            tags[budget.symbol] = budget.tag
            # 限价单而非市价单：原策略 `order(..., LimitOrderStyle(protect_price))`。
            # 回测里开盘价永远低于保护价，成交结果相同；实盘才见效——
            # 聚宽 82 笔成交中保护价被击穿 0 次。
            ctx.order.submit(
                Symbol.parse(budget.symbol), Side.BUY, budget.qty,
                OrderType.LIMIT, budget.protect_price,
            )

    def _resolve_previous(self, ctx, today: date) -> tuple[date | None, date | None]:
        """定出上一交易日，并说明攒的那个是不是过期了。

        口径是「有攒的就用攒的」，但攒的是**上次真正跑过的那天**，不等于
        **上一个交易日**——中间漏掉一天，两者就分叉了。2026-09-30 影子盘就这样：
        09-29 因对账停机没跑完 session_start，状态停在 09-28，于是今天拿前天的
        首板去选次日票，候选 27 只而模拟盘 44 只（09-28 首板 28 只、09-29 有 46 只）。
        不报错、不告警，静默地在错误的股票池上交易。

        所以攒的值要和日历对一次：一致就照常用攒的；**不一致说明漏了交易日，
        以日历为准**并回传过期值供调用方告警。日历答不上来（新账户第一天、
        早于日历范围）时仍然用攒的——那正是当初要攒它的原因。

        返回 (用哪天, 攒的那个过期值或 None)。
        """
        remembered = ctx.state.get("previous_session")
        from_calendar = self._previous_session(ctx, today)
        if from_calendar is None:
            return remembered, None
        if remembered is None or remembered == from_calendar:
            return from_calendar, None
        return from_calendar, remembered

    def _previous_session(self, ctx, today: date) -> date | None:
        """交易日历给出的上一交易日。

        买的是**首板次日**，选股必须先知道 T-1。原版长期连续运行，从不遇到
        「第一天」，移植过来后新账户、状态重置与回测区间首日都会空转一天。
        日历优先取运行时注入的 `ctx.calendar`——模拟账户跑的是创建时存档的源码，
        构造参数改不动它。日历拿不到（早于日历范围）时仍然不选股，不猜。
        """
        calendar = getattr(ctx, "calendar", None) or self._calendar
        if calendar is None:
            return None
        try:
            return calendar.prev_session(today)
        except (ValueError, IndexError):
            return None

    def on_fill(self, ctx, fill: Fill) -> None:
        """用真实成交维护分支 lot；卖出按 FIFO 核销，状态可跨重启恢复。"""
        key = str(fill.symbol)
        lots = ctx.state.setdefault("lots", {}).setdefault(key, [])
        if fill.side is Side.BUY:
            tag = ctx.state.get("tags", {}).get(key)
            if tag is None:
                log.warning("structural.fill_without_tag", symbol=key, qty=fill.qty)
                return
            lots.append({
                "qty": fill.qty, "entry_price": fill.price, "tag": tag,
                "acquired_on": fill.filled_at.date().isoformat(),
            })
            return
        remaining = fill.qty
        for lot in lots:
            if remaining <= 0:
                break
            consumed = min(int(lot["qty"]), remaining)
            lot["qty"] = int(lot["qty"]) - consumed
            remaining -= consumed
        ctx.state["lots"][key] = [lot for lot in lots if int(lot["qty"]) > 0]

    # ---- 退出 ----

    def on_checkpoint(self, ctx, at: time) -> None:
        """盘中离场判定。

        逐只上报结论（`ctx.report`）——**只记录，不参与判定**。有持仓的那几天，
        11:25 / 14:25 / 14:50 正是对钱做决定的时刻，「看了没卖」和「没看」
        在消息上长得一样是不行的（2026-10-03 按交易者视角补）。
        """
        tags = ctx.state.get("tags", {})
        held = sorted([
            s for s in ctx.portfolio.symbols
            if ctx.portfolio.position(s).available_qty > 0
        ], key=str)
        if not held:
            return

        outcomes: list[str] = []
        today = ctx.now.date()
        symbols = [str(s) for s in held]
        quotes = ctx.market.intraday(symbols, today, at)
        limits = ctx.market.limit_up_price(symbols, today)

        for symbol in held:
            key = str(symbol)
            tag = tags.get(key)
            lots = [
                lot for lot in ctx.state.get("lots", {}).get(key, [])
                if int(lot["qty"]) > 0
                and str(lot.get("acquired_on", "")) < today.isoformat()
            ]
            if tag is None and not lots:
                # 不知道该按哪条规则退，宁可不动
                outcomes.append(f"{key} 无分档记录，不动")
                continue
            quote = quotes.get(key)
            if quote is None:
                # 拿不到当时价格就不能判，绝不用别的价格顶替。
                # 但**必须出声**：静默跳过会让持仓永久卡死。实测 603809.SH
                # 因分钟线只覆盖到买入后第 3 天，从 2025-12-26 一直挂到区间
                # 末尾，把满仓策略的现金抽干，连累后面 16 笔该买的没买成。
                log.warning(
                    "structural.no_quote", symbol=key, at=str(at), dt=str(today),
                    qty=ctx.portfolio.position(symbol).available_qty,
                )
                outcomes.append(f"{key} 取不到盘中价，无法判定（持仓会卡住）")
                continue

            position = ctx.portfolio.position(symbol)
            if lots:
                exit_qty = 0
                reasons = []
                for lot in lots:
                    decision = self._decide(
                        ctx, at, str(lot["tag"]), key, today, quote.price,
                        limits.get(key), float(lot["entry_price"]),
                    )
                    if decision is not None and decision.should_exit:
                        exit_qty += int(lot["qty"])
                        reasons.append(decision.reason)
                exit_qty = min(exit_qty, position.available_qty)
                if exit_qty <= 0:
                    outcomes.append(_holding(key, "lot", quote.price, position.avg_cost))
                    continue
                ctx.order.submit(symbol, Side.SELL, exit_qty)
                outcomes.append(
                    f"{key} 卖出 {exit_qty} 股　{'、'.join(sorted(set(reasons)))}"
                )
                log.info("structural.exit", symbol=key, tag="lot", at=str(at),
                         reason=",".join(sorted(set(reasons))), qty=exit_qty)
            else:
                decision = self._decide(
                    ctx, at, tag, key, today, quote.price,
                    limits.get(key), position.avg_cost,
                )
                if decision is None or not decision.should_exit:
                    outcomes.append(_holding(key, tag, quote.price, position.avg_cost,
                                             applies=decision is not None))
                    continue
                ctx.order.submit(symbol, Side.SELL, position.available_qty)
                outcomes.append(
                    f"{key} 卖出 {position.available_qty} 股　{decision.reason}"
                )
                log.info(
                    "structural.exit", symbol=key, tag=tag, at=str(at),
                    reason=decision.reason, ret=decision.ret,
                )

        ctx.report(note=f"{at:%H:%M} 检查 {len(held)} 只｜" + "；".join(outcomes))

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
