"""数据资产查询（#662 R4）：总览、表清单、表详情、样本；只输出代码与事实。"""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from qsys.data.catalog import DATASETS
from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.asset_views import DataAssetService
from qsys.web.catalog_views import CatalogService
from qsys.web.config import WebConfig
from qsys.web.views import MonitorService
from qsys.webapp.contracts.data import DataOverview, DataTableDetail, DataTableList, TableSample
from qsys.webapp.queries import data_assets as mod
from qsys.webapp.queries.data_assets import DataAssets

PHYSICAL = next(d.id for d in DATASETS.values() if d.physical and d.layer.value != "raw")
DIMS = {"freshness", "completeness", "structure", "validity", "lineage"}


@pytest.fixture
def make(warehouse):
    def build(*, assets=True, catalog=True) -> TestClient:
        cfg = WebConfig()
        return TestClient(create_app(
            MonitorService(SessionJournal(warehouse), config=cfg), cfg,
            assets=DataAssetService(warehouse) if assets else None,
            catalog=CatalogService(warehouse) if catalog else None))
    return build


def test_missing_evidence_is_gray_everywhere(make):
    body = make().get("/api/app/data/overview").json()
    DataOverview.model_validate(body)
    assert body["tables"]
    for t in body["tables"]:
        assert t["status"] == "gray" and set(t["dimensions"]) == DIMS and set(t["dimensions"].values()) == {"gray"}
    dom = body["domains"]
    assert dom and sum(d["tables"] for d in dom) == len(body["tables"])
    assert set(dom[0]["domain"]) == {"code", "unclassified_reason"}
    node = next(n for n in body["lineage"]["nodes"] if n["id"] == f"dataset:{PHYSICAL}")
    assert node["status"] == "gray"
    assert {f"dataset:{d}" for d in DATASETS} <= {n["id"] for n in body["lineage"]["nodes"]}


def test_overview_has_no_display_labels(make):
    text = make().get("/api/app/data/overview").text
    for word in ("红·阻断", "灰·缺证据", "时效", "dimension_labels", "status_labels", "基础 Reference"):
        assert word not in text


def test_tables_list_and_detail_and_404(make):
    c = make()
    body = c.get("/api/app/data/tables").json()
    DataTableList.model_validate(body)
    rows = body["rows"]
    assert PHYSICAL in {r["id"] for r in rows} and all(r["layer"] != "raw" for r in rows)
    raw = c.get("/api/app/data/tables", params={"include_raw": "true"}).json()["rows"]
    assert any(r["layer"] == "raw" for r in raw)
    assert c.get("/api/app/data/tables/nope").status_code == 404


def test_table_detail_dimensions_complete_and_sources_structured(make):
    d = make().get(f"/api/app/data/tables/{PHYSICAL}").json()
    DataTableDetail.model_validate(d)
    assert d["row"]["id"] == PHYSICAL and d["contract"]["id"] == PHYSICAL and d["row"]["status"] == "gray"
    assert set(d["dimensions"]) == DIMS and set(d["dimensions"].values()) == {"gray"}
    assert "status_label" not in d and "domain_label" not in d
    for ref in d["sources"] + d["root_sources"]:
        assert ref["kind"] in ("source", "table", "note")
        if ref["kind"] == "source":
            assert ref["provider"] and ref["api"] and ref["status"] in ("active", "frozen", "dead")
        else:
            assert ref["provider"] is None and ref["status"] is None
    assert all(set(f) == {"name", "type", "meaning"} for f in d["fields"])


def test_sample_reason_codes(make, warehouse):
    c = make()
    s = c.get(f"/api/app/data/tables/{PHYSICAL}/sample").json()
    TableSample.model_validate(s)
    assert s["available"] is False and s["reason"] == "no_file"
    (warehouse.root / PHYSICAL).mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"a": [1, None]}), warehouse.root / PHYSICAL / "part.parquet")
    s = c.get(f"/api/app/data/tables/{PHYSICAL}/sample").json()
    assert s["available"] and s["reason"] == "ok" and s["rows"] == [{"a": 1}, {"a": None}]
    (warehouse.root / PHYSICAL / "part.parquet").write_bytes(b"not parquet")
    s = c.get(f"/api/app/data/tables/{PHYSICAL}/sample").json()
    assert s["available"] is False and s["reason"] == "read_failed"
    logical = next((d.id for d in DATASETS.values() if not d.physical), None)
    if logical:
        assert c.get(f"/api/app/data/tables/{logical}/sample").json()["reason"] == "logical_view"
    assert c.get("/api/app/data/tables/nope/sample").status_code == 404


def test_tables_coverage_pending_and_failed(warehouse, monkeypatch):
    monkeypatch.setattr(mod, "cached_coverage", lambda wh: ([], frozenset({PHYSICAL})))
    monkeypatch.setattr(mod, "previous_coverage", lambda wh, pending: [])

    def boom(wh):
        raise RuntimeError("统计失败")
    monkeypatch.setattr(mod, "coverage", boom)
    a = DataAssets(warehouse, background_coverage=True)
    first = {r.id: r for r in a.tables(False).rows}[PHYSICAL]
    assert first.coverage is None and first.coverage_state == "pending"
    a._coverage_thread.join(5)
    second = {r.id: r for r in a.tables(False).rows}[PHYSICAL]
    assert second.coverage is None and second.coverage_state == "failed"


def test_list_endpoints_cached_60s(make, monkeypatch):
    calls = {"n": 0}
    orig = DataAssets.tables

    def counting(self, *a, **kw):
        calls["n"] += 1
        return orig(self, *a, **kw)
    monkeypatch.setattr(DataAssets, "tables", counting)
    c = make()
    c.get("/api/app/data/tables")
    c.get("/api/app/data/tables")
    assert calls["n"] == 1


def test_asset_endpoints_disabled_without_services(make):
    c = make(assets=False, catalog=False)
    for url in ("/api/app/data/overview", "/api/app/data/tables", f"/api/app/data/tables/{PHYSICAL}",
                f"/api/app/data/tables/{PHYSICAL}/sample"):
        r = c.get(url)
        assert r.status_code == 200 and r.json() == {"schema_version": 1, "enabled": False}, url


def test_pending_coverage_keeps_previous_result(warehouse, monkeypatch):
    from qsys.data.coverage import DatasetCoverage
    prev = DatasetCoverage(dataset=PHYSICAL, rows=5, symbols=2, first_dt=None, last_dt=None)
    monkeypatch.setattr(mod, "cached_coverage", lambda wh: ([], frozenset({PHYSICAL})))
    monkeypatch.setattr(mod, "previous_coverage", lambda wh, pending: [prev])
    monkeypatch.setattr(mod, "coverage", lambda wh: [])
    row = {r.id: r for r in DataAssets(warehouse, background_coverage=True).tables(False).rows}[PHYSICAL]
    assert row.coverage_state == "pending" and row.coverage is not None and row.coverage.rows == 5
