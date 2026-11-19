"""今日运行组合查询；各来源独立失败，缺失不推算。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from qsys.ops.health import SHANGHAI
from qsys.webapp.contracts.today import (
    TodayConcern, TodayDisabled, TodayEvent, TodayKpis, TodayLink, TodayLinkPoint,
    TodayPhase, TodaySentimentPoint, TodayView,
)
from qsys.webapp.contracts.trading import LiveTradingOverview, TradingOrders, TradingRuns


class TodayQuery:
    def __init__(self, *, trading=None, market=None, alerts=None, health=None,
                 clock=lambda: datetime.now(SHANGHAI)) -> None:
        self.trading, self.market, self.alerts, self.health = trading, market, alerts, health
        self.clock = clock

    def overview(self, selected: date | None = None) -> TodayView | TodayDisabled:
        if not any((self.trading, self.market, self.alerts, self.health)):
            return TodayDisabled()
        now = self.clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=SHANGHAI)
        day = selected or now.astimezone(SHANGHAI).date()
        live = self._call(self.trading, "live", day)
        orders = self._call(self.trading, "orders", day)
        runs = self._call(self.trading, "runs", day)
        market = self._call(self.market, "overview")
        alerts = self._call(self.alerts, "center", day)
        links = self._links(day)
        missing: dict[str, str] = {"index_intraday": "上证 1 分钟分时尚未采集，由 #675 承接；不从当前价生成曲线"}
        if not isinstance(live, LiveTradingOverview): missing["live"] = "实盘状态不可读或未启用"
        if market is None: missing["market"] = "市场状态不可读或未配置"
        if alerts is None: missing["alerts"] = "告警汇总不可读或未配置"
        if not links: missing["links"] = "所选日期没有链路采样"
        events = self._events(runs)
        kill = live.kill if isinstance(live, LiveTradingOverview) else None
        state, title = self._state(day, now, kill, market)
        concerns = self._concerns(alerts, orders, events, market, links)
        available = runs.day.available if isinstance(runs, TradingRuns) else [day]
        if day not in available: available = [day, *available]
        return TodayView(day=day, days=available, state=state, title=title, kill=kill, concerns=concerns,
                         kpis=self._kpis(live, market, links), phases=self._phases(day, now, events),
                         events=events, links=links, sentiment=self._sentiment(market), missing=missing)

    @staticmethod
    def _call(obj, name: str, *args):
        if obj is None: return None
        try: return getattr(obj, name)(*args)
        except Exception: return None

    @staticmethod
    def _events(runs) -> list[TodayEvent]:
        if not isinstance(runs, TradingRuns): return []
        return [TodayEvent(event_key=r.event_key, phase=r.phase, scheduled_at=r.scheduled_at,
                           started_at=r.started_at, finished_at=r.finished_at, state=r.status,
                           summary=r.summary, detail=r.error or r.event_detail or r.note or None)
                for r in runs.runs]

    def _links(self, day: date) -> list[TodayLink]:
        if self.health is None: return []
        try:
            rows = self.health.link_samples(datetime.combine(day, time.min, SHANGHAI))
        except Exception:
            return []
        grouped: dict[str, list[TodayLinkPoint]] = {}
        for r in rows:
            if r["sampled_at_utc"].astimezone(SHANGHAI).date() != day: continue
            grouped.setdefault(str(r["source"]), []).append(TodayLinkPoint(
                sampled_at=r["sampled_at_utc"], age_seconds=r.get("age_s"),
                threshold_seconds=r.get("threshold_s"), fresh=r.get("fresh")))
        return [TodayLink(source=k, points=v) for k, v in sorted(grouped.items())]

    @staticmethod
    def _state(day, now, kill, market):
        if kill is not None and kill.state == "stopped": return "reduce_only", "停机闸已触发 · 仅允许减仓"
        if day != now.date(): return "closed", f"{day} 历史运行"
        schedule = getattr(market, "schedule", None)
        if schedule is not None and schedule.is_open: return "running", "运行中"
        if schedule is not None and schedule.is_trading_day and now.time() >= time(15, 30): return "closed", "已收盘"
        if schedule is not None: return "waiting", "等待交易时段"
        return "unknown", "运行状态未知"

    @staticmethod
    def _kpis(live, market, links):
        snap = market.live.snapshot if market and market.live and market.live.snapshot else None
        index = next((i for i in (snap.indices if snap else []) if i.symbol == "000001.SH" or "上证" in i.name), None)
        latest = [l.points[-1] for l in links if l.points]
        known = [p.age_seconds for p in latest if p.age_seconds is not None]
        return TodayKpis(
            account_total=live.observation.total_value if isinstance(live, LiveTradingOverview) else None,
            account_at=live.observation.at if isinstance(live, LiveTradingOverview) else None,
            index_name=index.name if index else None, index_price=index.price if index else None,
            index_change_ratio=(index.change_pct / 100 if index else None),
            limit_up=snap.limit_up_count if snap else None, limit_down=snap.limit_down_count if snap else None,
            seal_ratio=(1 - snap.broken_board_rate if snap else None),
            link_max_age_seconds=max(known) if known else None,
            link_fresh=sum(p.fresh is True for p in latest) if latest else None,
            link_total=len(latest) if latest else None)

    @staticmethod
    def _concerns(alerts, orders, events, market, links):
        out = []
        if alerts is not None:
            for i in alerts.items:
                if i.status == "unhandled": out.append(TodayConcern(id=f"alert:{i.key}:{i.subject}", severity=i.severity.lower() if i.severity.lower() in ("critical", "warning", "info") else "unknown", label=i.title, detail=i.detail, page="alerts"))
        if isinstance(orders, TradingOrders):
            unknown = sum(o.unknown_no_retry for o in orders.orders)
            if unknown: out.append(TodayConcern(id="orders:unknown", severity="warning", label=f"{unknown} 笔委托状态未知", detail="按交易契约不自动重发", page="orders"))
        failed = sum(e.state == "failed" for e in events)
        if failed: out.append(TodayConcern(id="runs:failed", severity="critical", label=f"{failed} 个策略事件失败", detail=None, page="runs"))
        if market and market.live and market.live.stale: out.append(TodayConcern(id="market:stale", severity="warning", label="市场快照已过期", detail=None, page="market"))
        latest = [l.points[-1] for l in links if l.points]
        bad = sum(p.fresh is False for p in latest)
        if bad: out.append(TodayConcern(id="links:stale", severity="critical", label=f"{bad} 个链路采样过期", detail=None, page="nodes"))
        return out

    @staticmethod
    def _phases(day, now, events):
        defs = [("pre", "盘前", time(0), time(9, 25)), ("auction", "竞价", time(9, 25), time(9, 30)),
                ("intraday", "盘中", time(9, 30), time(15)), ("close", "收盘", time(15), time(15, 40)),
                ("evening", "晚间任务", time(15, 40), time(23, 59, 59))]
        out = []
        for ident, label, start, end in defs:
            mine = [e for e in events if e.scheduled_at and start <= e.scheduled_at.astimezone(SHANGHAI).time() < end]
            completed = sum(e.state == "completed" for e in mine)
            if any(e.state == "failed" for e in mine): state = "failed"
            elif mine and completed == len(mine): state = "ok"
            elif day < now.date() or (day == now.date() and now.time() >= end): state = "unknown"
            elif day == now.date() and start <= now.time() < end: state = "running"
            else: state = "not_started"
            out.append(TodayPhase(id=ident, label=label, time_label=f"{start:%H:%M}–{end:%H:%M}", state=state, completed=completed, recorded=len(mine)))
        return out

    @staticmethod
    def _sentiment(market):
        regime = market.regime if market and market.regime else []
        return [TodaySentimentPoint(day=r.dt, limit_up=r.limit_up_count, limit_down=r.limit_down_count,
                                    broken_board_ratio=r.broken_board_rate,
                                    prior_limitup_return_ratio=r.prior_limitup_nextday_return_mean)
                for r in regime[-20:]]
