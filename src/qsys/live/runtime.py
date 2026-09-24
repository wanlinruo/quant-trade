"""Linux 实盘策略运行器：与 PaperRuntime 共用策略 API，Broker 边界不同。"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

import structlog

from qsys.backtest.runner import corporate_actions
from qsys.core.clock import Phase
from qsys.core.constraints import ConstraintContext
from qsys.core.order_ids import live_order_id
from qsys.core.trading import Order, OrderIntent, OrderType, Side
from qsys.core.types import Symbol
from qsys.data.cutover import CutoverConfig, CutoverMarketData
from qsys.data.feed import HistoricalFeed
from qsys.data.market_feed import MarketFeed
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.live.config import RiskConfig, StreamConfig
from qsys.live.heartbeat import HeartbeatMonitor
from qsys.live.kill_switch import KillSwitch
from qsys.live.live_broker import LiveBroker
from qsys.live.messages import AccountSnapshot, Heartbeat, MarketQuote
from qsys.live.pricing import protected_limit_price
from qsys.live.reconcile import reconcile_positions
from qsys.live.risk import RiskContext, default_ashare_gate
from qsys.live.shadow_broker import ShadowBroker
from qsys.live.store import LiveStore
from qsys.live.transport import Transport
from qsys.markets.ashare import ashare_pre_trade_pipeline, board_of
from qsys.paper.quotes import QuoteClient
from qsys.paper.runtime import DirectLimitCache, RealtimeClock, RealtimeMarketFeed, _bar
from qsys.strategy.context import Context, PortfolioView
from qsys.strategy.deps import StrategyDeps, build_universe
from qsys.strategy.loader import load_strategy
from qsys.strategy.orders import OrderApi
from qsys.strategy.schedule import Schedule
from qsys.ops.alerts import AlertBus, LogAlertSink
from qsys.ops.daily_report import build_daily_report
from qsys.ops.journal import SessionJournal
from qsys.ops.strategy_log import RunReport, build_record


log = structlog.get_logger(__name__)


class LivePreflightError(RuntimeError):
    pass


class LiveRuntime:
    # 事件超过宽限期就不再补跑。09:30 前后行情拥塞可能让一次请求超时，30 秒来不及
    # 重试就把当天的竞价事件判过期（2026-09-21 实测）；120 秒仍在 09:30 连续竞价
    # 之前，足够重试而不至于用过时的价格下单。
    GRACE_SECONDS = 120
    ACCOUNT_MAX_AGE = 5.0
    HEARTBEAT_MAX_AGE = 10.0

    def __init__(
        self,
        strategy_file: Path,
        warehouse: Path,
        calendar,
        quotes: QuoteClient,
        transport: Transport,
        streams: StreamConfig,
        store: LiveStore,
        *,
        params: dict | None = None,
        local_kill_path: Path = Path("var/KILL"),
        alerts: AlertBus | None = None,
        risk: RiskConfig | None = None,
        shadow: bool = False,
    ) -> None:
        self.transport, self.streams, self.store = transport, streams, store
        self.calendar, self.params = calendar, dict(params or {})
        risk = risk or RiskConfig()
        self.risk_config = risk
        self._fixed_position_budget = "max_position_value" in self.params
        remote = self._remote_account()
        remote_asset = float(remote.total_asset or remote.cash + remote.market_value)
        if remote_asset > risk.max_account_asset:
            raise LivePreflightError(
                f"账户总资产 {remote_asset:,.2f} 超过首期上限 "
                f"{risk.max_account_asset:,.2f}"
            )
        store.initialize(remote)
        self.portfolio, self.state, self.last_prices, self.day_start_value = store.load()
        self.enabled_capital = min(risk.enabled_capital, self.day_start_value)
        self.params.setdefault(
            "max_position_value",
            min(self.enabled_capital, self.day_start_value * risk.max_position_weight),
        )
        self.shadow = shadow
        self.kill_switch = KillSwitch(
            transport,
            key="shadow:control:kill" if shadow else "control:kill",
            local_path=local_kill_path,
        )
        self.alerts = alerts or AlertBus([LogAlertSink()])
        self.clock = RealtimeClock()
        self.clock.set(datetime.now())
        self._latest_quotes: dict[str, MarketQuote] = {}
        self.turnover_today = 0.0
        self.order_times: list[datetime] = []
        self.consecutive_rejects = 0
        self._previous_amounts: dict[tuple[date, str], float] = {}

        wh = Warehouse(warehouse)
        self.journal = SessionJournal(wh)
        query = WarehouseQuery(wh)
        market_data = CutoverMarketData(query, CutoverConfig.from_env())
        feed = HistoricalFeed(query, calendar, market_data)
        historical = MarketFeed(query, calendar, market_data)
        self.market = RealtimeMarketFeed(historical, quotes)
        instruments = market_data.instruments()
        self.universe, raw_boards = build_universe(instruments)
        self.boards = {Symbol.parse(key): value for key, value in raw_boards.items()}
        deps = StrategyDeps(
            feed=feed, market=self.market, query=query, calendar=calendar,
            params=self.params,
        )
        self.strategy = load_strategy(strategy_file, deps)
        self.feed, self.query = feed, query
        self._limits = DirectLimitCache(market_data)
        self.schedule = Schedule()
        self.strategy.on_init(self._context(self.schedule)[0])

        broker_kwargs = dict(
            risk_gate=default_ashare_gate(
                self.kill_switch, calendar,
                max_position_weight=risk.max_position_weight,
                max_daily_turnover=risk.max_daily_turnover,
                max_daily_loss=risk.max_daily_loss,
                max_price_deviation=risk.max_price_deviation,
                production_limits=True, max_order_value=risk.max_order_value,
                max_gross_exposure=risk.max_gross_exposure,
            ),
            risk_context_factory=self._risk_context,
            constraints=ashare_pre_trade_pipeline(),
            constraint_context_factory=self._constraint_context,
        )
        self.broker = (
            ShadowBroker(transport, streams, **broker_kwargs)
            if shadow else LiveBroker(
                transport, streams,
                **broker_kwargs,
            strategy_id=str(self.params.get("strategy_id", "structural")),
            command_ttl_seconds=120.0,
            )
        )
        for order, remaining in self.store.pending_orders():
            self.broker.restore(order, remaining)
        self.order_seq: dict[str, int] = {}
        self._assert_reconciled(remote)

    def _remote_account(self) -> AccountSnapshot:
        raw = self.transport.get_key(self.streams.account)
        if raw is None:
            raise LivePreflightError("券商账户快照不存在")
        snapshot = AccountSnapshot.model_validate_json(raw)
        age = (datetime.now() - snapshot.at).total_seconds()
        if age < -1 or age > self.ACCOUNT_MAX_AGE:
            raise LivePreflightError(f"券商账户快照已过期: {age:.1f}s")
        return snapshot

    def _assert_reconciled(self, remote: AccountSnapshot) -> None:
        result = reconcile_positions(self.portfolio, remote, cash_tolerance=10.0)
        if not result.is_consistent:
            self.journal.record_reconciliation(
                remote.at.date(), "intraday", is_consistent=False,
                cash_diff=result.cash_diff, n_mismatches=len(result.mismatches),
                detail=result.summary(), at=remote.at,
            )
            self.alerts.critical("实盘账户对账失败", result.summary())
            self.kill_switch.engage(result.summary())
            raise LivePreflightError(result.summary())
        self.journal.record_reconciliation(
            remote.at.date(), "intraday", is_consistent=True,
            cash_diff=result.cash_diff, at=remote.at,
        )

    def _preflight(self, *, require_market: bool = True) -> None:
        # kill 状态仍运行策略检查点：RiskGate 会拒绝所有买单但允许减仓。
        # 因而“停开仓”不会意外变成“把退出也锁死”。
        heartbeat = HeartbeatMonitor(self.transport, prefix=self.streams.heartbeat)
        required = ["agent"] + (["market"] if require_market else [])
        dead = heartbeat.dead_sources(
            required, max_age_seconds=self.HEARTBEAT_MAX_AGE
        )
        if dead:
            self.kill_switch.engage(f"heartbeat timeout: {','.join(dead)}")
            self.broker.cancel_all()
            self.alerts.critical("实盘心跳超时", ",".join(dead))
            raise LivePreflightError(f"heartbeat timeout: {','.join(dead)}")
        self._assert_reconciled(self._remote_account())

    def _quote(self, symbol: Symbol) -> MarketQuote:
        key = str(symbol)
        quote = self._latest_quotes.get(key)
        if quote is None:
            got = self.market.get([key])
            self._latest_quotes.update(got)
            quote = got.get(key)
        if quote is None:
            raise LivePreflightError(f"无实时行情: {key}")
        return quote

    def _risk_context(self, intent: OrderIntent) -> RiskContext:
        quote = self._quote(intent.symbol)
        reference = quote.ask1 if intent.side is Side.BUY else quote.bid1
        reference = reference or quote.price
        return RiskContext(
            portfolio=self.portfolio, now=self.clock.now,
            reference_price=float(intent.limit_price or reference),
            market_reference_price=reference,
            day_start_value=self.day_start_value,
            turnover_today=self.turnover_today,
            marks=dict(self.last_prices),
            frozen_cash=float(self._remote_account().frozen_cash),
            pending_buy_value=sum(
                order.qty * float(order.limit_price or reference)
                for order in self.broker.pending() if order.side is Side.BUY
            ) if hasattr(self, "broker") else 0.0,
            orders_last_minute=sum(
                stamp >= datetime.now() - timedelta(minutes=1)
                for stamp in self.order_times
            ),
            orders_today=len(self.order_times),
            cumulative_volume=quote.volume,
            previous_day_amount=self._previous_day_amount(intent.symbol),
        )

    def _previous_day_amount(self, symbol: Symbol) -> float:
        key = (self.clock.now.date(), str(symbol))
        if key in self._previous_amounts:
            return self._previous_amounts[key]
        frame = self.feed.get(
            [str(symbol)], self.clock.now.date() - timedelta(days=30),
            self.clock.now.date(), as_of=self.clock.now,
        )
        amount = 0.0
        if frame.height and "amount" in frame.columns:
            values = frame.sort("dt")["amount"].drop_nulls().to_list()
            if values:
                amount = float(values[-1])
        self._previous_amounts[key] = amount
        return amount

    def _constraint_context(self, intent: OrderIntent) -> ConstraintContext:
        quote = self._quote(intent.symbol)
        return ConstraintContext(
            portfolio=self.portfolio,
            reference_price=float(intent.limit_price or quote.price),
            prev_close=quote.prev_close or None,
            board=self.boards.get(intent.symbol, board_of(intent.symbol.code)),
            is_suspended=quote.suspended,
        )

    def _context(self, schedule=None, report=None):
        view = PortfolioView.of(self.portfolio, self.last_prices)
        deployed = sum(
            position.total_qty * self.last_prices.get(symbol, position.avg_cost)
            for symbol, position in self.portfolio.positions.items()
        )
        view = replace(
            view,
            cash=min(view.cash, max(0.0, self.enabled_capital - deployed)),
        )
        api = OrderApi(view, self.last_prices)
        return Context(
            self.clock, self.feed, view, api, params=self.params,
            state=self.state, market=self.market, schedule=schedule, report=report,
            calendar=self.calendar,
        ), api

    def _events(self):
        events = [(time(9, 15), Phase.SESSION_START)]
        if self.schedule.auction_at is not None:
            events.append((self.schedule.auction_at, Phase.AUCTION))
        events.extend((at, Phase.CHECKPOINT) for at in self.schedule.checkpoints)
        events.extend([(time(15, 0), Phase.BAR), (time(15, 30), Phase.SESSION_END)])
        return sorted(events, key=lambda item: item[0])

    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        self.clock.set(now)
        self.transport.set_key(
            f"{self.streams.heartbeat}:core",
            Heartbeat(source="core", at=now).model_dump_json(),
        )
        self._record_results()
        if not self.calendar.is_trading_day(now.date()):
            return
        for at, phase in self._events():
            moment = datetime.combine(now.date(), at)
            if moment > now:
                continue
            key = f"{now.date()}:{phase.value}:{at.isoformat()}"
            if not self.store.begin_event(key, moment, phase.value):
                continue
            late = (now - moment).total_seconds() > self.GRACE_SECONDS
            if late and phase is not Phase.SESSION_START:
                failure = self.store.event_detail(key)
                detail = "事件已过期，未补跑"
                if failure:
                    detail += f"；最后错误：{failure}"
                self.store.fail_event(key, detail, at=now)
                expired = RunReport()
                expired.report(note=detail)
                self._record_run(
                    key, moment, phase, expired, datetime.now(),
                    orders=0, rejects=0,
                    error=failure or "策略事件超过执行宽限",
                )
                continue
            self._run_event(key, moment, phase, skip_strategy=late)
        self._refresh_quotes(now)

    def _run_event(
        self, key: str, moment: datetime, phase: Phase, *, skip_strategy: bool
    ) -> None:
        started = datetime.now()
        report = RunReport()
        self.clock.set(moment)
        try:
            if phase is Phase.SESSION_START:
                self.turnover_today = 0.0
                self.order_times.clear()
                self.consecutive_rejects = 0
                remote = self._remote_account()
                self.day_start_value = float(remote.total_asset or self.day_start_value)
                self.enabled_capital = min(
                    self.risk_config.enabled_capital, self.day_start_value
                )
                if not self._fixed_position_budget:
                    self.params["max_position_value"] = min(
                        self.enabled_capital,
                        self.day_start_value * self.risk_config.max_position_weight,
                    )
                self.store.set_day_start_value(self.day_start_value, at=moment)
                settled = self.state.setdefault("live_settled_sessions", [])
                session_key = moment.date().isoformat()
                if session_key not in settled:
                    self.portfolio.unfreeze()
                    actions = corporate_actions(self.query, moment.date(), moment.date())
                    if moment.date() in actions:
                        from qsys.backtest.corporate import apply_corporate_actions

                        apply_corporate_actions(self.portfolio, actions[moment.date()])
                    settled.append(session_key)
                    # 事件若因对账失败重试，也不会重复应用公司行动。
                    self.store.save(
                        self.portfolio, self.state, self.last_prices, at=moment,
                    )
            self._preflight(require_market=not skip_strategy)
            if skip_strategy:
                report.report(note="仅完成日初结算；策略事件未补跑")
                self.store.commit_event(
                    key, self.portfolio, self.state, self.last_prices, at=moment,
                    detail=report.note,
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
                self.broker.cancel_all()
                if self.shadow:
                    self.store.record_statuses(self.broker.poll_statuses())
                remote = self._remote_account()
                result = reconcile_positions(self.portfolio, remote, cash_tolerance=10.0)
                # 影子意图从未到达券商，cancel_all 后自然没有真实在途委托。
                open_orders = () if self.shadow else self.store.pending_orders()
                eod_consistent = result.is_consistent and not open_orders
                detail = result.summary()
                if open_orders:
                    detail += (
                        f"; {len(open_orders)} orders still non-terminal after cancel request"
                    )
                self.journal.record_reconciliation(
                    moment.date(), "eod", is_consistent=eod_consistent,
                    cash_diff=result.cash_diff, n_mismatches=len(result.mismatches),
                    detail=detail, at=remote.at,
                )
                market_value = sum(
                    position.total_qty
                    * self.last_prices.get(symbol, position.avg_cost)
                    for symbol, position in self.portfolio.positions.items()
                )
                self.journal.record_equity(
                    moment.date(), cash=self.portfolio.cash, market_value=market_value,
                )
                daily_report = build_daily_report(self.journal, moment.date())
                publish = (
                    self.alerts.info if daily_report.is_healthy else self.alerts.critical
                )
                publish(daily_report.headline, daily_report.as_text())
            self.store.commit_event(
                key, self.portfolio, self.state, self.last_prices, at=moment
            )
            self._record_run(
                key, moment, phase, report, started,
                orders=orders, rejects=rejects, error="",
            )
        except Exception as exc:
            try:
                self.store.record_event_failure(key, str(exc))
            except Exception as store_exc:
                log.warning(
                    "strategy_event.failure_record_failed",
                    event_key=key, error=str(store_exc),
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
                position.total_qty * self.last_prices.get(symbol, position.avg_cost)
                for symbol, position in self.portfolio.positions.items()
            )
            self.store.record_run(
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
                )
            )
        except Exception as exc:
            log.warning("strategy_run.record_failed", event_key=key, error=str(exc))

    def _flush(self, api: OrderApi, event_key: str, moment: datetime) -> int:
        orders = []
        for intent in api.drain():
            quote = self._quote(intent.symbol)
            protected = protected_limit_price(
                quote, intent.side, limits=self._limits.on(moment.date()).get(str(intent.symbol)),
                now=datetime.now(),
            )
            if intent.limit_price is not None:
                protected = (
                    min(protected, intent.limit_price) if intent.side is Side.BUY
                    else max(protected, intent.limit_price)
                )
            index = self.order_seq.get(event_key, 0) + 1
            self.order_seq[event_key] = index
            order = Order(
                live_order_id(
                    str(self.params.get("strategy_id", "structural")),
                    moment.date(), event_key, index,
                ), intent.symbol, intent.side, intent.qty,
                OrderType.LIMIT, protected, moment,
            )
            self.broker.submit(order)
            if self.broker.remaining(order.order_id) > 0:
                self.order_times.append(datetime.now())
            sent = next(
                (item for item in self.broker.pending()
                 if item.order_id == order.order_id),
                order,
            )
            orders.append(sent)
        self.store.record_orders(
            event_key, orders,
            sent_ids=frozenset(order.order_id for order in self.broker.pending()),
        )
        return len(orders)

    def _record_results(self) -> int:
        statuses = self.broker.poll_statuses()
        received_fills = self.broker.poll_fills()
        fills = tuple(fill for fill in received_fills if not self.store.fill_recorded(fill))
        received_rejects = self.broker.poll_rejects()
        rejects = tuple(
            reject for reject in received_rejects
            if not self.store.reject_recorded(reject)
        )
        for fill in fills:
            self.portfolio.apply_fill(fill)
            self.turnover_today += fill.gross
            ctx, api = self._context()
            self.strategy.on_fill(ctx, fill)
            self._flush(api, f"fill:{fill.order_id}:{fill.filled_at.isoformat()}", fill.filled_at)
        remaining = {order.order_id: self.broker.remaining(order.order_id)
                     for order in self.broker.pending()}
        # 组合状态和成交幂等记录必须同一事务；成功后才 ACK Stream。
        self.store.commit_reports(
            self.portfolio, self.state, self.last_prices, fills, rejects, statuses,
            remaining, at=datetime.now(),
        )
        self.broker.commit_reports()
        self.journal.record_fills(datetime.now().date(), fills)
        self.journal.record_rejects(datetime.now().date(), rejects)
        if rejects:
            self.consecutive_rejects += len(rejects)
            self.alerts.warning(
                "实盘订单被拒", "\n".join(f"{r.order_id}: {r.reason}" for r in rejects)
            )
            if self.consecutive_rejects >= 3:
                reason = f"连续拒单达到熔断阈值: {self.consecutive_rejects}"
                self.kill_switch.engage(reason)
                self.broker.cancel_all()
                self.alerts.critical("实盘连续拒单熔断", reason)
        if fills:
            self.consecutive_rejects = 0
        return len(rejects)

    def _refresh_quotes(self, now: datetime) -> None:
        symbols = {str(symbol) for symbol in self.portfolio.positions}
        symbols.update(str(order.symbol) for order in self.broker.pending())
        if not symbols:
            # 空仓也是一个有效账户状态。持续记录现金净值，既让监控台能
            # 产生首个会话，也避免“没有持仓”被误显示成“核心未启动”。
            self.journal.record_equity(
                now.date(), cash=self.portfolio.cash, market_value=0.0
            )
            self.store.save(self.portfolio, self.state, self.last_prices, at=now)
            return
        quotes = self.market.get(sorted(symbols))
        self._latest_quotes.update(quotes)
        for raw, quote in quotes.items():
            if quote.price > 0:
                self.last_prices[Symbol.parse(raw)] = quote.price
        market_value = sum(
            position.total_qty * self.last_prices.get(symbol, position.avg_cost)
            for symbol, position in self.portfolio.positions.items()
        )
        self.journal.record_equity(
            now.date(), cash=self.portfolio.cash, market_value=market_value
        )
        self.store.save(self.portfolio, self.state, self.last_prices, at=now)
