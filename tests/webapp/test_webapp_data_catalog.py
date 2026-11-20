"""指标字典查询（#662 R4）：状态判定、结构化来源、筛选与缓存。"""

from __future__ import annotations

from datetime import date, datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from qsys.data import schema
from qsys.data.catalog import REGISTRY
from qsys.data.catalog.types import UNKNOWN, Computed, Direct, Uncovered
from qsys.data.query import WarehouseQuery
from qsys.webapp.app import WebappDeps, create_app
from qsys.webapp.live_kill_status import LiveKillReader
from qsys.webapp.contracts.data import MetricDetail, MetricList
from qsys.webapp.queries.data_catalog import DataCatalog

COVERED = [m for m, x in REGISTRY.items() if x.meaning is not UNKNOWN and not isinstance(x.checks, Uncovered)]
METRIC = COVERED[0]
DIRECT = next(m for m, x in REGISTRY.items() if isinstance(x.lineage, Direct))
COMPUTED = next(m for m, x in REGISTRY.items() if isinstance(x.lineage, Computed))


def write_partition(root, name, sch, rows):
    part = root / name / "year=2026"
    part.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=sch), part / "part.parquet")


def check(metric, run="r1", at=datetime(2026, 11, 2, 20), cid="c.rule", passed=False, severity="warning",
          known="", sample=None):
    return {"run_id": run, "checked_at": at, "metric_id": metric, "check_id": cid, "category": "x",
            "scope_start": date(2026, 11, 1), "scope_end": date(2026, 11, 2), "passed": passed,
            "severity": severity, "known_issue": known, "violations": 0 if passed else 3,
            "checked_rows": 100, "detail": "样本", "sample_path": sample}


@pytest.fixture
def make(warehouse):
    def build(*, catalog=True) -> TestClient:
        query = DataCatalog(WarehouseQuery(warehouse)) if catalog else None
        return TestClient(create_app(WebappDeps(
            live_kill=LiveKillReader(None, None),
            data_catalog=query,
        )))
    return build


def test_metric_status_classification(warehouse):
    ok, blocking, known, unverified = COVERED[:4]
    write_partition(warehouse.root, "metric_checks", schema.METRIC_CHECKS, [
        check(ok, passed=True), check(blocking, severity="blocking"), check(known, known="上游数据自相矛盾")])
    rows = {r.id: r for r in DataCatalog(WarehouseQuery(warehouse)).rows()}
    assert (rows[ok].status, rows[blocking].status, rows[known].status, rows[unverified].status) == (
        "ok", "failed", "known_issue", "unverified")
    assert "上游数据自相矛盾" in rows[known].issues
    unknown = next((m for m, x in REGISTRY.items() if x.meaning is UNKNOWN), None)
    if unknown:
        assert rows[unknown].status == "unknown" and rows[unknown].meaning is None
    uncovered = next((m for m, x in REGISTRY.items() if isinstance(x.checks, Uncovered)
                      and x.meaning is not UNKNOWN), None)
    if uncovered:
        assert rows[uncovered].status == "uncovered"
    order = [r.status for r in DataCatalog(WarehouseQuery(warehouse)).rows()]
    rank = ["failed", "unknown", "uncovered", "unverified", "known_issue", "ok"]
    assert order == sorted(order, key=rank.index)


def test_direct_lineage_structured(warehouse):
    rows = {r.id: r for r in DataCatalog(WarehouseQuery(warehouse)).rows()}
    d = rows[DIRECT]
    assert d.direct is not None and d.direct.source_id == REGISTRY[DIRECT].lineage.source_id
    assert d.direct.field == REGISTRY[DIRECT].lineage.field and isinstance(d.direct.multiplier, float)
    c = rows[COMPUTED]
    assert c.direct is None and isinstance(c.roots, list)


def test_unknown_meaning_is_null(warehouse):
    for r in DataCatalog(WarehouseQuery(warehouse)).rows():
        assert r.meaning is None or r.meaning == REGISTRY[r.id].meaning


def test_catalog_list_and_detail_with_evidence(make, warehouse):
    write_partition(warehouse.root, "metric_evidence", schema.METRIC_EVIDENCE, [{
        "metric_id": METRIC, "symbol": "600000.SH", "dt": date(2026, 11, 3), "our_value": "10.1",
        "reference_value": "10.1", "reference_source": "同花顺", "verdict": "一致", "checked_by": "维护者",
        "checked_at": datetime(2026, 11, 4, 9), "note": ""}])
    write_partition(warehouse.root, "metric_checks", schema.METRIC_CHECKS, [
        check(METRIC, sample="artifacts/metric_checks/r1/c.rule.parquet")])
    c = make()
    body = c.get("/api/app/data/catalog").json()
    MetricList.model_validate(body)
    assert body["summary"]["total"] == len(REGISTRY) and body["rows"] and "statuses" not in body
    assert body["summary"]["latest_run"].endswith("+08:00")
    assert sum(body["summary"]["by_kind"].values()) == len(REGISTRY)
    d = c.get(f"/api/app/data/catalog/{METRIC}").json()
    MetricDetail.model_validate(d)
    assert d["evidence"][0]["reference_source"] == "同花顺" and d["evidence"][0]["checked_at"].endswith("+08:00")
    assert d["checks"][0]["sample_file"] == "r1/c.rule.parquet"
    assert d["history"][0]["checked_at"] == "2026-11-02T20:00:00+08:00"
    assert c.get("/api/app/data/catalog/nope").status_code == 404


def test_catalog_filters_validate_and_do_not_grow_cache(make):
    c = make()
    bad = c.get("/api/app/data/catalog", params={"status": "bogus"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_request"
    c.get("/api/app/data/catalog", params={"kind": "direct"})
    for i in range(5):
        assert c.get("/api/app/data/catalog", params={"dataset": f"bogus{i}"}).json()["rows"] == []
    keys = [k for r in c.app.state.webapp_routers if hasattr(r, "caches") for k in r.caches]
    assert "catalog:None:direct:None" in keys and not any("bogus" in k for k in keys)


def test_catalog_list_cached_60s_per_filter(make, monkeypatch):
    calls = {"n": 0}
    orig = DataCatalog.rows

    def counting(self, *a, **kw):
        calls["n"] += 1
        return orig(self, *a, **kw)
    monkeypatch.setattr(DataCatalog, "rows", counting)
    c = make()
    c.get("/api/app/data/catalog")
    first = calls["n"]
    c.get("/api/app/data/catalog")
    assert calls["n"] == first
    c.get("/api/app/data/catalog", params={"kind": "direct"})
    assert calls["n"] > first


def test_catalog_disabled_without_service(make):
    c = make(catalog=False)
    for url in ("/api/app/data/catalog", f"/api/app/data/catalog/{METRIC}"):
        assert c.get(url).json() == {"schema_version": 1, "enabled": False}
