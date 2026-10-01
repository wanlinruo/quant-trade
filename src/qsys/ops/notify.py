"""通知：把已经产生的运行记录与告警送到人眼前。

和 ``qsys.ops.alerts`` 的分工：alerts 管「怎么送」（sink、失败隔离），
这里管「送不送、送什么」。

**只读不写**：所有判断都基于已经落盘的运行记录，不参与也不改变交易流程。
发送失败由 AlertBus 吞掉——通知挂了绝不能连累交易。

三个引擎每天各 7 个事件，全推是骚扰。只在四种情形推（2026-09-29 与维护者定）：

- 有实际买卖：下单、成交或拒单
- 竞价决策结果：每天一条，即使不交易也推，好确认策略在跑
- 策略出错
- 收盘汇总
"""

from __future__ import annotations

from dataclasses import dataclass

from qsys.ops.alerts import AlertBus
from qsys.ops.strategy_log import RunRecord

# 这两个阶段每天固定推一条；其余阶段只有出事或有买卖才推
_ALWAYS_PHASES = frozenset({"auction", "session_end"})
# 盘前这条只在「今天有事要做」时推：T+1 策略手里有票就必须今天卖掉。
# 空仓的早晨没有可反应的事，十分钟后的竞价决策那条已经说明策略活着。
_PREMARKET_PHASE = "session_start"

BUY, SELL = "买入", "卖出"


@dataclass(frozen=True, slots=True)
class Trade:
    """一笔委托或成交。交易者在手机上最先看的就是这几个字段。"""

    symbol: str
    side: str
    qty: int
    price: float

    @property
    def amount(self) -> float:
        return self.qty * self.price

    def as_line(self) -> str:
        return (f"{self.side} {self.symbol} {self.qty} 股 @ {self.price:,.2f}"
                f"（{self.amount:,.0f} 元）")


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    qty: int
    cost: float
    price: float

    @property
    def pnl(self) -> float:
        return (self.price - self.cost) * self.qty

    @property
    def pnl_pct(self) -> float:
        return (self.price / self.cost - 1) if self.cost else 0.0

    def as_line(self) -> str:
        return (f"{self.symbol} {self.qty} 股　成本 {self.cost:,.2f}　"
                f"现价 {self.price:,.2f}　浮盈 {self.pnl:+,.0f}"
                f"（{self.pnl_pct:+.2%}）")


@dataclass(frozen=True, slots=True)
class TradeContext:
    """运行记录之外、交易者需要的细节。

    `RunRecord` 存的是计数（`orders=2`），够做统计，不够做决策——手机上看到
    「下单 2 笔」是没法判断要不要干预的。这些字段不入库，只用来组织消息。
    """

    orders: tuple[Trade, ...] = ()
    fills: tuple[Trade, ...] = ()
    rejects: tuple[tuple[str, str], ...] = ()      # (标的, 原因)
    positions: tuple[Position, ...] = ()
    prev_total_value: float | None = None


_SIDE_WORDS = {"buy": BUY, "sell": SELL}


def trade_of(side, symbol, qty: int, price: float | None) -> Trade:
    """把内部的 Fill / Order / Reject 折成一条人读的交易。

    价格可能缺（市价委托还没成交），记 0 比编一个数诚实。
    """
    return Trade(str(symbol), _SIDE_WORDS.get(str(side), str(side)),
                 int(qty), float(price or 0.0))


def positions_of(positions, prices) -> tuple[Position, ...]:
    """组合持仓 → 消息里的持仓明细。没有现价时退回成本价，浮盈记 0。"""
    out = []
    for symbol, position in positions.items():
        if position.total_qty <= 0:
            continue
        cost = position.avg_cost
        out.append(Position(str(symbol), position.total_qty, cost,
                            float(prices.get(symbol, cost))))
    return tuple(sorted(out, key=lambda p: p.symbol))


