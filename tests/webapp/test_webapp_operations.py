from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient

from qsys.ops.health import DOWN, HEALTHY, UNKNOWN, HealthObservation, HealthStore, SHANGHAI
from qsys.ops.services import SERVICES
from qsys.webapp.queries.operations import OperationsQuery
from qsys.webapp.routes.operations import operations_router

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 19, 14, 0, tzinfo=TZ)


def _run(root: Path, status: str, run_id: str, hour: int) -> None:
    folder = root / "2026" / "11" / "19"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{run_id}.json").write_text(json.dumps({
        "run_id": run_id, "service": "tushare-sync", "session": "2026-11-19",
        "started_at": f"2026-11-19T{hour:02d}:00:00", "finished_at": f"2026-11-19T{hour:02d}:01:00",
        "status": status, "note": "", "error": "" if status == "succeeded" else "源失败",
        "steps": [{"name": "同步", "status": status, "started_at": f"2026-11-19T{hour:02d}:00:00",
                   "finished_at": f"2026-11-19T{hour:02d}:01:00", "detail": "事实", "affects_run": True}],
    }), encoding="utf-8")


def test_services_preserves_failure_then_marks_latest_fixed(tmp_path):
    root = tmp_path / "runs"
    _run(root, "failed", "r1", 10)
    _run(root, "succeeded", "r2", 11)
    got = OperationsQuery(root, now=lambda: NOW).services(date(2026, 11, 19))
    row = next(x for x in got.services if x.id == "tushare-sync")
    assert row.cells[-1].state == "fixed"
    assert [x.state for x in got.runs] == ["failed", "succeeded"]
    assert got.runs[0].error == "源失败"
    assert [x.duration_seconds for x in row.duration_history] == [60, 60]
    assert row.average_duration_seconds == 60
    assert [x.run_id for x in row.recent_runs] == ["r2", "r1"]
    assert row.version is None


def test_services_marks_only_proven_not_due_days(tmp_path):
    root = tmp_path / "runs"
    folder = root / "2026" / "11" / "19"
    folder.mkdir(parents=True)
    (folder / "closed.json").write_text(json.dumps({
        "run_id": "closed", "service": "tushare-sync", "session": "2026-11-19",
        "started_at": "2026-11-19T17:00:00", "finished_at": "2026-11-19T17:00:01",
        "status": "skipped", "note": "不是交易日", "error": "", "steps": [],
    }), encoding="utf-8")
    got = OperationsQuery(root, now=lambda: NOW).services(date(2026, 11, 19))
    daily = next(x for x in got.services if x.id == "tushare-sync")
    monthly = next(x for x in got.services if x.id == "auction-study")
    assert daily.cells[-1].state == "not_due"
    assert daily.completion_ratio_30d is None
    assert monthly.cells[-1].state == "not_due"
    assert monthly.cells[11].day == date(2026, 11, 1)
    assert monthly.cells[11].state == "unknown"


def test_timer_schedule_ready_observation_is_not_run_evidence(tmp_path):
    """#700: 周末 timer 只有“已就绪”观测、没有 ServiceRun 时不能画成正常或计入成功率。"""
    store = HealthStore(tmp_path / "health.sqlite3")
    saturday = datetime(2026, 11, 21, 12, tzinfo=TZ)
    base = HealthObservation(
        service_id="", observed_at_utc=saturday.astimezone(timezone.utc), phase="evening",
        expected_interval_s=30, warn_after_s=60, down_after_s=90,
        state=HEALTHY, process_state=HEALTHY, business_state=UNKNOWN,
        reason_code="schedule_ready", source="systemd", last_beat_at_utc=None, details={},
    )
    for sid in ("tushare-sync", "auction-study", "paper"):
        store.record(replace(base, service_id=sid))
    store.record(replace(base, service_id="catalog-verify", state=DOWN, process_state=DOWN,
                         reason_code="scheduled_task_failed"))
    got = OperationsQuery(tmp_path / "runs", health_store=store,
                          now=lambda: saturday).services(date(2026, 11, 21))
    rows = {x.id: x for x in got.services}
    daily = rows["tushare-sync"].cells[-1]
    assert (daily.state, daily.run_id, daily.detail) == ("unknown", None, "schedule_ready")
    assert rows["tushare-sync"].completion_ratio_30d is None
    assert rows["auction-study"].cells[-1].state == "not_due"
    assert rows["catalog-verify"].cells[-1].state == "failed"
    assert rows["paper"].cells[-1].state == "ok"


def _weekday_calendar(start: date, end: date) -> dict[date, bool]:
    """测试夹具：显式给出每一天的开闭市事实（夹具内按星期构造，生产只读 trade_cal）。"""
    return {start + timedelta(days=i): (start + timedelta(days=i)).weekday() < 5
            for i in range((end - start).days + 1)}


