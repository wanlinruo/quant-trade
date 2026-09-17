"""服务管理页的视图模型：服务清单、每日执行看板、运行明细、常驻服务状态。

Web 层只摆事实：运行记录来自任务自己写的 JSON（`qsys.ops.services`），
进程状态来自 `systemctl show`（只读），Windows 进程状态来自 Redis 心跳。
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import structlog

from qsys.core.calendar import AShareCalendar
from qsys.ops.service_checks import parse_step_name
from qsys.ops.services import (
    FAILED,
    REPAIRED,
    RUNNING,
    SERVICE_BY_ID,
    SERVICES,
    SKIPPED,
    SUCCEEDED,
    WARNING,
    ServiceSpec,
    effective_status,
    load_runs,
)

log = structlog.get_logger(__name__)

_SYSTEMD_PROPS = ("ActiveState", "SubState", "Result", "ExecMainStatus",
                  "ExecMainExitTimestamp", "ActiveEnterTimestamp",
                  "NextElapseUSecRealtime", "LastTriggerUSec")

# 看板格子：css 类与中文标签
CELL_LABELS = {
    "ok": "成功", "warn": "有警告", "bad": "失败", "running": "运行中", "fixed": "已修复",
    "qcbad": "质检未过", "qcwarn": "质检警告",
    "missed": "未运行", "pending": "未到时间", "closed": "休市", "none": "无记录",
}


def systemctl_show(unit: str) -> dict[str, str]:
    """只读查询 systemd 单元属性；拿不到（非 Linux、无权限）返回空。"""
    try:
        out = subprocess.run(
            ["systemctl", "show", unit, *(f"-p{p}" for p in _SYSTEMD_PROPS)],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if out.returncode != 0:
        return {}
    props = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)
    return {k: v for k, v in props.items() if v and v != "n/a"}


@dataclass(frozen=True, slots=True)
class Cell:
    day: date
    state: str
    runs: list[dict]
    checks: list[dict] = ()

    @property
    def label(self) -> str:
        return CELL_LABELS[self.state]


class ServiceConsole:
    def __init__(
        self,
        runs_root: Path,
        *,
        calendar: Callable[[], AShareCalendar] | None = None,
        heartbeat: Callable[[str], dict] | None = None,
        systemd: Callable[[str], dict[str, str]] = systemctl_show,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._root = Path(runs_root)
        self._calendar = calendar
        self._heartbeat = heartbeat
        self._systemd = systemd
        self._now = now

    # ---- 清单 ----

    def services(self, kind: str) -> list[ServiceSpec]:
        return [spec for spec in SERVICES if spec.kind == kind]

    # ---- 每日看板 ----

    def _trading_days(self, start: date, end: date) -> set[date] | None:
        if self._calendar is None:
            return None
        try:
            return set(self._calendar().sessions_in_range(start, end))
        except Exception as exc:  # 交易日历取不到时看板照样打开，只是不判「未运行」
            log.warning("services.calendar_unavailable", error=str(exc))
            return None

    def board(self, days: int = 14) -> tuple[list[date], list[tuple[ServiceSpec, list[Cell]]], bool]:
        """(日期列, [(服务, 每日格子)], 是否拿到了交易日历)。"""
        now = self._now()
        end = now.date()
        start = end - timedelta(days=days - 1)
        columns = [start + timedelta(days=i) for i in range(days)][::-1]
        runs = load_runs(self._root, start, end)
        trading = self._trading_days(start, end)
        first_record = self._first_record_day()
        checks = _checks_by_day(runs)
        rows = []
        for spec in self.services("timer"):
            mine = [r for r in runs if r.get("service") == spec.id]
            cells = []
            for day in columns:
                today_runs = [r for r in mine if r["started_at"][:10] == day.isoformat()]
                found = checks.get((spec.id, day.isoformat()), [])
                state = _with_checks(self._cell_state(spec, day, today_runs, trading,
                                                      first_record, now), found)
                cells.append(Cell(day, state, today_runs, found))
            rows.append((spec, cells))
        return columns, rows, trading is not None

    def _first_record_day(self) -> date | None:
        years = sorted(p for p in self._root.glob("[0-9][0-9][0-9][0-9]") if p.is_dir())
        for year in years:
            for month in sorted(p for p in year.iterdir() if p.is_dir()):
                for day in sorted(p for p in month.iterdir() if p.is_dir()):
                    if any(day.glob("*.json")):
                        return date(int(year.name), int(month.name), int(day.name))
        return None

    def _cell_state(self, spec: ServiceSpec, day: date, runs: list[dict],
                    trading: set[date] | None, first_record: date | None,
                    now: datetime) -> str:
        if runs:
            statuses = [effective_status(r, now=now) for r in runs]
            meaningful = [s for s in statuses if s != SKIPPED]
            if not meaningful:
                return "closed"
            last = meaningful[-1]
            if last == RUNNING:
                return "running"
            if last == REPAIRED:
                return "fixed"
            if last == SUCCEEDED:
                # 当天先失败、后补跑成功：结果可用，但要让人看到有过失败
                return "fixed" if any(s in (FAILED, "interrupted") for s in meaningful) else "ok"
            if last == WARNING:
                return "warn"
            return "bad"
        if first_record is None or day < first_record:
            return "none"
        if trading is None:
            return "none"
        if day not in trading:
            return "closed"
        due = datetime.combine(day, time.fromisoformat(spec.due or "23:59"))
        return "missed" if now >= due else "pending"

    # ---- 运行明细 ----

    def run(self, day: date, run_id: str) -> dict | None:
        for run in load_runs(self._root, day, day):
            if run.get("run_id") == run_id:
                checks = []
                if run.get("session"):
                    session = date.fromisoformat(run["session"])
                    later = load_runs(self._root, session, session + timedelta(days=1))
                    checks = _checks_by_day(later).get((run.get("service"), run["session"]), [])
                return {**run, "effective_status": effective_status(run, now=self._now()),
                        "spec": SERVICE_BY_ID.get(run.get("service", "")), "checks": checks}
        return None

    def recent_runs(self, service: str, limit: int = 20) -> list[dict]:
        end = self._now().date()
        runs = [r for r in load_runs(self._root, end - timedelta(days=40), end)
                if r.get("service") == service]
        return [{**r, "effective_status": effective_status(r, now=self._now())}
                for r in runs[::-1][:limit]]

    # ---- 进程状态 ----

    def process_status(self) -> list[dict]:
        rows = []
        for spec in SERVICES:
            row: dict = {"spec": spec, "systemd": {}, "heartbeat": None}
            if spec.unit:
                unit = f"{spec.unit}.timer" if spec.kind == "timer" else f"{spec.unit}.service"
                row["systemd"] = self._systemd(unit)
                if spec.kind == "timer":
                    row["last_exit"] = self._systemd(f"{spec.unit}.service")
            if spec.heartbeat and self._heartbeat is not None:
                try:
                    row["heartbeat"] = self._heartbeat(spec.heartbeat)
                except Exception as exc:
                    log.warning("services.heartbeat_failed", source=spec.heartbeat,
                                error=str(exc))
            row["state"] = _process_state(spec, row)
            rows.append(row)
        return rows


def _checks_by_day(runs: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """(服务, 交易日) → 该交易日最后一次服务质检里属于这个服务的检查。"""
    latest: dict[str, dict] = {}
    for run in runs:
        if run.get("service") == "service-checks" and run.get("session"):
            latest[run["session"]] = run  # runs 已按开始时间排序，后者覆盖前者
    out: dict[tuple[str, str], list[dict]] = {}
    for session, run in latest.items():
        for step in run.get("steps", []):
            parsed = parse_step_name(step.get("name", ""))
            if parsed:
                service, name = parsed
                out.setdefault((service, session), []).append(
                    {**step, "check": name, "check_run_id": run["run_id"],
                     "check_started_at": run["started_at"]})
    return out


def _with_checks(state: str, checks: list[dict]) -> str:
    """任务本身成功但质检不过，格子要显示出来；任务失败时保持原状态。

    任务未运行、但它该产出的数据质检已通过（后续补上了），显示「已修复」。
    """
    if not checks:
        return state
    statuses = {c.get("status") for c in checks}
    if state == "missed":
        return state if FAILED in statuses else "fixed"
    if state not in ("ok", "warn", "fixed"):
        return state
    if FAILED in statuses:
        return "qcbad"
    if WARNING in statuses and state == "ok":
        return "qcwarn"
    if REPAIRED in statuses and state == "ok":
        return "fixed"
    return state


def _process_state(spec: ServiceSpec, row: dict) -> str:
    """ok / bad / unknown。定时任务看 timer 是否在等待、上次退出是否成功。"""
    systemd = row["systemd"]
    if spec.heartbeat and row["heartbeat"] is not None:
        return "ok" if row["heartbeat"].get("alive") else "bad"
    if not systemd:
        return "unknown"
    if spec.kind == "timer":
        last = row.get("last_exit", {})
        if systemd.get("ActiveState") != "active":
            return "bad"
        return "bad" if last.get("Result", "success") != "success" else "ok"
    return "ok" if systemd.get("ActiveState") == "active" else "bad"
