"""Real-time strategy scheduler and durable virtual-account runtime."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from pathlib import Path

import structlog

from qsys.backtest.runner import corporate_actions, cost_model
from qsys.core.clock import Phase
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Order
from qsys.core.types import Bar, Symbol
from qsys.data.cutover import CutoverConfig, CutoverMarketData
from qsys.live.messages import MarketQuote
from qsys.markets.ashare import ashare_pipeline, board_of
from qsys.paper.broker import PaperBroker
from qsys.paper.quotes import QuoteClient
from qsys.paper.store import PaperStore
from qsys.strategy.context import Context, PortfolioView
from qsys.strategy.deps import build_universe
from qsys.strategy.loader import StrategyDeps, load_strategy
from qsys.strategy.orders import OrderApi
from qsys.strategy.schedule import Schedule
from qsys.data.feed import HistoricalFeed
from qsys.data.market_feed import MarketFeed
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.ops.strategy_log import RunReport, build_record


log = structlog.get_logger(__name__)


# 行情刷新与盘中权益只在这段时间有意义：开盘事件 09:15 到收盘结算事件 15:30。
# 窗口外刷新既拿不到新价格，又把账户挂在 QMT 可用性上（2026-09-17 00:49 的事故）。
QUOTE_WINDOW = (time(9, 15), time(15, 30))


class DirectLimitCache:
    """当日直取涨跌停价（price_limits_daily，盘前写入的 Tushare stk_limit）。

    模拟盘与实盘共用。盘前任务可能晚到：当日还是空表时每次都重读，读到后缓存到换日。
    """

    def __init__(self, market_data: CutoverMarketData) -> None:
        self._market_data = market_data
        self._cached: tuple[date, dict[str, tuple[float, float]]] | None = None

    def on(self, day: date) -> dict[str, tuple[float, float]]:
        if self._cached is None or self._cached[0] != day or not self._cached[1]:
            frame = self._market_data.price_limits(None, day, day)
            self._cached = (day, {
                row["symbol"]: (float(row["limit_up"]), float(row["limit_down"]))
                for row in frame.iter_rows(named=True)
                if row["limit_up"] is not None and row["limit_down"] is not None
            })
        return self._cached[1]


class RealtimeClock:
    def __init__(self) -> None:
        self._now: datetime | None = None

    @property
    def now(self) -> datetime:
        if self._now is None:
            raise RuntimeError("paper clock has not started")
        return self._now

    def set(self, value: datetime) -> None:
        self._now = value


class RealtimeMarketFeed:
    """Historical derived data plus a short-lived QMT quote overlay for today."""

    def __init__(self, historical: MarketFeed, quotes: QuoteClient) -> None:
        self._historical, self._quotes = historical, quotes
        self._cache: dict[str, MarketQuote] = {}

    def bind(self, as_of: datetime):
        return RealtimeBoundMarket(self, self._historical.bind(as_of), as_of)

    def get(self, symbols: Sequence[str]) -> dict[str, MarketQuote]:
        got = self._quotes.request(symbols)
        self._cache.update(got)
        return got

    @property
    def cache(self) -> Mapping[str, MarketQuote]:
        return self._cache


class RealtimeBoundMarket:
    def __init__(self, owner: RealtimeMarketFeed, historical, as_of: datetime) -> None:
        self._owner, self._historical, self._as_of = owner, historical, as_of

    def flags(self, symbols, start, end):
        return self._historical.flags(symbols, start, end)

    def regime(self, start, end):
        return self._historical.regime(start, end)

    def valuation(self, symbols, start, end):
        return self._historical.valuation(symbols, start, end)

    def _today(self, symbols, day):
        if day != self._as_of.date():
            return {}
        return self._owner.get(symbols)

    def session_open(self, symbols, day):
        return {s: q.open for s, q in self._today(symbols, day).items() if q.open > 0}

    def intraday(self, symbols, day, at):
        from qsys.data.market_feed import IntradayQuote
        return {s: IntradayQuote(q.price, q.volume // 100)
                for s, q in self._today(symbols, day).items() if q.price > 0}

    def limit_up_price(self, symbols, day):
        """当日涨停价读仓库 price_limits_daily（盘前写入的 Tushare stk_limit），与回测同源。

        QMT get_full_tick 不给涨停价：早先读 quote.upper_limit，盘中永远为空，
        退出逻辑因此把封死涨停的持仓也当成「未涨停」卖掉（D-19）。
        """
        return self._historical.limit_up_price(symbols, day)

    def suspended_symbols(self, symbols, day):
        return {s for s, q in self._today(symbols, day).items() if q.suspended}

    def auction_volume(self, symbols, day):
        return {s: q.volume // 100 for s, q in self._today(symbols, day).items()}


class PaperRuntime:
    """One account runtime. ``tick`` is safe to call repeatedly."""

    GRACE_SECONDS = 90

    def __init__(
        self, account_id: str, store: PaperStore, warehouse: Path,
        calendar, quote_client: QuoteClient, workspace: Path,
    ) -> None:
        self.id, self.store, self.calendar = account_id, store, calendar
        account = store.account(account_id)
        if account is None:
            raise LookupError("模拟账户不存在")
        wh = Warehouse(warehouse)
        query = WarehouseQuery(wh)
        market_data = CutoverMarketData(query, CutoverConfig.from_env())
        feed = HistoricalFeed(query, calendar, market_data)
        historical_market = MarketFeed(query, calendar, market_data)
        self.market = RealtimeMarketFeed(historical_market, quote_client)
        instruments = market_data.instruments()
        self.universe, raw_boards = build_universe(instruments)
        self.boards = {Symbol.parse(k): v for k, v in raw_boards.items()}
        deps = StrategyDeps(feed=feed, market=self.market, query=query,
                            calendar=calendar, params=dict(account.config.get("params", {})))
        root = workspace / "paper" / account_id
        root.mkdir(parents=True, exist_ok=True)
        strategy_file = root / "strategy.py"
        strategy_file.write_text(store.source(account_id), encoding="utf-8")
        self.strategy = load_strategy(strategy_file, deps)
        self.feed, self.query = feed, query
        self.portfolio = store.portfolio(account_id)
        self.state = dict(account.state)
        self.clock = RealtimeClock()
        fees, slippage, _ = cost_model(bool(account.config.get("jq_compat", False)))
        self.broker = PaperBroker(
            self.portfolio, ashare_pipeline(), fees, slippage, volume_cap=0.05
        )
        for order in store.pending_orders(account_id):
            self.broker.restore(order, order.qty)
        self.last_prices = {
            Symbol.parse(row["symbol"]): float(row["last_price"])
            for row in store.rows(account_id, "positions")
        }
        self.order_seq: dict[str, int] = {}
        self._limits = DirectLimitCache(market_data)
        self.actions = corporate_actions(query, date.today(), date.today())
        schedule = Schedule()
        self.clock.set(datetime.now())
        self.strategy.on_init(self._context(schedule)[0])
        self.schedule = schedule

    def _context(self, schedule=None, report=None):
        view = PortfolioView.of(self.portfolio, self.last_prices)
        api = OrderApi(view, self.last_prices)
        return Context(
            self.clock, self.feed, view, api,
            params=dict(self.store.account(self.id).config.get("params", {})),
            state=self.state, market=self.market, schedule=schedule, report=report,
            calendar=self.calendar,
        ), api

    def _events(self, day: date):
        events = [(time(9, 15), Phase.SESSION_START)]
        if self.schedule.auction_at is not None:
            events.append((self.schedule.auction_at, Phase.AUCTION))
        events.extend((at, Phase.CHECKPOINT) for at in self.schedule.checkpoints)
        events.extend([(time(15, 0), Phase.BAR), (time(15, 30), Phase.SESSION_END)])
        return sorted(events, key=lambda item: item[0])

    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        account = self.store.account(self.id)
        if account is None or account.status not in ("scheduled", "running"):
            return
        if now.date() < date.fromisoformat(account.activation_date):
            return
        if not self.calendar.is_trading_day(now.date()):
            return
        self.store.mark_running(self.id)
        for at, phase in self._events(now.date()):
            moment = datetime.combine(now.date(), at)
            if moment > now:
                continue
            key = f"{now.date()}:{phase.value}:{at.isoformat()}"
            if not self.store.begin_event(self.id, key, moment, phase.value):
                continue
            late = (now - moment).total_seconds() > self.GRACE_SECONDS
            if late and phase is not Phase.SESSION_START:
                failure = self.store.event_detail(self.id, key)
                detail = "进程恢复时事件已过期，未补跑"
                if failure:
                    detail += f"；最后错误：{failure}"
                self.store.fail_event(self.id, key, detail)
                expired = RunReport()
                expired.report(note=detail)
                self._record_run(
                    key, moment, phase, expired, datetime.now(),
                    orders=0, rejects=0,
                    error=failure or "策略事件超过执行宽限",
                )
                continue
            self._run_event(key, moment, phase, skip_strategy=late)
        if QUOTE_WINDOW[0] <= now.time() <= QUOTE_WINDOW[1]:
            self._refresh_quotes(now)

    def _run_event(self, key: str, moment: datetime, phase: Phase,
                   *, skip_strategy: bool = False) -> None:
        started = datetime.now()
        report = RunReport()
        self.clock.set(moment)
        try:
            if phase is Phase.SESSION_START:
                self.broker.begin_session()
                self.portfolio.unfreeze()
                if moment.date() in self.actions:
                    from qsys.backtest.corporate import apply_corporate_actions

                    apply_corporate_actions(
                        self.portfolio, self.actions[moment.date()]
                    )
            if skip_strategy:
                report.report(note="仅执行账户结算；策略事件未补跑")
                self.store.commit_event(
                    self.id, key, self.portfolio, self.state, self.last_prices,
                    at=moment, detail=report.note,
                )
                self._record_run(
                    key, moment, phase, report, started,
                    orders=0, rejects=0, error="",
                )
                return

            ctx, api = self._context(report=report)
            if phase is Phase.SESSION_START:
                self.strategy.on_session_start(ctx)
            elif phase is Phase.AUCTION:
                self.strategy.on_auction(ctx)
            elif phase is Phase.CHECKPOINT:
                self.strategy.on_checkpoint(ctx, moment.time())
            elif phase is Phase.BAR:
                quotes = self.market.get([str(s) for s in self.universe])
                bars = {
                    Symbol.parse(symbol): _bar(quote, moment.date())
                    for symbol, quote in quotes.items()
                    if quote.price > 0 and not quote.suspended
                }
                for symbol, bar in bars.items():
                    self.last_prices[symbol] = bar.close
                self.strategy.on_bar(ctx, bars)
            else:
                self.strategy.on_session_end(ctx)
            orders = self._flush(api, key, moment)
            rejects = self._record_results()
            if phase is Phase.SESSION_END:
                self.broker.cancel_all(moment, "session ended: unfilled order cancelled")
                rejects += self._record_results()
            self.store.commit_event(
                self.id, key, self.portfolio, self.state, self.last_prices,
                at=moment,
            )
            self._record_run(
                key, moment, phase, report, started,
                orders=orders, rejects=rejects, error="",
            )
        except Exception as exc:
            try:
                self.store.record_event_failure(self.id, key, str(exc))
            except Exception as store_exc:
                log.warning(
                    "strategy_event.failure_record_failed",
                    account_id=self.id, event_key=key, error=str(store_exc),
                )
            self._record_run(
                key, moment, phase, report, started,
                orders=0, rejects=0, error=str(exc),
            )
            raise

    def _record_run(
        self,
        key: str,
        moment: datetime,
        phase: Phase,
        report: RunReport,
        started: datetime,
        *,
        orders: int,
        rejects: int,
        error: str,
    ) -> None:
        """写运行记录；记录失败只能告警，不能影响交易。"""
        try:
            market_value = sum(
                position.total_qty
                * self.last_prices.get(symbol, position.avg_cost)
                for symbol, position in self.portfolio.positions.items()
            )
            self.store.record_run(
                self.id,
                build_record(
                    report,
                    event_key=key,
                    session=moment.date(),
                    phase=phase.value,
                    started_at=started,
                    finished_at=datetime.now(),
                    cash=self.portfolio.cash,
                    market_value=market_value,
                    position_count=len(self.portfolio.positions),
                    orders=orders,
                    rejects=rejects,
                    error=error,
                ),
            )
        except Exception as exc:
            log.warning(
                "strategy_run.record_failed",
                account_id=self.id,
                event_key=key,
                error=str(exc),
            )

    def _flush(self, api: OrderApi, event_key: str, moment: datetime) -> int:
        orders = []
        for intent in api.drain():
            index = self.order_seq.get(event_key, 0) + 1
            self.order_seq[event_key] = index
            order = Order(
                f"{self.id}:{event_key}:{index}", intent.symbol, intent.side,
                intent.qty, intent.order_type, intent.limit_price, moment,
            )
            self.broker.submit(order)
            orders.append(order)
        self.store.record_orders(self.id, event_key, orders)
        return len(orders)

    def _refresh_quotes(self, now: datetime) -> None:
        symbols = {str(s) for s in self.portfolio.positions}
        symbols.update(str(order.symbol) for order in self.broker.pending())
        symbols.add("000300.SH")
        if not symbols:
            return
        quotes = self.market.get(sorted(symbols))
        limits = self._limits_on(now.date())
        for symbol, quote in quotes.items():
            if quote.price > 0:
                parsed = Symbol.parse(symbol)
                self.last_prices[parsed] = quote.price
                self.broker.on_quote(
                    quote, limits=limits.get(symbol),
                    board=self.boards.get(parsed, board_of(parsed.code)),
                )
        self._record_results()
        benchmark = quotes.get("000300.SH")
        benchmark_value = None
        account = self.store.account(self.id)
        if benchmark and benchmark.prev_close > 0:
            benchmark_value = account.initial_cash * benchmark.price / benchmark.prev_close
        self._save(now, benchmark_value)

    def _limits_on(self, day: date) -> dict[str, tuple[float, float]]:
        return self._limits.on(day)

    def _record_results(self) -> int:
        fills, rejects = self.broker.poll_fills(), self.broker.poll_rejects()
        for fill in fills:
            ctx, api = self._context()
            self.strategy.on_fill(ctx, fill)
            self._flush(api, f"fill:{fill.order_id}:{fill.filled_at.isoformat()}",
                        fill.filled_at)
        remaining = {order.order_id: self.broker.remaining(order.order_id)
                     for order in self.broker.pending()}
        self.store.record_results(self.id, fills, rejects, remaining)
        return len(rejects)

    def _save(self, at: datetime, benchmark_value: float | None = None) -> None:
        self.store.save_snapshot(
            self.id, self.portfolio, self.state, self.last_prices,
            at=at, benchmark_value=benchmark_value,
        )


def _bar(quote: MarketQuote, day: date) -> Bar:
    return Bar(
        Symbol.parse(quote.symbol), day, quote.open or quote.price,
        quote.high or quote.price, quote.low or quote.price, quote.price,
        quote.volume // 100, quote.amount,
    )
