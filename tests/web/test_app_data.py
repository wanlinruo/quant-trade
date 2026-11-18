"""新前端数据组只读接口 /api/app/data/*（#662，设计文档 §8.13、§10）。"""

from __future__ import annotations

from datetime import date, datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from qsys.data import schema
from qsys.data.catalog import DATASETS, REGISTRY
from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.asset_views import DataAssetService
from qsys.web.catalog_views import CatalogService
from qsys.web.config import WebConfig
from qsys.web.views import MonitorService

METRIC = next(iter(REGISTRY))
PHYSICAL = next(d.id for d in DATASETS.values() if d.physical and d.layer.value != "raw")


def write_partition(root, name, sch, rows):
    part = root / name / "year=2026"
    part.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=sch), part / "part.parquet")


def metric_row(run, at, check="c.rule", passed=False, severity="warning", known="", sample=None):
    return {"run_id": run, "checked_at": at, "metric_id": METRIC, "check_id": check, "category": "x",
            "scope_start": date(2026, 11, 1), "scope_end": date(2026, 11, 2), "passed": passed,
            "severity": severity, "known_issue": known, "violations": 0 if passed else 3,
            "checked_rows": 100, "detail": "样本", "sample_path": sample}


@pytest.fixture
def make(warehouse):
    def build(*, assets=True, catalog=True) -> TestClient:
        cfg = WebConfig()
        return TestClient(create_app(
            MonitorService(SessionJournal(warehouse), config=cfg), cfg,
            assets=DataAssetService(warehouse) if assets else None,
            catalog=CatalogService(warehouse) if catalog else None))
    return build


def test_endpoints_disabled_without_services(make):
    c = make(assets=False, catalog=False)
    for url in ("/api/app/data/overview", "/api/app/data/tables", f"/api/app/data/tables/{PHYSICAL}",
                f"/api/app/data/tables/{PHYSICAL}/sample", "/api/app/data/catalog",
                f"/api/app/data/catalog/{METRIC}", "/api/app/data/quality", "/api/app/data/quality/rules/x"):
        r = c.get(url)
        assert r.status_code == 200 and r.json() == {"enabled": False}, url
        assert r.headers["cache-control"] == "no-store"


def test_overview_matrix_gray_without_evidence(make):
    body = make().get("/api/app/data/overview").json()
    assert body["enabled"] and body["tables"]
    for t in body["tables"]:
        assert t["status"] == "gray"
        assert set(t["dimensions"]) == {"freshness", "completeness", "structure", "validity", "lineage"}
        assert set(t["dimensions"].values()) == {"gray"}
    assert body["dimension_labels"]["freshness"] == "时效"
    dom = body["domains"]
    assert dom and set(dom[0]) == {"domain", "label", "tables", "sources", "status_counts"}
    assert sum(d["tables"] for d in dom) == len(body["tables"])


def test_overview_lineage_nodes_cover_datasets(make):
    g = make().get("/api/app/data/overview").json()["lineage"]
    ids = {n["id"] for n in g["nodes"]}
    assert {f"dataset:{d}" for d in DATASETS} <= ids
    node = next(n for n in g["nodes"] if n["id"] == f"dataset:{PHYSICAL}")
    assert node["status"] == "gray"


def test_tables_list_and_detail_and_404(make):
    c = make()
    rows = c.get("/api/app/data/tables").json()["rows"]
    assert PHYSICAL in {r["id"] for r in rows}
    assert all(r["layer"] != "raw" for r in rows)
    assert any(r["layer"] == "raw" for r in c.get("/api/app/data/tables", params={"include_raw": "true"}).json()["rows"])
    d = c.get(f"/api/app/data/tables/{PHYSICAL}").json()
    assert d["enabled"] and d["row"]["dataset"]["id"] == PHYSICAL and d["status_label"] == "灰·缺证据"
    assert c.get("/api/app/data/tables/nope").status_code == 404


