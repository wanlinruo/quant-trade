"""运维只读查询：ServiceRun/observer 本地库与受管 JSON 快照。"""

from __future__ import annotations

import calendar as _calendar
import json
import threading
from collections import OrderedDict
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from qsys.ops.health import SHANGHAI
from qsys.ops.services import SERVICES, effective_status, load_runs, run_session
from qsys.webapp.cache import SingleFlightCache
from qsys.webapp.contracts.operations import (
    NodesView, OperationsDisabled, OpsState, ReleasesView,
    ServiceCell, ServiceDurationPoint, ServiceRow, ServiceRunItem, ServicesView, ServiceStep,
)

SERVICES_TTL_SECONDS = 15.0
SERVICES_CACHE_KEYS = 8


def _aware(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    return moment.replace(tzinfo=SHANGHAI) if moment.tzinfo is None else moment.astimezone(SHANGHAI)


def _run_state(raw: str) -> OpsState:
    return {
        "succeeded": "ok", "warning": "warning", "failed": "failed",
        "interrupted": "failed", "running": "running", "repaired": "fixed",
        "skipped": "paused",
    }.get(raw, "unknown")


TradingDays = dict[date, bool]


def _last_closed_day(open_days: TradingDays, year: int, month: int) -> date | None:
    """当月最后一个休市日；从月末往回找，遇到日历缺行就返回 None，不按星期推算。"""
    day = date(year, month, _calendar.monthrange(year, month)[1])
    while day.month == month:
        is_open = open_days.get(day)
        if is_open is None:
            return None
        if not is_open:
            return day
        day -= timedelta(days=1)
    return None


class OperationsQuery:
    def __init__(self, runs_root: Path, health_store=None, snapshot_root: Path | None = None,
                 *, now=lambda: datetime.now(SHANGHAI),
                 trading_days: Callable[[date, date], TradingDays] | None = None) -> None:
        self.runs_root = Path(runs_root)
        self.health_store = health_store
        # 只读 warehouse 规范交易日历（直取 Tushare trade_cal）；请求路径不调用外部接口。
        self.trading_days = trading_days
        self.snapshot_root = Path(snapshot_root) if snapshot_root is not None else None
        self.now = now
        self._services_caches: OrderedDict[date, SingleFlightCache[ServicesView]] = OrderedDict()
        self._services_caches_lock = threading.Lock()

    def services(self, selected: date | None = None) -> ServicesView:
        now = self._now()
        selected = selected or now.date()
        with self._services_caches_lock:
            cache = self._services_caches.get(selected)
            if cache is None:
                cache = SingleFlightCache(
                    lambda: self._build_services(selected), SERVICES_TTL_SECONDS
                )
                self._services_caches[selected] = cache
                while len(self._services_caches) > SERVICES_CACHE_KEYS:
                    self._services_caches.popitem(last=False)
            else:
                self._services_caches.move_to_end(selected)
        try:
            return cache.get(stale_on_error=self._stale_services)
        except Exception as exc:
            # There is no previous successful value on the first failed read.
            # Preserve the page contract with an explicit missing/error marker;
            # later refresh failures are handled by _stale_services above.
            return self._build_services(selected, tolerate_health_error=True, health_error=exc)

    def _now(self) -> datetime:
        now = self.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=SHANGHAI)
        return now

    @staticmethod
    def _stale_services(value: ServicesView, exc: Exception) -> ServicesView:
        missing = dict(value.missing)
        missing["cache_stale"] = "服务看板刷新失败，当前为上一份成功结果"
        missing["cache_error"] = type(exc).__name__
        return value.model_copy(update={"missing": missing})

    def _build_services(
        self, selected: date, *, tolerate_health_error: bool = False,
        health_error: Exception | None = None,
    ) -> ServicesView:
        now = self._now()
        start = selected - timedelta(days=29)
        days = [start + timedelta(days=i) for i in range(30)]
        runs = load_runs(self.runs_root, start, selected)
        by_service: dict[str, list[dict]] = {}
        for run in runs:
            by_service.setdefault(str(run.get("service", "")), []).append(run)
        if tolerate_health_error and health_error is not None:
            health = []
        else:
            try:
                health = self._daily_health(start, selected, now)
            except Exception as exc:
                if not tolerate_health_error:
                    raise
                health = []
                health_error = exc
        versions = self._service_versions()
        open_days: TradingDays = {}
        calendar_error: Exception | None = None
        if self.trading_days is not None:
            # 读到所选月月末，monthly_closed 才能确定当月最后一个休市日。
            first = start.replace(day=1)
            last = selected.replace(day=_calendar.monthrange(selected.year, selected.month)[1])
            try:
                open_days = dict(self.trading_days(first, last))
            except Exception as exc:
                calendar_error = exc
        rows = [self._service_row(spec, days, by_service.get(spec.id, []), health, now,
                                  versions.get(spec.id), open_days)
                for spec in SERVICES]
        run_items = [self._run_item(run, now) for run in runs]
        missing = {}
        if self.trading_days is None:
            missing["calendar"] = "交易日历未配置，休市日保持未知"
        elif calendar_error is not None:
            missing["calendar"] = "交易日历读取失败，休市日保持未知"
        elif any(day not in open_days for day in days):
            missing["calendar"] = "交易日历缺少部分日期，缺行日期保持未知"
        if self.health_store is None:
            missing["health"] = "health-observer 本地只读库未配置"
        elif health_error is not None:
            missing["health"] = "health-observer 本地只读库读取失败"
            missing["cache_error"] = type(health_error).__name__
        elif not health:
            missing["health"] = "所选窗口没有健康观察记录"
        if not versions:
            missing["versions"] = "受管发布快照缺失或已过期"
        return ServicesView(selected_day=selected, days=days, services=rows,
                            runs=run_items, missing=missing,
                            trading_days={day: open_days[day] for day in days if day in open_days})

    @staticmethod
    def _health_state(state: object) -> OpsState:
        return {"healthy": "ok", "degraded": "warning", "down": "failed",
                "planned": "not_due"}.get(str(state), "unknown")

    def _daily_health(self, start: date, end: date, now: datetime) -> list[dict]:
        if self.health_store is None:
            return []
        begin = datetime.combine(start, time.min, tzinfo=SHANGHAI)
        finish = min(now, datetime.combine(end + timedelta(days=1), time.min, tzinfo=SHANGHAI))
        return self.health_store.daily_latest(begin, finish)

    def _service_row(self, spec, days: list[date], runs: list[dict], health: list[dict],
                     now: datetime, version: str | None,
                     open_days: TradingDays | None = None) -> ServiceRow:
        open_days = open_days or {}
        cells = []
        successful = observed = 0
        for day in days:
            mine = [r for r in runs if run_session(r) == day.isoformat()]
            raw_states = [effective_status(r, now=now.replace(tzinfo=None)) for r in mine]
            h = [r for r in health if r.get("service_id") == spec.id
                 and r["observed_at_utc"].astimezone(SHANGHAI).date() == day]
            state: OpsState = "unknown"
            detail = None
            run_id = None
            if raw_states:
                meaningful = [(run, raw) for run, raw in zip(mine, raw_states, strict=True)
                              if raw != "skipped"]
                if not meaningful:
                    state = "not_due"
                    chosen = mine[-1]
                else:
                    observed += 1
                    chosen, raw = meaningful[-1]
                    state = _run_state(raw)
                    if state == "ok" and any(_run_state(s) == "failed"
                                               for _, s in meaningful[:-1]):
                        state = "fixed"
                    if state in ("ok", "warning", "fixed"):
                        successful += 1
                run_id = str(chosen.get("run_id"))
                detail = chosen.get("note") or chosen.get("error") or None
            elif h and not (spec.kind == "timer"
                            and self._health_state(h[-1].get("state")) == "ok"):
                # 健康观测的失败/警告是真实证据；timer 的“就绪”观测只说明调度器在线，
                # 不是运行事实，不能画成正常或计入成功率（#700）。
                observed += 1
                state = self._health_state(h[-1].get("state"))
                if state == "not_due":
                    observed -= 1
                if state == "ok":
                    successful += 1
                detail = str(h[-1].get("reason_code", "")) or None
            elif spec.cadence == "monthly" and day.day != spec.run_day:
                state = "not_due"
            elif spec.cadence == "daily" and open_days.get(day) is False:
                state, detail = "not_due", "休市（trade_cal）"
            elif (spec.cadence == "monthly_closed"
                  and (expected := _last_closed_day(open_days, day.year, day.month)) is not None
                  and day != expected):
                state, detail = "not_due", f"非执行日（当月最后休市日 {expected.isoformat()}）"
            elif h:
                detail = str(h[-1].get("reason_code", "")) or None
            cells.append(ServiceCell(day=day, state=state, run_id=run_id, detail=detail))
        latest_run = next((run for run in reversed(runs)
                           if effective_status(run, now=now.replace(tzinfo=None)) != "skipped"),
                          runs[-1] if runs else None)
        latest_h = next((r for r in reversed(health) if r.get("service_id") == spec.id), None)
        latest_at = _aware(latest_run.get("finished_at") or latest_run.get("started_at")) if latest_run else (
            latest_h.get("observed_at_utc") if latest_h else None)
        latest_state = next((cell.state for cell in reversed(cells)
                             if cell.state not in ("unknown", "not_due")), cells[-1].state)
        duration = None
        if latest_run:
            a, b = _aware(latest_run.get("started_at")), _aware(latest_run.get("finished_at"))
            duration = (b - a).total_seconds() if a and b else None
        durations: list[ServiceDurationPoint] = []
        for run in runs:
            a, b = _aware(run.get("started_at")), _aware(run.get("finished_at"))
            if a is not None and b is not None and b >= a:
                durations.append(ServiceDurationPoint(
                    run_id=str(run.get("run_id", "")), started_at=a,
                    duration_seconds=(b - a).total_seconds(),
                ))
        recent = [self._run_item(run, now) for run in reversed(runs[-5:])]
        trend = durations[-20:]
        average = (sum(point.duration_seconds for point in trend) / len(trend)
                   if trend else None)
        return ServiceRow(id=spec.id, name=spec.name, kind=spec.kind, cadence=spec.cadence, node=spec.host,
                          schedule=spec.schedule, latest_state=latest_state, latest_at=latest_at,
                          completion_ratio_30d=(successful / observed if observed else None),
                          duration_seconds=duration, average_duration_seconds=average,
                          duration_history=trend, recent_runs=recent, version=version,
                          cells=cells)

    def _service_versions(self) -> dict[str, str]:
        snapshot = self.releases()
        if not isinstance(snapshot, ReleasesView) or snapshot.stale:
            return {}
        return {component.id: component.current_sha for component in snapshot.components
                if component.current_sha}

    @staticmethod
    def _run_item(run: dict, now: datetime) -> ServiceRunItem:
        session = run.get("session")
        return ServiceRunItem(
            run_id=str(run.get("run_id", "")), service_id=str(run.get("service", "")),
            session=date.fromisoformat(session) if session else None,
            started_at=_aware(run.get("started_at")), finished_at=_aware(run.get("finished_at")),
            state=effective_status(run, now=now.replace(tzinfo=None)),
            note=str(run.get("note") or ""), error=str(run.get("error") or ""),
            steps=[ServiceStep(name=str(s.get("name", "")), state=str(s.get("status", "unknown")),
                               started_at=_aware(s.get("started_at")), finished_at=_aware(s.get("finished_at")),
                               detail=str(s.get("detail") or s.get("error") or ""),
                               affects_run=bool(s.get("affects_run", True)))
                   for s in run.get("steps", [])])

    def nodes(self) -> NodesView | OperationsDisabled:
        return self._snapshot("nodes.json", NodesView)

    def releases(self) -> ReleasesView | OperationsDisabled:
        return self._snapshot("releases.json", ReleasesView)

    def _snapshot(self, name: str, model):
        if self.snapshot_root is None:
            return OperationsDisabled()
        path = self.snapshot_root / name
        if path.is_symlink() or not path.is_file():
            return OperationsDisabled()
        try:
            result = model.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return OperationsDisabled()
        now = self.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=SHANGHAI)
        stale = (now.astimezone(timezone.utc) - result.captured_at.astimezone(timezone.utc)).total_seconds() > result.ttl_seconds
        missing = dict(result.missing)
        if stale:
            missing["snapshot"] = "采集快照已过期"
        return result.model_copy(update={"stale": stale, "missing": missing})
