"""视图模型。

纯数据结构，不含任何 HTML —— 页面逻辑因此可以脱离 Web 框架单独测试。
Web 层不持有业务逻辑，也不写任何数据。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

import structlog

from qsys.live.heartbeat import HeartbeatMonitor
from qsys.live.messages import AccountSnapshot
from qsys.live.store import LiveStore
from qsys.live.transport import Transport
from qsys.ops.daily_report import DailyReport, EquityLine, build_daily_report
from qsys.ops.journal import SessionJournal
from qsys.web.config import WebConfig

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class OverviewView:
    session: date
    equity_curve: list[float]
    equity_line: EquityLine
    positions: tuple[dict, ...]
    cash: float
    heartbeats: tuple[dict, ...]
    is_healthy: bool


@dataclass(frozen=True, slots=True)
class TradesView:
    session: date
    fills: tuple[dict, ...]
    rejects: tuple[dict, ...]


@dataclass(frozen=True, slots=True)
class HealthView:
    session: date
    reconciliations: tuple[dict, ...]
    quality_summary: str
    dead_sources: tuple[str, ...]


class MonitorService:
    def __init__(
        self,
        journal: SessionJournal,
        *,
        config: WebConfig,
        transport: Transport | None = None,
        quality_summary_provider: Callable[[], str] | None = None,
        store: LiveStore | None = None,
    ) -> None:
        self._journal = journal
        self._cfg = config
        self._transport = transport
        self._quality = quality_summary_provider
        self._store = store

    def latest_session(self) -> date | None:
        sessions = self._journal.sessions()
        return sessions[-1] if sessions else None

    def equity_by_session(self) -> list[dict]:
        """影子/实盘核心每日总资产（收盘写入 live_equity）。"""
        return self._journal.equity().to_dicts()

    def has_session(self, session: date) -> bool:
        return session in self._journal.sessions()

    def _account(self) -> AccountSnapshot | None:
        if self._transport is None:
            return None
        raw = self._transport.get_key(self._cfg.account_key)
        if raw is None:
            return None
        try:
            return AccountSnapshot.model_validate_json(raw)
        except Exception as exc:
            log.error("views.bad_account_snapshot", error=str(exc))
            return None

    def _heartbeats(self, now: datetime | None) -> tuple[dict, ...]:
        if self._transport is None:
            return ()
        monitor = HeartbeatMonitor(self._transport, prefix=self._cfg.heartbeat_prefix)
        return tuple(
            {
                "source": source,
                "last_beat": monitor.last_beat(source),
                "alive": monitor.is_alive(
                    source, max_age_seconds=self._cfg.heartbeat_max_age, now=now
                ),
            }
            for source in self._cfg.watch_sources
        )

    def heartbeat_status(self, source: str, *, now: datetime | None = None) -> dict:
        """Health for optional workers without adding them to global live health."""
        if self._transport is None:
            return {"source": source, "last_beat": None, "alive": False,
                    "expected_interval_s": None, "phase": None}
        monitor = HeartbeatMonitor(self._transport, prefix=self._cfg.heartbeat_prefix)
        heartbeat = monitor.read(source)
        last = heartbeat.at if heartbeat else None
        return {
            "source": source,
            "last_beat": last,
            "alive": False if last is None else (
                ((now or datetime.now()) - last).total_seconds() <= self._cfg.heartbeat_max_age
            ),
            "expected_interval_s": heartbeat.expected_interval_s if heartbeat else None,
            "phase": heartbeat.phase if heartbeat else None,
        }

    def _quality_summary(self) -> str:
        if self._quality is None:
            return ""
        try:
            return self._quality()
        except Exception as exc:  # 质量报告取不到不能让整个页面 500
            log.error("views.quality_failed", error=str(exc))
            return f"数据质量报告不可用: {exc}"

    def _dead_sources(self, now: datetime | None) -> tuple[str, ...]:
        return tuple(h["source"] for h in self._heartbeats(now) if not h["alive"])

    def overview(self, session: date, *, now: datetime | None = None) -> OverviewView:
        curve_df = self._journal.equity(end=session).tail(self._cfg.equity_days)
        report = build_daily_report(self._journal, session)

        snapshot = self._account()
        positions: tuple[dict, ...] = ()
        cash = report.equity.cash
        if snapshot is not None:
            cash = snapshot.cash
            positions = tuple(
                {
                    "symbol": symbol,
                    "total_qty": p.total_qty,
                    "available_qty": p.available_qty,
                    "avg_cost": p.avg_cost,
                }
                for symbol, p in sorted(snapshot.positions.items())
                if p.total_qty != 0
            )

        heartbeats = self._heartbeats(now)
        healthy = report.is_healthy and all(h["alive"] for h in heartbeats)

        return OverviewView(
            session=session, equity_curve=curve_df["total_value"].to_list(),
            equity_line=report.equity, positions=positions, cash=cash,
            heartbeats=heartbeats, is_healthy=healthy,
        )

    def trades(self, session: date) -> TradesView:
        return TradesView(
            session=session,
            fills=tuple(self._journal.fills(session).iter_rows(named=True)),
            rejects=tuple(self._journal.rejects(session).iter_rows(named=True)),
        )

    def health(self, session: date, *, now: datetime | None = None) -> HealthView:
        return HealthView(
            session=session,
            reconciliations=tuple(
                self._journal.reconciliations(session).iter_rows(named=True)
            ),
            quality_summary=self._quality_summary(),
            dead_sources=self._dead_sources(now),
        )

    def report(self, session: date, *, now: datetime | None = None) -> DailyReport:
        return build_daily_report(
            self._journal, session,
            quality_summary=self._quality_summary(),
            dead_sources=self._dead_sources(now),
        )

    def strategy_runs(self, session: date) -> list[dict]:
        return self._store.strategy_runs(session) if self._store else []

    def run_sessions(self) -> list[date]:
        return self._store.run_sessions() if self._store else []