def test_table_sample_endpoint(make, warehouse):
    c = make()
    assert c.get(f"/api/app/data/tables/{PHYSICAL}/sample").json()["available"] is False
    (warehouse.root / PHYSICAL).mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"a": [1, None]}), warehouse.root / PHYSICAL / "part.parquet")
    s = c.get(f"/api/app/data/tables/{PHYSICAL}/sample").json()
    assert s["available"] and s["rows"] == [{"a": 1}, {"a": None}]
    assert c.get("/api/app/data/tables/nope/sample").status_code == 404


def test_catalog_list_and_detail_with_evidence(make, warehouse):
    write_partition(warehouse.root, "metric_evidence", schema.METRIC_EVIDENCE, [{
        "metric_id": METRIC, "symbol": "600000.SH", "dt": date(2026, 11, 3), "our_value": "10.1",
        "reference_value": "10.1", "reference_source": "同花顺", "verdict": "一致", "checked_by": "维护者",
        "checked_at": datetime(2026, 11, 4, 9), "note": ""}])
    c = make()
    body = c.get("/api/app/data/catalog").json()
    assert body["enabled"] and body["summary"]["total"] == len(REGISTRY) and body["rows"]
    d = c.get(f"/api/app/data/catalog/{METRIC}").json()
    assert d["evidence"][0]["reference_source"] == "同花顺" and d["evidence"][0]["verdict"] == "一致"
    assert c.get("/api/app/data/catalog/nope").status_code == 404
    assert c.get("/api/app/data/catalog", params={"status": "bogus"}).status_code == 422


def test_quality_endpoint_shapes(make, warehouse):
    write_partition(warehouse.root, "metric_checks", schema.METRIC_CHECKS, [
        metric_row("r1", datetime(2026, 11, 1, 20), passed=True),
        metric_row("r2", datetime(2026, 11, 2, 20), known="上游数据自相矛盾"),
        metric_row("r2", datetime(2026, 11, 2, 20), check="c.other", severity="blocking")])
    body = make().get("/api/app/data/quality").json()
    assert [r["run_id"] for r in body["runs"]] == ["r2", "r1"]
    assert body["runs"][0]["fields"] == {"total": 2, "passed": 0, "blocking": 1, "warning": 1}
    assert body["runs"][0]["tables"] is None
    src = {g["key"]: g["count"] for g in body["warning_sources"]}
    assert src == {"upstream": 1, "registered": 0, "unclassified": 0}
    rules = {r["id"]: r for r in body["rules"]}
    assert "T1.table_contract" in rules
    assert rules["c.other"]["latest_status"] == "blocking" and rules["c.rule"]["latest_status"] == "warning"


def test_rule_detail_has_sample_url(make, warehouse):
    write_partition(warehouse.root, "metric_checks", schema.METRIC_CHECKS, [
        metric_row("r2", datetime(2026, 11, 2, 20), sample="artifacts/metric_checks/r2/c.rule.parquet")])
    c = make()
    d = c.get("/api/app/data/quality/rules/c.rule").json()
    assert d["enabled"] and d["latest"][0]["sample_url"] == "/data/catalog/sample?path=artifacts/metric_checks/r2/c.rule.parquet"
    assert c.get("/api/app/data/quality/rules/nope.never").status_code == 404


def test_list_endpoints_cached_60s(make, monkeypatch):
    calls = {"n": 0}
    orig = DataAssetService.tables

    def counting(self, *a, **kw):
        calls["n"] += 1
        return orig(self, *a, **kw)
    monkeypatch.setattr(DataAssetService, "tables", counting)
    c = make()
    c.get("/api/app/data/tables")
    c.get("/api/app/data/tables")
    assert calls["n"] == 1


def test_still_no_write_endpoints(make):
    c = make()
    methods = {m for r in c.app.routes if str(getattr(r, "path", "")).startswith("/api/app/data")
               for m in getattr(r, "methods", set())}
    assert methods <= {"GET", "HEAD"}
