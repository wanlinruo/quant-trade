"""运维只读查询：ServiceRun/observer 本地库与受管 JSON 快照。"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from qsys.ops.health import SHANGHAI
from qsys.ops.services import SERVICES, effective_status, load_runs, run_session
from qsys.webapp.contracts.operations import (
    NodesView, OperationsDisabled, OpsState, ReleasesView,
    ServiceCell, ServiceHealthItem, ServiceRow, ServiceRunItem, ServicesView, ServiceStep,
)


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


class OperationsQuery:
    def __init__(self, runs_root: Path, health_store=None, snapshot_root: Path | None = None,
                 *, now=lambda: datetime.now(SHANGHAI)) -> None:
        self.runs_root = Path(runs_root)
        self.health_store = health_store
        self.snapshot_root = Path(snapshot_root) if snapshot_root is not None else None
        self.now = now

    def services(self, selected: date | None = None) -> ServicesView:
        now = self.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=SHANGHAI)
        selected = selected or now.date()
        start = selected - timedelta(days=29)
        days = [start + timedelta(days=i) for i in range(30)]
        runs = load_runs(self.runs_root, start, selected)
        by_service: dict[str, list[dict]] = {}
        for run in runs:
            by_service.setdefault(str(run.get("service", "")), []).append(run)
        health = self._health(start, selected, now)
        rows = [self._service_row(spec, days, by_service.get(spec.id, []), health, now)
                for spec in SERVICES]
        selected_runs = [self._run_item(run, now) for run in runs if run_session(run) == selected.isoformat()]
        missing = {}
        if self.health_store is None:
            missing["health"] = "health-observer 本地只读库未配置"
        elif not health:
            missing["health"] = "所选窗口没有健康观察记录"
        return ServicesView(selected_day=selected, days=days, services=rows, health=[
            ServiceHealthItem(service_id=r["service_id"], observed_at=r["observed_at_utc"],
                              state=self._health_state(r.get("state")),
                              process_state=str(r.get("process_state", "unknown")),
                              business_state=str(r.get("business_state", "unknown")),
                              reason_code=str(r.get("reason_code", "unknown")))
            for r in health if r["observed_at_utc"].astimezone(SHANGHAI).date() == selected
        ], runs=selected_runs, missing=missing)

    @staticmethod
    def _health_state(state: object) -> OpsState:
        return {"healthy": "ok", "degraded": "warning", "down": "failed",
                "planned": "paused"}.get(str(state), "unknown")

    def _health(self, start: date, end: date, now: datetime) -> list[dict]:
        if self.health_store is None:
            return []
        begin = datetime.combine(start, time.min, tzinfo=SHANGHAI)
        finish = min(now, datetime.combine(end + timedelta(days=1), time.min, tzinfo=SHANGHAI))
        try:
            return self.health_store.history(begin, finish)
        except Exception:
            return []

    def _service_row(self, spec, days: list[date], runs: list[dict], health: list[dict],
                     now: datetime) -> ServiceRow:
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
                observed += 1
                state = _run_state(raw_states[-1])
                if state == "ok" and any(_run_state(s) == "failed" for s in raw_states[:-1]):
                    state = "fixed"
                if state in ("ok", "fixed"):
                    successful += 1
                run_id = str(mine[-1].get("run_id"))
                detail = mine[-1].get("note") or mine[-1].get("error") or None
            elif h:
                observed += 1
                state = self._health_state(h[-1].get("state"))
                if state == "ok":
                    successful += 1
                detail = str(h[-1].get("reason_code", "")) or None
            cells.append(ServiceCell(day=day, state=state, run_id=run_id, detail=detail))
        latest_run = runs[-1] if runs else None
        latest_h = next((r for r in reversed(health) if r.get("service_id") == spec.id), None)
        latest_at = _aware(latest_run.get("finished_at") or latest_run.get("started_at")) if latest_run else (
            latest_h.get("observed_at_utc") if latest_h else None)
        latest_state = cells[-1].state
        duration = None
        if latest_run:
            a, b = _aware(latest_run.get("started_at")), _aware(latest_run.get("finished_at"))
            duration = (b - a).total_seconds() if a and b else None
        return ServiceRow(id=spec.id, name=spec.name, kind=spec.kind, node=spec.host,
                          schedule=spec.schedule, latest_state=latest_state, latest_at=latest_at,
                          completion_ratio_30d=(successful / observed if observed else None),
                          duration_seconds=duration, cells=cells)

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
