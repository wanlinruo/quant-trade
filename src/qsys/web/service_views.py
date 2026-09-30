"""服务管理页的视图模型：服务清单、每日执行看板、运行明细、常驻服务状态。

Web 层只摆事实：运行记录来自任务自己写的 JSON（`qsys.ops.services`），
进程状态来自 `systemctl show`（只读），Windows 进程状态来自 Redis 心跳。
"""

from __future__ import annotations

import base64
import csv
import io
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import structlog

from qsys.core.calendar import AShareCalendar
from qsys.ops.infra import systemctl_show
from qsys.ops.service_checks import parse_step_name
from qsys.ops.health import DEGRADED, DOWN, HEALTHY, SHANGHAI, HealthStore, assess_heartbeat
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

# 看板格子：css 类与中文标签
CELL_LABELS = {
    "ok": "成功", "warn": "有警告", "bad": "失败", "running": "运行中", "fixed": "已修复",
    "qcbad": "质检未过", "qcwarn": "质检警告",
    "missed": "未运行", "pending": "未到时间", "closed": "休市", "none": "无记录",
}




# schtasks /query /fo CSV /v 的列顺序在各语言下一致，取值按位置读，不认标题文字。
_TASK_COLUMNS = {"name": 1, "status": 3, "last_run": 5, "last_result": 6}
# 状态文字随系统语言变化；只映射需要区分的三种，其余原样返回由调用方判未知。
_TASK_STATES = {
    "ready": "Ready", "就绪": "Ready", "準備就緒": "Ready",
    "running": "Running", "正在运行": "Running", "執行中": "Running",
    "disabled": "Disabled", "已禁用": "Disabled", "已停用": "Disabled",
}


