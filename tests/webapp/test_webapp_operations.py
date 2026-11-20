from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient

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