def test_trading_calendar_marks_closed_days_not_due(tmp_path):
    saturday = datetime(2026, 11, 21, 21, tzinfo=TZ)
    got = OperationsQuery(tmp_path / "runs", now=lambda: saturday,
                          trading_days=_weekday_calendar).services(date(2026, 11, 21))
    rows = {x.id: x for x in got.services}
    cells = {c.day: c for c in rows["tushare-sync"].cells}
    assert (cells[date(2026, 11, 21)].state, cells[date(2026, 11, 21)].detail) == (
        "not_due", "休市（trade_cal）")
    # 交易日没有运行记录：不由日历推断成功或失败，保持未知
    assert cells[date(2026, 11, 20)].state == "unknown"
    # everyday 任务休市日也该运行，日历不能把它标成未到期
    assert rows["daily-digest"].cells[-1].state == "unknown"
    # monthly_closed：11 月最后一个休市日是 11-29，其余日子未到期；10-31 是 10 月执行日
    drill = {c.day: c for c in rows["reboot-drill"].cells}
    assert drill[date(2026, 11, 21)].state == "not_due"
    assert drill[date(2026, 10, 31)].state == "unknown"
    assert got.trading_days[date(2026, 11, 21)] is False
    assert got.trading_days[date(2026, 11, 20)] is True
    assert "calendar" not in got.missing


def test_trading_calendar_gaps_and_errors_stay_unknown(tmp_path):
    saturday = datetime(2026, 11, 21, 21, tzinfo=TZ)

    def gappy(start, end):
        full = _weekday_calendar(start, end)
        full.pop(date(2026, 11, 21))
        full.pop(date(2026, 11, 30))  # 月末缺行：无法确定 11 月最后休市日
        return full

    got = OperationsQuery(tmp_path / "runs", now=lambda: saturday,
                          trading_days=gappy).services(date(2026, 11, 21))
    rows = {x.id: x for x in got.services}
    assert rows["tushare-sync"].cells[-1].state == "unknown"
    assert rows["tushare-sync"].cells[-2].state == "unknown"  # 11-20 交易日，无运行
    assert next(c for c in rows["tushare-sync"].cells
                if c.day == date(2026, 11, 15)).state == "not_due"
    assert rows["reboot-drill"].cells[-1].state == "unknown"
    assert date(2026, 11, 21) not in got.trading_days
    assert got.missing["calendar"] == "交易日历缺少部分日期，缺行日期保持未知"

    def broken(start, end):
        raise OSError("warehouse unavailable")

    failed = OperationsQuery(tmp_path / "runs", now=lambda: saturday,
                             trading_days=broken).services(date(2026, 11, 21))
    assert next(x for x in failed.services if x.id == "tushare-sync").cells[-1].state == "unknown"
    assert failed.trading_days == {}
    assert failed.missing["calendar"] == "交易日历读取失败，休市日保持未知"


def test_closed_day_run_still_wins_over_calendar(tmp_path):
    root = tmp_path / "runs"
    folder = root / "2026" / "11" / "21"
    folder.mkdir(parents=True)
    (folder / "weekend.json").write_text(json.dumps({
        "run_id": "weekend", "service": "tushare-sync", "session": "2026-11-21",
        "started_at": "2026-11-21T10:00:00", "finished_at": "2026-11-21T10:01:00",
        "status": "failed", "note": "", "error": "手工补跑失败", "steps": [],
    }), encoding="utf-8")
    got = OperationsQuery(root, now=lambda: datetime(2026, 11, 21, 21, tzinfo=TZ),
                          trading_days=_weekday_calendar).services(date(2026, 11, 21))
    cell = next(x for x in got.services if x.id == "tushare-sync").cells[-1]
    assert (cell.state, cell.run_id) == ("failed", "weekend")


def test_later_skip_does_not_hide_same_day_failure(tmp_path):
    root = tmp_path / "runs"
    _run(root, "failed", "failed-first", 10)
    folder = root / "2026" / "11" / "19"
    (folder / "skipped-last.json").write_text(json.dumps({
        "run_id": "skipped-last", "service": "tushare-sync", "session": "2026-11-19",
        "started_at": "2026-11-19T12:00:00", "finished_at": "2026-11-19T12:00:01",
        "status": "skipped", "note": "没有新输入", "error": "", "steps": [],
    }), encoding="utf-8")
    row = next(x for x in OperationsQuery(root, now=lambda: NOW).services().services
               if x.id == "tushare-sync")
    assert row.cells[-1].state == "failed"
    assert row.cells[-1].run_id == "failed-first"


def _nodes(captured: datetime) -> dict:
    return {"schema_version": 1, "enabled": True, "captured_at": captured.isoformat(),
            "ttl_seconds": 60, "stale": False, "nodes": [], "links": [], "processes": [],
            "security": [], "missing": {}}