def windows_task_show(task: str, *, host: str, key: Path) -> dict:
    """经 SSH 只读查询 Windows 计划任务；连接失败时返回空，避免误报为禁用。

    用 `schtasks` 而不是 `Get-ScheduledTask`：后者走计划任务的 WMI 提供程序，
    该机上有 20 个系统自带任务的残留登记，枚举直接失败（2026-09-26 实测
    `Get-ScheduledTask` 与 CIM 均取不到，`schtasks` 正常），看门狗因此在时刻轴上
    连续显示"未知"。看门狗脚本本身用的就是 `Win32_Process` + `schtasks`，不受影响。
    """
    script = f"chcp 65001 > $null; schtasks /query /tn '{task}' /fo CSV /v"
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        out = subprocess.run(
            ["ssh", "-i", str(key), "-o", "BatchMode=yes", "-o", "ConnectTimeout=3",
             host, "powershell", "-NoProfile", "-EncodedCommand", encoded],
            # Windows PowerShell 的 stderr 可能带 GBK 编码的 CLIXML 进度信息；
            # 只解析 stdout，不能让 text=True 因 stderr 解码失败丢掉结果。
            capture_output=True, timeout=8,
        )
        if out.returncode != 0:
            return {}
        return parse_schtasks_csv(out.stdout.decode("utf-8", errors="replace"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def parse_schtasks_csv(text: str) -> dict:
    """把 `schtasks /fo CSV /v` 的一行解析成 State / LastTaskResult / LastRunTime。

    解析不出来就返回空字典——宁可报"取不到"，也不要编一个状态出来。
    """
    rows = [row for row in csv.reader(io.StringIO(text.strip())) if len(row) > 6]
    if len(rows) < 2:
        return {}
    values = rows[1]
    status = values[_TASK_COLUMNS["status"]].strip()
    try:
        result = int(values[_TASK_COLUMNS["last_result"]].strip())
    except ValueError:
        return {}
    return {
        "State": _TASK_STATES.get(status.casefold(), status),
        "LastTaskResult": result,
        "LastRunTime": _task_time(values[_TASK_COLUMNS["last_run"]].strip()),
    }


def _task_time(raw: str) -> str | None:
    """schtasks 的时间形如 2026/9/26 23:19:00；从未运行过时是 N/A 之类的占位。"""
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %I:%M:%S %p"):
        try:
            return datetime.strptime(raw, fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    return None


@dataclass(frozen=True, slots=True)
class Cell:
    day: date
    state: str
    runs: list[dict]
    checks: list[dict] = ()
    health: dict | None = None
    service_id: str | None = None

    @property
    def label(self) -> str:
        if self.health is not None:
            return {
                HEALTHY: "当前正常",
                DEGRADED: "事件延迟",
                DOWN: "事件失败",
            }.get(self.health.get("business_state"), "状态未知")
        return CELL_LABELS[self.state]

    @property
    def detail_url(self) -> str | None:
        """Return the run that actually contains this cell's details.

        A retry may start on the day after its logical session, and resident
        acceptance cells only have a service-check run.  The route therefore
        has to use the run's start date instead of the board column date.
        """
        if self.health is not None and self.service_id:
            return f"/services/{self.service_id}"
        if self.runs:
            run = self.runs[-1]
            return f"/services/runs/{run['started_at'][:10]}/{run['run_id']}"
        if self.checks:
            check = self.checks[-1]
            return (
                f"/services/runs/{check['check_started_at'][:10]}/"
                f"{check['check_run_id']}"
            )
        return None


class ServiceConsole:
    def __init__(
        self,
        runs_root: Path,
        *,
        calendar: Callable[[], AShareCalendar] | None = None,
        heartbeat: Callable[[str], dict] | None = None,
        windows_task: Callable[[str], dict] | None = None,
        health_store: HealthStore | None = None,
        systemd: Callable[[str], dict[str, str]] = systemctl_show,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._root = Path(runs_root)
        self._calendar = calendar
        self._heartbeat = heartbeat
        self._windows_task = windows_task
        self._health_store = health_store
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
            calendar = self._calendar()
            return None if calendar is None else set(calendar.sessions_in_range(start, end))
        except Exception as exc:  # 交易日历取不到时看板照样打开，只是不判「未运行」
            log.warning("services.calendar_unavailable", error=str(exc))
            return None

    def board(self, days: int = 14) -> tuple[list[date], list[tuple[ServiceSpec, list[Cell]]], bool]:
        """每日定时任务看板；常驻服务的业务验收与月度任务不混入这里。"""
        daily = [s for s in self.services("timer")
                 if s.cadence in ("daily", "everyday")]
        return self._board_for(daily, days)

    def periodic_board(
        self, days: int = 62,
    ) -> tuple[list[date], list[tuple[ServiceSpec, list[Cell]]], bool]:
        """月度任务看板：按月执行，天数窗口要盖到上个月的执行日。"""
        monthly = [s for s in self.services("timer") if s.cadence == "monthly"]
        return self._board_for(monthly, days)

    def acceptance_board(
        self, days: int = 14,
    ) -> tuple[list[date], list[tuple[ServiceSpec, list[Cell]]], bool]:
        """影子盘/模拟盘每日策略事件验收，与当前进程健康严格分离。"""
        specs = [SERVICE_BY_ID[name] for name in ("shadow-core", "paper")]
        columns, rows, has_calendar = self._board_for(specs, days)
        if self._health_store is None:
            return columns, rows, has_calendar

        now = self._now()
        start = datetime.combine(columns[-1], time.min)
        observations = self._health_store.history(start, now)
        latest: dict[tuple[str, date], dict] = {}
        for observation in observations:
            if observation["service_id"] not in ("shadow-core", "paper"):
                continue
            local_day = observation["observed_at_utc"].astimezone(SHANGHAI).date()
            latest[(observation["service_id"], local_day)] = observation

        enriched = []
        state_map = {HEALTHY: "ok", DEGRADED: "warn", DOWN: "bad"}
        for spec, cells in rows:
            updated = []
            for cell in cells:
                health = latest.get((spec.id, cell.day))
                if health is None or health.get("business_state") == "unknown":
                    updated.append(cell)
                    continue
                updated.append(Cell(
                    cell.day,
                    state_map.get(health["business_state"], "none"),
                    cell.runs,
                    cell.checks,
                    health,
                    spec.id,
                ))
            enriched.append((spec, updated))
        return columns, enriched, has_calendar

    def _board_for(
        self, specs: list[ServiceSpec], days: int,
    ) -> tuple[list[date], list[tuple[ServiceSpec, list[Cell]]], bool]:
        now = self._now()
        end = now.date()
        start = end - timedelta(days=days - 1)
        columns = [start + timedelta(days=i) for i in range(days)][::-1]
        runs = load_runs(self._root, start, end)
        trading = self._trading_days(start, end)
        first_record = self._first_record_day()
        checks = _checks_by_day(runs)
        rows = []
        for spec in specs:
            mine = [r for r in runs if r.get("service") == spec.id]
            cells = []
            for day in columns:
                today_runs = [r for r in mine if _run_session(r) == day.isoformat()]
                found = checks.get((spec.id, day.isoformat()), [])
                state = _with_checks(
                    self._cell_state(spec, day, today_runs, trading, first_record, now),
                    found, resident=spec.kind == "resident",
                )
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
        if spec.kind == "resident":
            # 常驻服务没有「本次执行」，状态完全由质检结论决定（见 _with_checks）
            return "ok"
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
        if spec.cadence == "monthly" and day.day != spec.run_day:
            return "none"      # 月度任务：不是执行日就不该有记录
        if day not in trading and spec.cadence != "everyday":
            return "closed"    # everyday 的任务休市日也该跑，缺了就是缺了
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

    def service_health(self, service: str) -> dict | None:
        """Latest persisted process/business observation for a service detail page."""
        if self._health_store is None:
            return None
        rows = self._health_store.latest(service)
        if not rows:
            return None
        row = rows[0]
        return {
            **row,
            "observed_at_local": row["observed_at_utc"].astimezone(SHANGHAI).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }

    # ---- 进程状态 ----

    def process_status(self) -> list[dict]:
        if self._health_store is not None:
            stored = {row["service_id"]: row for row in self._health_store.latest()}
            rows = []
            # The observer writes unchanged-state checkpoints every five
            # minutes.  Allow that interval plus scheduling/probe headroom;
            # 90 seconds would incorrectly mark healthy checkpoints stale for
            # most of every cycle.
            stale_after = self._health_store.checkpoint_interval + timedelta(minutes=2)
            for spec in SERVICES:
                observation = stored.get(spec.id)
                state = "unknown"
                if observation is not None:
                    observed = observation["observed_at_utc"]
                    reference = self._now()
                    if reference.tzinfo is None:
                        reference = reference.replace(tzinfo=SHANGHAI)
                    age = (reference.astimezone(timezone.utc) - observed).total_seconds()
                    if age <= stale_after.total_seconds():
                        state = {
                            HEALTHY: "ok", DEGRADED: "warn", DOWN: "bad"
                        }.get(observation["state"], "unknown")
                    else:
                        observation = {**observation, "state": "unknown",
                                       "reason_code": "observer_stale"}
                rows.append({
                    "spec": spec, "systemd": {}, "heartbeat": None,
                    "heartbeat_assessment": None, "windows_task": {},
                    "health_observation": observation, "state": state,
                })
            # Once a store is configured the request path is strictly local,
            # including before the observer has produced its first sample.
            return rows
        rows = []
        now = self._now()
        trading = self._trading_days(now.date(), now.date())
        for spec in SERVICES:
            row: dict = {"spec": spec, "systemd": {}, "heartbeat": None,
                         "heartbeat_assessment": None, "windows_task": {}}
            if spec.unit:
                unit = f"{spec.unit}.timer" if spec.kind == "timer" else f"{spec.unit}.service"
                row["systemd"] = self._systemd(unit)
                if spec.kind == "timer":
                    row["last_exit"] = self._systemd(f"{spec.unit}.service")
            if spec.heartbeat and self._heartbeat is not None:
                try:
                    row["heartbeat"] = self._heartbeat(spec.heartbeat)
                    advertised = row["heartbeat"].get("expected_interval_s")
                    row["heartbeat_assessment"] = assess_heartbeat(
                        spec.heartbeat, row["heartbeat"].get("last_beat"), now, trading,
                        active=advertised is not None and advertised <= 2,
                    )
                except Exception as exc:
                    log.warning("services.heartbeat_failed", source=spec.heartbeat,
                                error=str(exc))
            if spec.windows_task and self._windows_task is not None:
                try:
                    row["windows_task"] = self._windows_task(spec.windows_task)
                except Exception as exc:
                    log.warning("services.windows_task_failed", task=spec.windows_task,
                                error=str(exc))
            row["state"] = _process_state(spec, row)
            rows.append(row)
        return rows

    def health_timeline(self, hours: int = 24) -> list[dict]:
        if self._health_store is None:
            return []
        end = self._now()
        start = end - timedelta(hours=hours)
        grouped = self._health_store.timeline(start, end)
        total = (end - start).total_seconds()
        rows = []
        for service_id, segments in grouped.items():
            spec = SERVICE_BY_ID.get(service_id)
            # Daily timers answer whether their latest scheduled run
            # succeeded, so their history belongs on the execution board.
            # Supervisors such as the Windows watchdog still represent a
            # continuously expected protection capability and remain here.
            if spec is None or spec.kind == "timer":
                continue
            rows.append({
                "spec": spec,
                "segments": [
                    {**segment, "width": 100 * segment["duration_s"] / total}
                    for segment in segments
                ],
            })
        return sorted(rows, key=lambda row: row["spec"].name)


def _run_session(run: dict) -> str:
    """这次执行算在哪个交易日：任务自己记的 `session`。

    没记 session 的（非交易日跳过、刚起步就崩）退回启动日期——次日补跑的活
    属于它检查的那个交易日，不属于启动那天。
    """
    return run.get("session") or (run.get("started_at") or "")[:10]


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


def _with_checks(state: str, checks: list[dict], *, resident: bool = False) -> str:
    """任务本身成功但质检不过，格子要显示出来；任务失败时保持原状态。

    任务未运行、但它该产出的数据质检已通过（后续补上了），显示「已修复」。
    """
    if not checks:
        return "none" if resident else state
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
    if spec.windows_task:
        task = row["windows_task"]
        if not task:
            return "unknown"
        return ("ok" if task.get("State") in ("Ready", "Running")
                and task.get("LastTaskResult") == 0 else "bad")
    if spec.heartbeat:
        if row["heartbeat"] is None:
            return "unknown"
        assessment = row.get("heartbeat_assessment")
        if assessment is None:
            return "unknown"
        return {HEALTHY: "ok", DEGRADED: "warn", DOWN: "bad"}.get(
            assessment.state, "unknown"
        )
    if not systemd:
        return "unknown"
    if spec.kind == "timer":
        last = row.get("last_exit", {})
        if systemd.get("ActiveState") != "active":
            return "bad"
        return "bad" if last.get("Result", "success") != "success" else "ok"
    return "ok" if systemd.get("ActiveState") == "active" else "bad"