def should_notify(record: RunRecord, context: TradeContext | None = None) -> bool:
    if record.error:
        return True
    if record.orders or record.rejects:
        return True
    if record.phase == _PREMARKET_PHASE:
        return bool(context and context.positions)
    return record.phase in _ALWAYS_PHASES


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _trade_title(trades: tuple[Trade, ...], orders: int) -> str:
    """买卖分开说。T+1 策略同一天可能既卖昨天的、又买今天的，
    「下单 2 笔」分不清是建仓还是离场——而离场才是钱回来的时刻。"""
    buys = sum(1 for t in trades if t.side == BUY)
    sells = len(trades) - buys
    if not trades:
        return f"下单 {orders} 笔"
    if buys and sells:
        return f"买入 {buys} 只，卖出 {sells} 只"
    return f"买入 {buys} 只" if buys else f"卖出 {sells} 只"


def _pnl_line(record: RunRecord, context: TradeContext) -> str | None:
    before = context.prev_total_value
    if before is None or before <= 0:
        return None
    change = record.total_value - before
    return f"当日盈亏 {change:+,.2f}（{change / before:+.2%}）"


def compose(name: str, record: RunRecord,
            context: TradeContext | None = None) -> tuple[str, str]:
    """(标题, 正文)。标题要能在通知列表里一眼看出发生了什么。"""
    context = context or TradeContext()
    if record.error:
        title = f"{name}｜{record.phase} 出错"
        return title, f"{record.event_key}\n{record.error}"

    trades = context.fills or context.orders
    if record.orders or record.rejects:
        title = f"{name}｜{_trade_title(trades, record.orders)}"
        if record.rejects:
            title += f"，拒单 {record.rejects} 笔"
    elif record.phase == _PREMARKET_PHASE:
        title = f"{name}｜盘前就位"
    elif record.phase == "auction":
        title = f"{name}｜竞价决策"
    else:
        title = f"{name}｜收盘"

    lines: list[str] = []
    if record.phase == _PREMARKET_PHASE:
        lines.append("今天手里有票，开盘后要处理")
    elif record.phase == "session_end" and trades:
        # 收盘报的是**整天**。照抄事件级摘要会写出「无下单」，而下面正列着
        # 当日成交——自相矛盾（2026-10-01 预览真实消息时发现）。
        lines.append(f"今日成交 {len(trades)} 笔")
    else:
        lines.append(record.summary or "（无摘要）")
    if record.reasons:
        lines.append("　" + "　".join(
            f"{reason} {count}" for reason, count in record.reasons.items()
        ))

    # 具体买了什么、卖了什么——手机上看到「下单 2 笔」是没法判断要不要干预的
    lines += [f"　{trade.as_line()}" for trade in trades]
    lines += [f"　拒单 {symbol}：{reason}" for symbol, reason in context.rejects]

    if context.positions and record.phase in (_PREMARKET_PHASE, "session_end"):
        lines.append(f"持仓 {len(context.positions)} 只：")
        lines += [f"　{position.as_line()}" for position in context.positions]

    lines.append(
        f"总资产 {_money(record.total_value)}（现金 {_money(record.cash)}，"
        f"持仓 {record.position_count} 只）"
    )
    pnl = _pnl_line(record, context)
    if pnl and record.phase == "session_end":
        lines.append(pnl)
    lines.append(f"{record.session} {record.phase}")
    return title, "\n".join(lines)


class StrategyNotifier:
    """把运行记录按上面的规则推出去。alerts 为 None 时整体静默。"""

    def __init__(self, alerts: AlertBus | None, name: str) -> None:
        self._alerts = alerts
        self._name = name

    def on_run(self, record: RunRecord,
               context: TradeContext | None = None) -> bool:
        """返回是否真的发了，便于调用方留痕与测试。"""
        if self._alerts is None or not should_notify(record, context):
            return False
        title, detail = compose(self._name, record, context)
        if record.error:
            self._alerts.critical(title, detail)
        elif record.rejects:
            self._alerts.warning(title, detail)
        else:
            self._alerts.info(title, detail)
        return True