def test_snapshot_staleness_and_symlink_are_explicit(tmp_path):
    root = tmp_path / "snapshots"; root.mkdir()
    (root / "nodes.json").write_text(json.dumps(_nodes(NOW - timedelta(minutes=2))), encoding="utf-8")
    q = OperationsQuery(tmp_path / "runs", snapshot_root=root, now=lambda: NOW)
    got = q.nodes()
    assert got.enabled and got.stale
    assert got.missing["snapshot"] == "采集快照已过期"
    (root / "nodes.json").unlink()
    target = tmp_path / "outside.json"; target.write_text(json.dumps(_nodes(NOW)), encoding="utf-8")
    (root / "nodes.json").symlink_to(target)
    assert q.nodes().enabled is False


def test_operations_routes_are_no_store_and_disabled_without_query():
    app = FastAPI(); app.include_router(operations_router(None))
    response = TestClient(app).get("/api/app/operations/nodes")
    assert response.json() == {"schema_version": 1, "enabled": False}
    assert response.headers["cache-control"] == "no-store"


class _LegacyDailyHealth:
    """Pre-optimization semantics: load every row, then retain daily tails."""

    def __init__(self, store: HealthStore) -> None:
        self.store = store

    def history(self, start, end, service_id=None):
        return self.store.history(start, end, service_id)

    def daily_latest(self, start, end):
        latest = {}
        for row in self.store.history(start, end):
            latest[(row["service_id"], row["observed_at_utc"].astimezone(SHANGHAI).date())] = row
        return list(latest.values())


def test_services_production_shape_daily_reduction_is_exact(tmp_path):
    assert len(SERVICES) == 20
    store = HealthStore(tmp_path / "health.sqlite3")
    begin = datetime(2026, 10, 22, 2, tzinfo=timezone.utc)
    template = HealthObservation(
        service_id="", observed_at_utc=begin, phase="morning",
        expected_interval_s=30, warn_after_s=60, down_after_s=90,
        state=DOWN, process_state=DOWN, business_state=DOWN,
        reason_code="test", source="fixture", last_beat_at_utc=None,
        details={"shape": "production"},
    )
    for spec in SERVICES:
        for offset in range(30):
            moment = begin + timedelta(days=offset)
            store.record(replace(template, service_id=spec.id, observed_at_utc=moment))
            store.record(replace(
                template, service_id=spec.id, observed_at_utc=moment + timedelta(minutes=5),
                state=UNKNOWN, process_state=UNKNOWN, business_state=UNKNOWN,
            ))
    _run(tmp_path / "runs", "succeeded", "selected-with-step", 11)

    optimized = OperationsQuery(
        tmp_path / "runs", health_store=store, now=lambda: NOW,
    ).services(date(2026, 11, 19))
    legacy = OperationsQuery(
        tmp_path / "runs", health_store=_LegacyDailyHealth(store), now=lambda: NOW,
    ).services(date(2026, 11, 19))

    assert optimized.model_dump() == legacy.model_dump()
    assert len(optimized.services) == 20
    assert all(len(row.cells) == 30 for row in optimized.services)
    assert optimized.runs[0].steps[0].name == "同步"


class _CountingHealth:
    def __init__(self) -> None:
        self.daily_calls = 0
        self.history_calls = 0
        self.fail = False
        self.lock = threading.Lock()

    def daily_latest(self, start, end):
        with self.lock:
            self.daily_calls += 1
        time.sleep(0.02)
        if self.fail:
            raise RuntimeError("health unavailable")
        return []

    def history(self, start, end):
        with self.lock:
            self.history_calls += 1
        return []


def test_services_concurrent_requests_share_one_health_read(tmp_path):
    health = _CountingHealth()
    query = OperationsQuery(tmp_path / "runs", health_store=health, now=lambda: NOW)
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(
            lambda _: query.services(date(2026, 11, 19)).model_dump(), range(16),
        ))
    assert all(result == results[0] for result in results)
    assert (health.daily_calls, health.history_calls) == (1, 0)


def test_services_refresh_error_returns_marked_stale_value(tmp_path, monkeypatch):
    monkeypatch.setattr("qsys.webapp.queries.operations.SERVICES_TTL_SECONDS", 0.0)
    health = _CountingHealth()
    query = OperationsQuery(tmp_path / "runs", health_store=health, now=lambda: NOW)
    first = query.services(date(2026, 11, 19))
    health.fail = True
    stale = query.services(date(2026, 11, 19))
    assert stale.services == first.services
    assert stale.missing["cache_stale"] == "服务看板刷新失败，当前为上一份成功结果"
    assert stale.missing["cache_error"] == "RuntimeError"
