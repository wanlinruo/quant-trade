"""服务清单与运行记录：每天跑了哪些任务、访问了哪些接口、写了哪些表、结果如何。

- `SERVICES` 是唯一的服务清单，页面与质检都读它，不在别处另写一份。
- `ServiceRun` 由定时任务在执行时写运行记录：一次执行一个 JSON 文件，
  `<root>/YYYY/MM/DD/<服务>-<HHMMSS>-<pid>.json`，每步结束就原子落盘一次，
  进程中途被杀也能看到停在哪一步。
- **记录失败绝不影响任务本身**：写文件出错只打日志。

取代原先空转的 `ingest_log`（Tushare 同步从不写它，D-18）。
"""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

SUCCEEDED, FAILED, WARNING, SKIPPED, RUNNING = "succeeded", "failed", "warning", "skipped", "running"


@dataclass(frozen=True, slots=True)
class ServiceSpec:
    id: str
    name: str
    kind: str  # timer（定时任务）或 resident（常驻服务）
    host: str
    unit: str | None
    schedule: str
    interfaces: tuple[str, ...]
    writes: tuple[str, ...]
    purpose: str
    # 定时任务：交易日几点前应当已有运行记录（宽限后仍无记录判「未运行」）
    due: str | None = None
    # 超过这么久仍是 running，视为进程已中断
    max_runtime: timedelta = timedelta(hours=3)
    heartbeat: str | None = None


SERVICES: tuple[ServiceSpec, ...] = (
    ServiceSpec(
        "premarket-limits", "盘前涨跌停价", "timer", "Linux", "qsys-premarket-limits",
        "交易日 08:58，每分钟重试到 09:25",
        ("Tushare trade_cal", "Tushare stk_limit", "Redis 发布给盘中进程"),
        ("price_limits_daily",),
        "开盘前取当日涨跌停价，供模拟盘、影子盘撮合与行情页使用",
        due="09:30", max_runtime=timedelta(hours=1),
    ),
    ServiceSpec(
        "daily-archive", "每日归档（QMT）", "timer", "Linux → Windows QMT", "qsys-daily-archive",
        "交易日 15:40",
        ("Tushare trade_cal", "QMT 竞价逐笔", "QMT 1 分钟线"),
        ("auction_ticks", "minute_bars"),
        "收盘后采集 QMT 独有、会滚动过期的数据：首板次日候选的竞价逐笔与分钟线",
        due="17:00", max_runtime=timedelta(hours=2),
    ),
    ServiceSpec(
        "tushare-sync", "Tushare 收盘同步", "timer", "Linux", "qsys-tushare-sync",
        "交易日 17:00，19:00 补跑",
        ("Tushare trade_cal", "Tushare daily", "Tushare daily_basic", "Tushare adj_factor",
         "Tushare stk_limit", "Tushare suspend_d", "Tushare stock_st", "Tushare index_daily",
         "Tushare stock_basic", "Tushare namechange", "Tushare limit_list_d"),
        ("tushare_daily", "price_limits_daily", "suspend_daily", "stock_st_daily",
         "index_bars", "instruments", "name_history", "limit_list_daily",
         "adj_factors", "daily_flags", "market_regime", "valuation_snapshot", "delisting_pool",
         "metric_checks"),
        "收盘后同步日线、涨跌停价、停牌、ST、指数、标的与改名记录、涨停列表，再计算派生表（含退市整理期池）并跑增量质检",
        due="19:30", max_runtime=timedelta(hours=2),
    ),
    ServiceSpec(
        "service-checks", "每日服务质检", "timer", "Linux", "qsys-service-checks",
        "交易日 20:00",
        ("仓库只读", "运行记录"), ("var/service-runs（只写质检结果）",),
        "检查上面每个任务当天该写的表是否到齐、数量是否正确，结果挂回各任务的看板格子",
        due="21:00", max_runtime=timedelta(minutes=30),
    ),
    ServiceSpec(
        "paper", "模拟交易", "resident", "Linux", "qsys-paper", "常驻",
        ("Redis 行情", "price_limits_daily"), ("workbench.sqlite3",),
        "按实时行情运行模拟账户", heartbeat="paper",
    ),
    ServiceSpec(
        "shadow-core", "影子盘策略核心", "resident", "Linux", "qsys-shadow-core", "常驻",
        ("Redis 行情与事件",), ("live_*",),
        "按实盘口径运行策略但从不下单", heartbeat="core",
    ),
    ServiceSpec(
        "monitor", "监控网页", "resident", "Linux", "qsys-monitor", "常驻",
        ("仓库只读", "Redis 只读"), (), "本页面（8443 经 nginx 反代）",
    ),
    ServiceSpec(
        "redis-tunnel", "Redis 加密隧道", "resident", "Linux → Windows", "qsys-redis-tunnel",
        "常驻", ("SSH 隧道",), (), "Linux 与 Windows 之间行情与事件的通道",
    ),
    ServiceSpec(
        "market-gateway", "QMT 行情网关", "resident", "Windows", None, "常驻（计划任务）",
        ("QMT 行情", "Redis 发布"), (), "把 QMT 实时行情推给 Linux 各进程",
        heartbeat="market",
    ),
    ServiceSpec(
        "shadow-agent", "影子盘执行代理", "resident", "Windows", None, "常驻",
        ("QMT 交易接口（影子模式不下单）",), (), "执行端代理", heartbeat="agent",
    ),
)

SERVICE_BY_ID = {spec.id: spec for spec in SERVICES}


