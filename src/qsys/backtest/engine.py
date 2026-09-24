"""回测驱动器。

每个交易日的执行顺序是正确性的核心，顺序错了整个回测就错了：

  1. unfreeze()                     昨日及更早买入的份额解冻
  2. 应用当日除权除息                 持仓与现金调整
  3. on_session_start(ctx)          盘前，as_of = 09:15
  4. broker.on_market_data()        撮合昨日订单 @ 今日开盘
  5. 应用 fills + on_fill           今日成交**不**解冻（T+1 的物理基础）
  6. on_bar(ctx, bars)              as_of = 15:00，下单待明日成交
  7. on_session_end(ctx)            as_of = 15:30
  8. 记录当日净值

引擎持有全区间行情属基础设施；策略只能通过 ctx.data 取数，
其 as_of 恒等于 ctx.now——引擎绝不把未来 bar 交给策略。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Callable

import polars as pl
import structlog

from qsys.backtest.corporate import CorporateAction, apply_corporate_actions
from qsys.backtest.entry_fill import EntryFill, EntryFillMode, MinuteBar, decide
from qsys.backtest.fees import FeeModel
from qsys.backtest.sim_broker import SimBroker
from qsys.backtest.slippage import SlippageModel
from qsys.core.calendar import TradingCalendar
from qsys.core.clock import BacktestClock, Phase
from qsys.core.constraints import ConstraintPipeline
from qsys.core.events import MarketSnapshot, Reject
from qsys.core.portfolio import Portfolio
from qsys.strategy.schedule import Schedule
from qsys.core.trading import Fill, Order, Side, to_cents
from qsys.core.types import Bar, Symbol

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BacktestResult:
    equity: pl.DataFrame
    fills: tuple[Fill, ...]
    rejects: tuple[Reject, ...]
    run_id: str
    params: Mapping[str, object] = field(default_factory=dict)
    # 策略在 ctx.state 里攒下的东西。落盘要用它还原每笔买入的分支标签——
    # Fill 本身不带标签，那是策略层的概念。
    state: Mapping[str, object] = field(default_factory=dict)
    orders: tuple[Order, ...] = ()
    positions: pl.DataFrame = field(default_factory=lambda: pl.DataFrame(schema={
        "dt": pl.Date, "symbol": pl.Utf8, "qty": pl.Int64,
        "available_qty": pl.Int64, "avg_cost": pl.Float64,
        "close": pl.Float64, "market_value": pl.Float64,
        "weight": pl.Float64, "unrealized_pnl": pl.Float64,
    }))
    # 竞价入场口径的统计：{"orders", "open"/"slippage"/"continuous"/"missed"/
    # "fallback_no_minute" 计数}。未配入场口径时为空。
    diagnostics: Mapping[str, object] = field(default_factory=dict)

    @property
    def final_value(self) -> float:
        if self.equity.height == 0:
            return 0.0
        return float(self.equity["total_value"].to_list()[-1])


class BacktestEngine:
    def __init__(
        self,
        strategy,
        *,
        feed,
        calendar: TradingCalendar,
        universe: Sequence[Symbol],
        initial_cash: float,
        pipeline: ConstraintPipeline,
        fees: FeeModel,
        slippage: SlippageModel,
        price_limits: Callable[[date, date], Mapping[date, Mapping[Symbol, tuple[float, float]]]],
        volume_cap: float = 0.05,
        corporate_actions: Mapping[date, Sequence[CorporateAction]] | None = None,
        params: Mapping[str, object] | None = None,
        boards: Mapping[Symbol, str] | None = None,
        checkpoints: Sequence[time] = (),
        auction_at: time | None = None,
        intraday: object | None = None,
        market: object | None = None,
        progress: Callable[[int, int, date], None] | None = None,
        schedule_validator: Callable[[tuple[time, ...], time | None], None] | None = None,
        entry_fill: EntryFill | None = None,
        entry_minutes: Callable[
            [date, time, Sequence[Symbol]], Mapping[Symbol, MinuteBar]
        ] | None = None,
    ) -> None:
        self._strategy = strategy
        self._feed = feed
        self._calendar = calendar
        self._universe = tuple(universe)
        self._initial_cash = initial_cash
        self._pipeline = pipeline
        self._fees = fees
        self._slippage = slippage
        # 当日直取涨跌停价（stk_limit）。必传：漏接会让买单全被「缺价」拒绝（D-19）
        self._price_limits = price_limits
        self._volume_cap = volume_cap
        self._actions = dict(corporate_actions or {})
        self._params = dict(params or {})
        self._boards = dict(boards or {})
        # 盘中检查点。不配则引擎行为与原来逐字节相同。
        self._checkpoints = tuple(sorted(checkpoints))
        # 配了竞价时刻，则开盘撮合推迟到那一刻之后——策略要先看到已结算的
        # 竞价价才能定量。不配则与原来完全一致。
        self._auction_at = auction_at
        # intraday(session, at, symbols) -> {symbol: (price, 截至该时刻的成交量)}
        self._intraday = intraday
        self._market = market
        self._progress = progress
        self._schedule_validator = schedule_validator
        # 竞价入场口径。不配则竞价买单按开盘价 + 通用滑点，与原来完全一致。
        if (entry_fill is not None and entry_fill.mode is EntryFillMode.CONTINUOUS
                and entry_minutes is None):
            raise ValueError("continuous entry fill needs entry_minutes")
        self._entry_fill = entry_fill
        self._entry_minutes = entry_minutes

    def _run_id(self, start: date, end: date) -> str:
        payload = json.dumps(
            {
                "strategy": type(self._strategy).__name__,
                "universe": sorted(str(s) for s in self._universe),
                "start": str(start),
                "end": str(end),
                "initial_cash": self._initial_cash,
                "volume_cap": self._volume_cap,
                "params": {k: str(v) for k, v in sorted(self._params.items())},
                # 只在配了检查点时才进入 run_id：不配时哈希必须与扩展前一致，
                # 否则既有回测的 run_id 全变，可复现性就断了。
                **(
                    {"checkpoints": [t.isoformat() for t in self._checkpoints]}
                    if self._checkpoints
                    else {}
                ),
                **(
                    {"auction_at": self._auction_at.isoformat()}
                    if self._auction_at is not None
                    else {}
                ),
                **(
                    {"entry_fill": {"mode": self._entry_fill.mode.value,
                                    "slippage": self._entry_fill.slippage,
                                    "minute_at": self._entry_fill.minute_at.isoformat()}}
                    if self._entry_fill is not None
                    else {}
                ),
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def _load_bars(self, start: date, end: date) -> dict[date, dict[Symbol, Bar]]:
        """预取全区间行情。这是引擎的基础设施，绝不交给策略。"""
        symbols = [str(s) for s in self._universe]
        as_of = self._calendar.close_time(self._calendar.sessions_in_range(start, end)[-1])
        df = self._feed.get(symbols, start, end, as_of=as_of)

        by_session: dict[date, dict[Symbol, Bar]] = {}
        for row in df.iter_rows(named=True):
            symbol = Symbol.parse(row["symbol"])
            by_session.setdefault(row["dt"], {})[symbol] = Bar(
                symbol=symbol, dt=row["dt"], open=row["open"], high=row["high"],
                low=row["low"], close=row["close"], volume=row["volume"],
                amount=row["amount"],
            )
        return by_session

    def _quotes_at(self, session: date, at: time, portfolio) -> dict[Symbol, tuple]:
        """检查点报价。只问持仓里的标的——检查点只做退出。"""
        if self._intraday is None:
            return {}
        held = [s for s, p in portfolio.positions.items() if p.total_qty > 0]
        if not held:
            return {}
        return dict(self._intraday(session, at, held) or {})

    def _checkpoint_snapshot(self, session, quotes, prev_closes, limits) -> MarketSnapshot:
        """把检查点报价包成快照。

        SimBroker 按 `bar.open` 撮合，故此处把该时刻的价格放进 open——
        约束、滑点、费用、成交量上限全部复用既有那一条路径，不另写撮合。
        """
        bars = {
            symbol: Bar(
                symbol=symbol, dt=session, open=price, high=price, low=price,
                close=price, volume=volume, amount=price * volume,
            )
            for symbol, (price, volume) in quotes.items()
        }
        return MarketSnapshot(
            session=session, bars=bars, prev_closes=dict(prev_closes),
            boards=self._boards, limits=limits,
        )

    def run(self, start: date, end: date) -> BacktestResult:
        from qsys.strategy.context import Context, PortfolioView
        from qsys.strategy.orders import OrderApi

        sessions = self._calendar.sessions_in_range(start, end)
        if not sessions:
            return BacktestResult(
                pl.DataFrame(schema={"dt": pl.Date, "cash": pl.Float64,
                                     "market_value": pl.Float64,
                                     "total_value": pl.Float64}),
                (), (), self._run_id(start, end), self._params,
            )

        bars_by_session = self._load_bars(start, end)
        limits_by_session = self._price_limits(start, end)
        portfolio = Portfolio(self._initial_cash)
        broker = SimBroker(portfolio, self._pipeline, self._fees, self._slippage,
                           volume_cap=self._volume_cap)
        clock = BacktestClock(
            self._calendar, start, end, self._checkpoints, self._auction_at
        )
        state: dict[str, object] = {}

        all_fills: list[Fill] = []
        all_rejects: list[Reject] = []
        all_orders: list[Order] = []
        equity_rows: list[dict[str, object]] = []
        position_rows: list[dict[str, object]] = []
        last_close: dict[Symbol, float] = {}
        prev_closes: dict[Symbol, float] = {}
        order_seq = 0
        entry_stats: dict[str, int] = {}

        def make_ctx(prices: Mapping[Symbol, float], schedule=None):
            view = PortfolioView.of(portfolio, prices)
            api = OrderApi(view, prices)
            return Context(clock, self._feed, view, api,
                           params=self._params, state=state,
                           market=self._market, schedule=schedule,
                           calendar=self._calendar), api

        def flush(api, at: datetime) -> None:
            nonlocal order_seq
            for intent in api.drain():
                order_seq += 1
                order = Order(f"o{order_seq}", intent.symbol, intent.side,
                              intent.qty, intent.order_type, intent.limit_price, at)
                all_orders.append(order)
                broker.submit(order)

        def entry_pricer(session: date, entries: Sequence[Order]):
            """竞价买单的定价器；未配入场口径或没有竞价买单时返回 None。"""
            rule = self._entry_fill
            if rule is None or not entries:
                return None
            minutes: Mapping[Symbol, MinuteBar] = {}
            if rule.mode is EntryFillMode.CONTINUOUS:
                minutes = self._entry_minutes(  # type: ignore[misc]
                    session, rule.minute_at, sorted({o.symbol for o in entries}, key=str)
                ) or {}
            ids = {o.order_id for o in entries}

            def price(order: Order, bar: Bar):
                if order.order_id not in ids:
                    return None
                decision = decide(rule, order, bar.open, minutes.get(order.symbol),
                                  self._slippage)
                entry_stats["orders"] = entry_stats.get("orders", 0) + 1
                entry_stats[decision.outcome] = entry_stats.get(decision.outcome, 0) + 1
                return decision.price, decision.reason

            return price

        def match_at_open(at: datetime, entries: Sequence[Order] = ()) -> None:
            session = at.date()
            snapshot = MarketSnapshot(
                session=session, bars=bars_by_session.get(session, {}),
                prev_closes=dict(prev_closes), boards=self._boards,
                limits=limits_by_session.get(session, {}),
            )
            broker.on_market_data(snapshot, pricer=entry_pricer(session, entries))
            all_rejects.extend(broker.poll_rejects())
            for fill in broker.poll_fills():
                portfolio.apply_fill(fill)
                all_fills.append(fill)
                fill_ctx, fill_api = make_ctx(last_close)
                self._strategy.on_fill(fill_ctx, fill)
                flush(fill_api, at)

        # 策略可以在 on_init 里自己声明调度（对应聚宽的 run_daily）。
        # 声明了就按它重建时钟；没声明则沿用构造时给的值，老策略不受影响。
        schedule = Schedule()
        self._strategy.on_init(make_ctx(last_close, schedule)[0])
        if schedule.declared:
            clock = BacktestClock(
                self._calendar, start, end,
                schedule.checkpoints or self._checkpoints,
                schedule.auction_at or self._auction_at,
            )
            # 就地改引擎上的调度：run_id 是在最后算的，把它一并算进去，
            # 换了调度就是另一次运行——这是对的。
            self._checkpoints = clock.checkpoints
            self._auction_at = clock.auction_at

        if self._schedule_validator is not None:
            self._schedule_validator(clock.checkpoints, clock.auction_at)

        for moment, phase in clock.ticks():
            session = moment.date()
            bars = bars_by_session.get(session, {})

            if phase is Phase.SESSION_START:
                portfolio.unfreeze()
                if session in self._actions:
                    apply_corporate_actions(portfolio, self._actions[session])

                ctx, api = make_ctx(last_close)
                self._strategy.on_session_start(ctx)
                flush(api, moment)

                if self._auction_at is None:
                    match_at_open(moment)

            elif phase is Phase.AUCTION:
                # 竞价已结算，当日 open 已确定；策略在此定量下单，
                # 随后按开盘价撮合——对应原策略的 run_daily(buy, '09:26')
                ctx, api = make_ctx(last_close)
                self._strategy.on_auction(ctx)
                submitted = len(all_orders)
                flush(api, moment)
                entries = [o for o in all_orders[submitted:] if o.side is Side.BUY]
                match_at_open(moment, entries)

            elif phase is Phase.CHECKPOINT:
                # 钩子总是触发：「没持仓」与「没分钟线」是两回事，不能混为一谈。
                # 撮合只按实际拿到的报价来——没有报价的标的会被 SimBroker 以
                # 「no bar for this session」拒单并留痕，绝不拿收盘价冒充。
                quotes = self._quotes_at(session, moment.time(), portfolio)
                prices = {s: q[0] for s, q in quotes.items()}
                ctx, api = make_ctx({**last_close, **prices})
                self._strategy.on_checkpoint(ctx, moment.time())
                flush(api, moment)

                broker.on_market_data(
                    self._checkpoint_snapshot(
                        session, quotes, prev_closes, limits_by_session.get(session, {})
                    ), moment
                )
                all_rejects.extend(broker.poll_rejects())
                for fill in broker.poll_fills():
                    portfolio.apply_fill(fill)
                    all_fills.append(fill)

            elif phase is Phase.BAR:
                for symbol, bar in bars.items():
                    last_close[symbol] = bar.close
                ctx, api = make_ctx(last_close)
                self._strategy.on_bar(ctx, bars)
                flush(api, moment)

            else:  # SESSION_END
                ctx, api = make_ctx(last_close)
                self._strategy.on_session_end(ctx)
                flush(api, moment)

                market = sum(
                    held.total_qty * last_close.get(symbol, 0.0)
                    for symbol, held in portfolio.positions.items()
                )
                equity_rows.append({
                    "dt": session,
                    "cash": portfolio.cash,
                    "market_value": to_cents(market),
                    "total_value": to_cents(portfolio.cash + market),
                })
                total = portfolio.cash + market
                for symbol, held in portfolio.positions.items():
                    close = last_close.get(symbol, 0.0)
                    value = to_cents(held.total_qty * close)
                    position_rows.append({
                        "dt": session, "symbol": str(symbol), "qty": held.total_qty,
                        "available_qty": held.available_qty, "avg_cost": held.avg_cost,
                        "close": close, "market_value": value,
                        "weight": value / total if total else 0.0,
                        "unrealized_pnl": to_cents((close - held.avg_cost) * held.total_qty),
                    })
                prev_closes = dict(last_close)
                if self._progress is not None:
                    self._progress(len(equity_rows), len(sessions), session)

        equity = pl.DataFrame(
            equity_rows,
            schema={"dt": pl.Date, "cash": pl.Float64, "market_value": pl.Float64,
                    "total_value": pl.Float64},
        )
        log.info("backtest.finished", sessions=len(sessions), fills=len(all_fills),
                 rejects=len(all_rejects), entry=entry_stats)
        positions = pl.DataFrame(position_rows, schema={
            "dt": pl.Date, "symbol": pl.Utf8, "qty": pl.Int64,
            "available_qty": pl.Int64, "avg_cost": pl.Float64,
            "close": pl.Float64, "market_value": pl.Float64,
            "weight": pl.Float64, "unrealized_pnl": pl.Float64,
        })
        return BacktestResult(equity, tuple(all_fills), tuple(all_rejects),
                              self._run_id(start, end), self._params, state,
                              tuple(all_orders), positions, dict(entry_stats))
