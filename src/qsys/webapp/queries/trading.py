"""交易组只读查询（#662 计划 06c）。

直接读取领域存储，不依赖旧页面视图。没有事实就保留 ``None``，不从资产跳变推收益，
不从成交时刻反推券商受理时刻，也不把缺失对账补成一致。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from fastapi import HTTPException

from qsys.live.config import RiskConfig
from qsys.live.kill_review import KillReview
from qsys.live.messages import AccountSnapshot, Heartbeat
from qsys.ops.daily_report import build_daily_report
from qsys.webapp.contracts.shell import KillStatus
from qsys.webapp.contracts.trading import (
    EquityPoint, KillReviewInfo, KillReviewItemInfo, LiveTradingOverview, MoneyObservation,
    OrderStage, PaperAccountDetail, PaperAccountRow, PaperAccounts, PaperBacktestPoint, PaperEquityPoint,
    ReconciliationRecord, ReconCell, RiskLimit, StrategyRunInfo, TradingDayInfo,
    TradingDisabled, TradingFill, TradingHeartbeat, TradingOrder, TradingOrders,
    TradingPosition, TradingReconciliation, TradingReject, TradingRuns,
)
from qsys.webapp.queries.shell import build_status

log = structlog.get_logger(__name__)
REVIEW_MAX_AGE_SECONDS = 300.0
UNKNOWN_NO_RETRY = frozenset({"unknown", "dispatch:unknown"})


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _day(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _mask(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    tail = value[-6:] if len(value) >= 6 else value
    return "••••••" + tail


class TradingQuery:
    def __init__(self, *, journal=None, store=None, transport=None, heartbeat_prefix: str = "state:heartbeat",
                 account_key: str = "state:account", heartbeat_max_age: float = 90.0, kill_reader=None,
                 risk: RiskConfig | None = None, paper=None, review_path: Path | None = None,
                 live_mode: bool = True,
                 workbench=None, backtests=None,
                 clock: Callable[[], datetime] = datetime.now) -> None:
        self.journal, self.store, self.transport = journal, store, transport
        self.heartbeat_prefix, self.account_key = heartbeat_prefix, account_key
        self.heartbeat_max_age, self.kill_reader = heartbeat_max_age, kill_reader
        self.risk, self.paper = risk or RiskConfig(), paper
        self.live_mode = live_mode
        self.workbench, self.backtests = workbench, backtests
        self.review_path, self.clock = review_path, clock

    def _sessions(self) -> list[date]:
        try:
            return self.journal.sessions() if self.journal is not None else []
        except Exception as exc:  # noqa: BLE001
            log.info("webapp.trading.sessions_unavailable", error_type=type(exc).__name__)
            return []

    def _days(self, selected: date | None) -> TradingDayInfo:
        available = self._sessions()
        return TradingDayInfo(selected=selected or (available[-1] if available else self.clock().date()),
                              available=list(reversed(available[-60:])))

    def _kill(self) -> KillStatus:
        try:
            raw = self.kill_reader.read() if self.kill_reader is not None else None
        except Exception:  # noqa: BLE001
            raw = None
        return build_status(raw, self.clock()).kill

    def _account(self) -> AccountSnapshot | None:
        if self.transport is None:
            return None
        try:
            raw = self.transport.get_key(self.account_key)
            return AccountSnapshot.model_validate_json(raw) if raw else None
        except Exception:  # noqa: BLE001
            return None

    def _heartbeat(self, source: str) -> Heartbeat | None:
        if self.transport is None:
            return None
        try:
            raw = self.transport.get_key(f"{self.heartbeat_prefix}:{source}")
            beat = Heartbeat.model_validate_json(raw) if raw else None
            return beat if beat is not None and beat.source == source else None
        except Exception:  # noqa: BLE001
            return None

    def _heartbeats(self) -> list[TradingHeartbeat]:
        now = self.clock()
        rows: list[TradingHeartbeat] = []
        for source in ("core", "agent", "market"):
            beat = self._heartbeat(source)
            if beat is None:
                rows.append(TradingHeartbeat(source=source, at=None, age_seconds=None,
                                             threshold_seconds=None, fresh=None, detail="心跳不可读"))
                continue
            try:
                age = (now - beat.at).total_seconds()
            except TypeError:
                age = (now.replace(tzinfo=None) - beat.at.replace(tzinfo=None)).total_seconds()
            threshold = beat.expected_interval_s * 3 if beat.expected_interval_s else self.heartbeat_max_age
            rows.append(TradingHeartbeat(source=source, at=beat.at, age_seconds=round(age, 3),
                                         threshold_seconds=threshold, fresh=-1 <= age <= threshold,
                                         detail=beat.phase))
        return rows

    def _risk_rows(self, account: AccountSnapshot | None, positions: list[TradingPosition],
                   orders: list[dict[str, Any]]) -> list[RiskLimit]:
        total = account.total_asset if account else None
        gross = (account.market_value / total) if account and total else None
        weights = [p.market_value / total for p in positions
                   if p.market_value is not None and total]
        max_weight = max(weights) if weights else (0.0 if total and not positions else None)
        order_values = [o["qty"] * o["limit_price"] for o in orders if o.get("limit_price") is not None]
        maximum_order = max(order_values) if order_values else (0.0 if not orders else None)

        def row(id: str, label: str, value: float | None, limit: float, unit: str) -> RiskLimit:
            ratio = value / limit if value is not None and limit else None
            state = ("unknown" if value is None else "blocked" if value > limit else
                     "warning" if ratio is not None and ratio >= .7 else "ok")
            return RiskLimit(id=id, label=label, value=value, limit=limit, ratio=ratio,
                             unit=unit, state=state)  # type: ignore[arg-type]

        return [
            row("max_position_weight", "单票占比", max_weight, self.risk.max_position_weight, "ratio"),
            row("max_gross_exposure", "总敞口", gross, self.risk.max_gross_exposure, "ratio"),
            row("max_daily_loss", "单日亏损", None, self.risk.max_daily_loss, "ratio"),
            row("max_order_value", "单笔委托", maximum_order, self.risk.max_order_value, "yuan"),
        ]

    def _live_rows(self, table: str) -> list[dict[str, Any]]:
        if self.store is None:
            return []
        try:
            return self.store.read_rows(table)
        except Exception as exc:  # noqa: BLE001
            log.info("webapp.trading.live_store_unavailable", table=table, error_type=type(exc).__name__)
            return []

    def live(self, selected: date | None) -> LiveTradingOverview | TradingDisabled:
        if not self.live_mode or (self.journal is None and self.transport is None and self.store is None):
            return TradingDisabled()
        days = self._days(selected)
        account = self._account()
        state = self._live_rows("state")
        positions_raw = self._live_rows("positions")
        orders = self._live_rows("orders")
        pending_sell: dict[str, int] = defaultdict(int)
        for o in orders:
            if o.get("side") == "sell" and int(o.get("remaining_qty") or 0) > 0:
                pending_sell[str(o.get("symbol"))] += int(o["remaining_qty"])
        positions = []
        if account is not None:
            positions = [TradingPosition(
                symbol=symbol, total_qty=p.total_qty, available_qty=p.available_qty,
                avg_cost=p.avg_cost, last_price=None, market_value=p.market_value,
                reserved_qty=pending_sell.get(symbol, 0)) for symbol, p in sorted(account.positions.items())
                if p.total_qty != 0]
        elif positions_raw:
            positions = [TradingPosition(
                symbol=str(p["symbol"]), total_qty=int(p["total_qty"]),
                available_qty=int(p["available_qty"]),
                avg_cost=(float(p["cost_basis"]) / int(p["total_qty"]) if int(p["total_qty"]) else None),
                last_price=float(p["last_price"]) if p.get("last_price") is not None else None,
                market_value=None, reserved_qty=pending_sell.get(str(p["symbol"]), 0))
                for p in positions_raw]
        observation = MoneyObservation(
            at=account.at if account else _parse_time(state[0].get("updated_at")) if state else None,
            cash=account.cash if account else float(state[0]["cash"]) if state else None,
            market_value=account.market_value if account else None,
            total_value=account.total_asset if account else None,
        )
        kill = self._kill()
        posture = "reduce_only" if kill.state == "stopped" else "trading" if kill.state == "clear" else "unknown"
        equity = []
        missing: dict[str, str] = {"return_ratio": "缺少完整资金变动流水，不从总资产跳变计算收益"}
        try:
            equity = [EquityPoint(day=r["dt"], total_value=r["total_value"])
                      for r in self.journal.equity().iter_rows(named=True)] if self.journal else []
        except Exception:  # noqa: BLE001
            missing["equity"] = "实盘总资产历史不可读"
        if account is None:
            missing["account"] = "券商账户快照不可读"
        return LiveTradingOverview(
            day=days, posture=posture, posture_label={"trading": "可下单", "reduce_only": "仅允许减仓",
                                                       "unknown": "交易状态未知"}[posture],
            observation=observation, positions=positions, heartbeats=self._heartbeats(), kill=kill,
            risks=self._risk_rows(account, positions, orders), equity=equity,
            order_count=account.active_order_count if account else None, missing=missing)

    def orders(self, selected: date | None) -> TradingOrders | TradingDisabled:
        if not self.live_mode or (self.journal is None and self.store is None):
            return TradingDisabled()
        days = self._days(selected)
        target = days.selected
        raw_orders = [o for o in self._live_rows("orders") if _day(o.get("created_at")) == target]
        raw_stages = sorted(self._live_rows("transitions"), key=lambda r: int(r.get("id") or 0))
        raw_fills = [f for f in self._live_rows("fills") if _day(f.get("filled_at")) == target]
        raw_rejects = [r for r in self._live_rows("rejects") if _day(r.get("at")) == target]
        by_order: dict[str, list[OrderStage]] = defaultdict(list)
        for s in raw_stages:
            if any(o["order_id"] == s.get("order_id") for o in raw_orders):
                by_order[str(s["order_id"])].append(OrderStage(
                    state=str(s["state"]), at=_parse_time(s.get("at")), detail=str(s.get("detail") or "") or None))
        fills_by_order: dict[str, list[dict]] = defaultdict(list)
        for f in raw_fills:
            fills_by_order[str(f["order_id"])].append(f)
        orders = []
        for o in raw_orders:
            stages = by_order[str(o["order_id"])]
            accepted = next((s for s in stages if s.state == "accepted"), None)
            broker_ids = [str(f["broker_order_id"]) for f in fills_by_order[str(o["order_id"])]
                          if f.get("broker_order_id")]
            orders.append(TradingOrder(
                id=str(o["order_id"]), event_key=str(o["event_key"]), symbol=str(o["symbol"]),
                side=str(o["side"]), qty=int(o["qty"]), remaining_qty=int(o["remaining_qty"]),
                order_type=str(o["order_type"]), limit_price=o.get("limit_price"), status=str(o["status"]),
                created_at=_parse_time(o["created_at"]), updated_at=_parse_time(o.get("updated_at")),
                broker_order_id=broker_ids[0] if broker_ids else None,
                broker_accepted_at=accepted.at if accepted else None,
                unknown_no_retry=any(s.state in UNKNOWN_NO_RETRY for s in stages) or o.get("status") == "unknown",
                stages=stages))
        fills = [TradingFill(order_id=str(f["order_id"]), symbol=str(f["symbol"]), side=str(f["side"]),
                             qty=int(f["qty"]), price=float(f["price"]), fee=float(f["fee"]),
                             filled_at=_parse_time(f["filled_at"]), broker_order_id=f.get("broker_order_id"),
                             broker_trade_id=f.get("broker_trade_id")) for f in raw_fills]
        rejects = [TradingReject(order_id=str(r["order_id"]), symbol=str(r["symbol"]), side=str(r["side"]),
                                 qty=int(r["qty"]), reason=str(r["reason"]), at=_parse_time(r["at"]))
                   for r in raw_rejects]
        if self.store is None and self.journal is not None and target is not None:
            try:
                fills = [TradingFill(
                    order_id=str(f["client_order_id"]), symbol=str(f["symbol"]), side=str(f["side"]),
                    qty=int(f["qty"]), price=float(f["price"]), fee=float(f["fee"]),
                    filled_at=f["filled_at"], broker_order_id=f.get("broker_order_id"),
                    broker_trade_id=f.get("broker_trade_id"))
                    for f in self.journal.fills(target).iter_rows(named=True)]
                rejects = [TradingReject(
                    order_id=str(r["client_order_id"]), symbol=str(r["symbol"]), side="unknown",
                    qty=int(r["qty"]), reason=str(r["reason"]), at=r["at"])
                    for r in self.journal.rejects(target).iter_rows(named=True)]
            except Exception:  # noqa: BLE001
                pass
        missing = {"broker_accepted_at": "券商受理时刻未记录时保持缺失，不用成交时刻反推"}
        if self.store is None:
            missing["live_store"] = "实盘状态库未配置"
        return TradingOrders(day=days, orders=orders, fills=fills, rejects=rejects, missing=missing)

    @staticmethod
    def _recon_state(rows: list[dict[str, Any]], layer: str) -> tuple[str, datetime | None, str | None]:
        # 当前账本的每次对账同时覆盖资金与持仓；委托/成交没有独立层级记录，保持未知。
        if layer == "orders" or not rows:
            return "unknown", None, None
        latest = rows[-1]
        return ("consistent" if latest["is_consistent"] else "inconsistent",
                _parse_time(latest["at"]), str(latest.get("detail") or "") or None)

    def _review(self) -> tuple[KillReviewInfo | None, str | None]:
        if self.review_path is None:
            return None, "停机闸核对记录未配置"
        try:
            raw = KillReview.model_validate_json(self.review_path.read_text(encoding="utf-8"))
            now, at = self.clock(), raw.reviewed_at
            try:
                age = (now - at).total_seconds()
            except TypeError:
                age = (now.replace(tzinfo=None) - at.replace(tzinfo=None)).total_seconds()
            return KillReviewInfo(reviewed_at=at, overall=raw.overall, engaged=raw.engaged,
                                  stale=age > REVIEW_MAX_AGE_SECONDS,
                                  items=[KillReviewItemInfo(**i.model_dump()) for i in raw.items]), None
        except Exception as exc:  # noqa: BLE001
            return None, f"停机闸核对记录不可读（{type(exc).__name__}）"

    def reconciliation(self, selected: date | None) -> TradingReconciliation | TradingDisabled:
        if not self.live_mode or self.journal is None:
            return TradingDisabled()
        days = self._days(selected)
        sessions = list(reversed(list(reversed(days.available))[-30:]))
        heatmap: list[ReconCell] = []
        selected_rows: list[dict[str, Any]] = []
        for d in sessions:
            try:
                rows = list(self.journal.reconciliations(d).iter_rows(named=True))
            except Exception:  # noqa: BLE001
                rows = []
            if d == days.selected:
                selected_rows = rows
            for layer in ("cash", "positions", "orders"):
                state, at, detail = self._recon_state(rows, layer)
                heatmap.append(ReconCell(day=d, layer=layer, state=state, at=at, detail=detail))
        records = [ReconciliationRecord(at=_parse_time(r["at"]), layer=str(r["layer"]),
                                        mode=str(r.get("mode") or ""), consistent=bool(r["is_consistent"]),
                                        cash_diff=r.get("cash_diff"), mismatches=int(r["n_mismatches"]),
                                        detail=str(r.get("detail") or "")) for r in selected_rows]
        review, review_error = self._review()
        missing = {"orders_reconciliation": "现有对账记录未拆出独立委托/成交层"}
        if review_error:
            missing["kill_review"] = review_error
        live = self.live(days.selected)
        risks = live.risks if isinstance(live, LiveTradingOverview) else []
        return TradingReconciliation(day=days, heatmap=heatmap, records=records, risks=risks,
                                     kill=self._kill(), review=review, missing=missing)

    @staticmethod
    def _run(row: dict[str, Any], event: dict[str, Any] | None = None) -> StrategyRunInfo:
        return StrategyRunInfo(
            event_key=str(row["event_key"]), phase=str(row["phase"]),
            scheduled_at=_parse_time(event.get("scheduled_at")) if event else None,
            started_at=_parse_time(row.get("started_at")), finished_at=_parse_time(row.get("finished_at")),
            duration_ms=int(row.get("duration_ms") or 0),
            status="failed" if row.get("error") else "completed",
            candidates=row.get("candidates"), routed=row.get("routed"), passed=row.get("passed"),
            orders=int(row.get("orders") or 0), rejects=int(row.get("rejects") or 0),
            summary=str(row.get("summary") or ""), reasons=dict(row.get("reasons") or {}),
            note=str(row.get("note") or ""), error=str(row.get("error") or ""),
            event_status=str(event.get("status")) if event else None,
            event_detail=str(event.get("detail") or "") or None if event else None)

    def runs(self, selected: date | None) -> TradingRuns | TradingDisabled:
        if not self.live_mode or self.journal is None:
            return TradingDisabled()
        days = self._days(selected)
        target = days.selected
        rows, missing = [], {}
        if self.store is not None and target is not None:
            try:
                rows = self.store.strategy_runs(target)
            except Exception:  # noqa: BLE001
                missing["runs"] = "策略运行记录不可读"
        else:
            missing["runs"] = "实盘状态库未配置"
        report = None
        if target in self._sessions():
            try:
                report = build_daily_report(self.journal, target).as_text()
            except Exception:  # noqa: BLE001
                missing["report"] = "日报不可读"
        else:
            missing["report"] = "该日没有实盘会话"
        events = {str(e["event_key"]): e for e in self._live_rows("events")
                  if _day(e.get("scheduled_at")) == target}
        return TradingRuns(day=days, runs=[self._run(r, events.get(str(r["event_key"]))) for r in rows],
                           report_text=report, missing=missing)

    def paper_accounts(self) -> PaperAccounts | TradingDisabled:
        if self.paper is None:
            return TradingDisabled()
        return PaperAccounts(accounts=[self._paper_row(a) for a in self.paper.accounts()])

    def _paper_row(self, account) -> PaperAccountRow:
        try:
            summary = self.paper.summary(account.id)
        except Exception:  # noqa: BLE001
            summary = {}
        return PaperAccountRow(
            id=account.id, name=account.name, strategy_name=account.strategy_name,
            version_id=account.version_id, status=account.status,
            activation_date=_day(account.activation_date), error=account.error or None,
            total_value=summary.get("total_value"),
            return_ratio=(summary["return_pct"] / 100 if summary.get("return_pct") is not None else None),
            max_drawdown_ratio=(summary["max_drawdown_pct"] / 100
                                if summary.get("max_drawdown_pct") is not None else None),
            position_count=summary.get("position_count"))

    def paper_detail(self, ident: str, selected: date | None) -> PaperAccountDetail | TradingDisabled:
        if self.paper is None:
            return TradingDisabled()
        account = self.paper.account(ident)
        if account is None:
            raise HTTPException(status_code=404, detail="模拟账户不存在")
        base = self._paper_row(account).model_dump()
        daily = self.paper.equity_daily(ident)
        points = [PaperEquityPoint(
            at=_parse_time(r.get("at")), day=_day(r.get("session")), total_value=float(r["total_value"]),
            strategy_ratio=(float(r["total_value"]) / account.initial_cash - 1 if account.initial_cash else None),
            benchmark_ratio=(float(r["benchmark_close"]) / account.initial_cash - 1
                             if r.get("benchmark_close") is not None and account.initial_cash else None))
            for r in daily]
        run_days = self.paper.run_sessions(ident)
        run_day = selected or (run_days[0] if run_days else None)
        source_backtest: list[PaperBacktestPoint] = []
        if self.backtests is not None:
            job = self.workbench.job(account.backtest_id) if self.workbench is not None else None
            run_name = (job.run_name or job.id) if job is not None else account.backtest_id
            run = self.backtests.run(run_name)
            initial = run.initial_value if run is not None else None
            source_backtest = [PaperBacktestPoint(
                day=date.fromisoformat(d), total_value=v,
                return_ratio=(v / initial - 1 if v is not None and initial else None))
                for d, v in self.backtests.nav(run_name)]
        paper_events = self.paper.rows(ident, "events", limit=200)
        events_by_key = {str(e.get("event_key")): e for e in paper_events}
        return PaperAccountDetail(
            **base, backtest_id=account.backtest_id, strategy_id=account.strategy_id,
            initial_cash=account.initial_cash, cash=account.cash,
            notify_mask=_mask(str(account.config.get("notify_webhook", ""))),
            positions=self.paper.rows(ident, "positions"), orders=self.paper.rows(ident, "orders"),
            fills=self.paper.rows(ident, "fills"), rejects=self.paper.rows(ident, "rejects"),
            events=paper_events, logs=self.paper.rows(ident, "logs", limit=200),
            runs=[self._run(r, events_by_key.get(str(r["event_key"])))
                  for r in self.paper.strategy_runs(ident, run_day)] if run_day else [],
            equity=points, source_backtest=source_backtest,
            day=TradingDayInfo(selected=run_day, available=run_days),
            missing=({"recovery": "出错账户恢复须走备份回读与精确状态转换，页面不直接改库"}
                     if account.status == "error" else {}) |
                    ({"source_backtest": "来源回测曲线不可读"} if not source_backtest else {}) |
                    {"fill_reference": "模拟成交尚未记录独立参考价与成交量占比，保持缺失"})


class TradingWrites:
    def __init__(self, query: TradingQuery, *, activation: Callable[[], date] | None = None) -> None:
        self.q, self.activation = query, activation

    def action(self, ident: str, action: str):
        when = self.activation() if action == "resume" and self.activation else None
        try:
            return self.q.paper.transition(ident, action, activation=when)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    def notify(self, ident: str, webhook: str):
        url = webhook.strip()
        if url and not url.startswith("https://"):
            raise HTTPException(status_code=422, detail="通知链接必须是 https")
        try:
            return self.q.paper.set_notify_webhook(ident, url)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