def _now() -> datetime:
    return datetime.now()


def _stamp(moment: datetime | None) -> str | None:
    return moment.isoformat(timespec="seconds") if moment else None


@dataclass(slots=True)
class Step:
    name: str
    interface: str | None = None
    table: str | None = None
    started_at: datetime = field(default_factory=_now)
    finished_at: datetime | None = None
    status: str = RUNNING
    rows: int | None = None
    detail: str = ""
    error: str = ""

    def fail(self, detail: str) -> None:
        """不抛异常地把这一步记为失败（任务按原逻辑继续）。"""
        self.status, self.detail = FAILED, detail

    def warn(self, detail: str) -> None:
        self.status, self.detail = WARNING, detail

    def skip(self, detail: str) -> None:
        self.status, self.detail = SKIPPED, detail

    def as_dict(self) -> dict:
        return {
            "name": self.name, "interface": self.interface, "table": self.table,
            "started_at": _stamp(self.started_at), "finished_at": _stamp(self.finished_at),
            "status": self.status, "rows": self.rows, "detail": self.detail, "error": self.error,
        }


class ServiceRun:
    """一次任务执行的运行记录。用 `ServiceRun.open(...)` 作为上下文管理器。"""

    def __init__(self, root: Path | None, service: str) -> None:
        if service not in SERVICE_BY_ID:
            raise ValueError(f"未登记的服务：{service}")
        self.root = Path(root) if root is not None else None
        self.service = service
        self.started_at = _now()
        self.finished_at: datetime | None = None
        self.session: date | None = None
        self.status = RUNNING
        self.note = ""
        self.error = ""
        self.steps: list[Step] = []
        self.run_id = f"{service}-{self.started_at:%H%M%S}-{os.getpid()}"

    @classmethod
    @contextmanager
    def open(cls, root: Path | None, service: str) -> Iterator[ServiceRun]:
        run = cls(root, service)
        run._save()
        try:
            yield run
        except BaseException as exc:
            run.error = f"{type(exc).__name__}: {exc}"
            run._finish(FAILED)
            raise
        else:
            run._finish(None)

    @contextmanager
    def step(self, name: str, *, interface: str | None = None,
             table: str | None = None) -> Iterator[Step]:
        current = Step(name, interface, table)
        self.steps.append(current)
        self._save()
        try:
            yield current
        except BaseException as exc:
            current.status, current.error = FAILED, f"{type(exc).__name__}: {exc}"
            raise
        else:
            if current.status == RUNNING:
                current.status = SUCCEEDED
        finally:
            current.finished_at = _now()
            self._save()

    def add_step(self, name: str, *, status: str, interface: str | None = None,
                 table: str | None = None, rows: int | None = None, detail: str = "",
                 started_at: datetime | None = None) -> Step:
        """补记一个已经结束的步骤（线性脚本里不便包 with 的地方）。"""
        done = Step(name, interface, table, started_at=started_at or _now(),
                    finished_at=_now(), status=status, rows=rows, detail=detail)
        self.steps.append(done)
        self._save()
        return done

    def skip(self, note: str) -> None:
        """整次执行无事可做（例如非交易日）。"""
        self.status, self.note = SKIPPED, note

    @property
    def path(self) -> Path | None:
        if self.root is None:
            return None
        day = self.started_at
        return self.root / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}" / f"{self.run_id}.json"

    def _finish(self, forced: str | None) -> None:
        self.finished_at = _now()
        if forced:
            self.status = forced
        elif self.status != SKIPPED:
            statuses = {s.status for s in self.steps}
            self.status = (FAILED if FAILED in statuses
                           else WARNING if WARNING in statuses else SUCCEEDED)
        self._save()

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id, "service": self.service, "host": socket.gethostname(),
            "pid": os.getpid(), "started_at": _stamp(self.started_at),
            "finished_at": _stamp(self.finished_at),
            "session": self.session.isoformat() if self.session else None,
            "status": self.status, "note": self.note, "error": self.error,
            "steps": [s.as_dict() for s in self.steps],
        }

    def _save(self) -> None:
        path = self.path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.as_dict(), ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.warning("service_run.save_failed", service=self.service, error=str(exc))


@contextmanager
def record_step(run: ServiceRun | None, name: str, *, interface: str | None = None,
                table: str | None = None) -> Iterator[Step]:
    """有运行记录就记一步；没有（测试、手工调用）就给一个不落盘的 Step。"""
    if run is None:
        yield Step(name, interface, table)
        return
    with run.step(name, interface=interface, table=table) as step:
        yield step


def load_runs(root: Path, start: date, end: date) -> list[dict]:
    """读 [start, end] 自然日内开始的运行记录，按开始时间排序。坏文件跳过并记日志。"""
    runs: list[dict] = []
    day = start
    while day <= end:
        folder = Path(root) / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
            try:
                runs.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                log.warning("service_run.unreadable", path=str(path), error=str(exc))
        day += timedelta(days=1)
    return sorted(runs, key=lambda r: r.get("started_at") or "")


def effective_status(run: dict, *, now: datetime) -> str:
    """running 超过该服务的最长运行时间，视为中断（进程被杀或机器重启）。"""
    if run.get("status") != RUNNING:
        return run.get("status") or FAILED
    spec = SERVICE_BY_ID.get(run.get("service", ""))
    started = datetime.fromisoformat(run["started_at"])
    limit = spec.max_runtime if spec else timedelta(hours=3)
    return "interrupted" if now - started > limit else RUNNING
