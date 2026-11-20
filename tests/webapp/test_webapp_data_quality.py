"""数据质量查询、违规样本下载与数据组路由（#662 R4）。"""

from __future__ import annotations

from datetime import date, datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from qsys.data import schema
from qsys.data.catalog import DATASETS, DEPENDENCIES, REGISTRY
from qsys.data.query import WarehouseQuery
from qsys.webapp.app import WebappDeps, create_app
from qsys.webapp.config import WebConfig
from qsys.webapp.live_kill_status import LiveKillReader
from qsys.webapp.queries.data_quality import DataQuality
from qsys.webapp.contracts.data import QualitySummary, RuleDetail

METRIC = next(iter(REGISTRY))
PHYSICAL = next(d.id for d in DATASETS.values() if d.physical and d.layer.value != "raw")
EDGE = DEPENDENCIES[0]


def write_partition(root, name, sch, rows):
    part = root / name / "year=2026"
    part.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=sch), part / "part.parquet")


def metric_row(run, at, check="c.rule", passed=False, severity="warning", known="", sample=None):
    return {"run_id": run, "checked_at": at, "metric_id": METRIC, "check_id": check, "category": "x",
            "scope_start": date(2026, 11, 1), "scope_end": date(2026, 11, 2), "passed": passed,
            "severity": severity, "known_issue": known, "violations": 0 if passed else 3,
            "checked_rows": 100, "detail": "样本", "sample_path": sample}


def relation_row(run, at, status, check="R1.join_integrity"):
    return {"run_id": run, "checked_at": at, "upstream": EDGE.upstream, "downstream": EDGE.downstream,
            "check_id": check, "dimension": "lineage", "scope_start": date(2026, 11, 1),
            "scope_end": date(2026, 11, 2), "status": status, "evidence_at": at, "expires_at": None,
            "required": True, "upstream_rows": 10, "downstream_rows": 10, "matched_rows": 10,
            "orphan_rows": 0, "violations": 0, "detail": "", "input_version": None}


@pytest.fixture
def make(warehouse, tmp_path):
    samples = tmp_path / "samples"
    samples.mkdir()

    def build(*, assets=True, catalog=True) -> TestClient:
        quality = DataQuality(WarehouseQuery(warehouse), samples) if assets and catalog else None
        return TestClient(create_app(WebappDeps(
            live_kill=LiveKillReader(None, None),
            data_quality=quality,
        )))
    build.samples = samples
    return build


def test_quality_endpoint_shapes(make, warehouse):
    write_partition(warehouse.root, "metric_checks", schema.METRIC_CHECKS, [
        metric_row("r1", datetime(2026, 11, 1, 20), passed=True),
        metric_row("r2", datetime(2026, 11, 2, 20), known="上游数据自相矛盾"),
        metric_row("r2", datetime(2026, 11, 2, 20), check="c.other", severity="blocking")])
    body = make().get("/api/app/data/quality").json()
    QualitySummary.model_validate(body)
    assert [r["run_id"] for r in body["runs"]] == ["r2", "r1"]
    assert body["runs"][0]["checked_at"] == "2026-11-02T20:00:00+08:00"
    assert body["runs"][0]["fields"] == {"total": 2, "passed": 0, "blocking": 1, "warning": 1}
    assert body["runs"][0]["tables"] is None
    src = {g["key"]: g["count"] for g in body["warning_sources"]}
    assert src == {"upstream": 1, "registered": 0, "unclassified": 0}
    assert all("label" not in g for g in body["warning_sources"])
    rules = {r["id"]: r for r in body["rules"]}
    assert "T1.table_contract" in rules
    assert rules["c.other"]["latest_status"] == "blocking" and rules["c.rule"]["latest_status"] == "warning"


def test_relation_rule_latest_status_from_relation_checks(make, warehouse):
    write_partition(warehouse.root, "relation_checks", schema.RELATION_CHECKS, [
        relation_row("r1", datetime(2026, 11, 1, 20), "red"),
        relation_row("r2", datetime(2026, 11, 2, 20), "green"),
        relation_row("r2", datetime(2026, 11, 2, 20), "yellow", check="R2.independent_recompute")])
    rules = {r["id"]: r for r in make().get("/api/app/data/quality").json()["rules"]}
    assert rules["R1.join_integrity"]["latest_status"] == "passed"
    assert rules["R2.independent_recompute"]["latest_status"] == "warning"
    d = make().get("/api/app/data/quality/rules/R1.join_integrity").json()
    RuleDetail.model_validate(d)
    assert len(d["history"]) == 2 and d["latest"][0]["status"] == "green"
    assert d["latest"][0]["upstream"] == EDGE.upstream


def test_sample_file_shape_and_download_guard(make, warehouse):
    (make.samples / "r2").mkdir()
    pq.write_table(pa.table({"symbol": ["600000.SH"]}), make.samples / "r2" / "c.rule.parquet")
    write_partition(warehouse.root, "metric_checks", schema.METRIC_CHECKS, [
        metric_row("r2", datetime(2026, 11, 2, 20), sample="artifacts/metric_checks/r2/c.rule.parquet"),
        metric_row("r2", datetime(2026, 11, 2, 20), check="c.odd", sample="/elsewhere/x.parquet")])
    c = make()
    d = c.get("/api/app/data/quality/rules/c.rule").json()
    assert d["latest"][0]["sample_file"] == "r2/c.rule.parquet" and "sample_path" not in d["latest"][0]
    odd = c.get("/api/app/data/quality/rules/c.odd").json()
    assert odd["latest"][0]["sample_file"] is None
    ok = c.get("/api/app/data/quality/samples/r2/c.rule.parquet")
    assert ok.status_code == 200 and ok.content.startswith(b"PAR1")
    for url in ("/api/app/data/quality/samples/r2/missing.parquet", "/api/app/data/quality/samples/..%2F..%2Fx/a.parquet",
                "/api/app/data/quality/samples/r2/..%2F..%2Fsecret.parquet", "/api/app/data/quality/samples/r2/c.rule.csv"):
        assert c.get(url).status_code == 404, url
    assert c.get("/api/app/data/quality/rules/nope.never").status_code == 404


def test_data_responses_validate_against_contracts(make, warehouse):
    write_partition(warehouse.root, "metric_checks", schema.METRIC_CHECKS, [
        metric_row("r1", datetime(2026, 11, 1, 20))])
    c = make()
    QualitySummary.model_validate(c.get("/api/app/data/quality").json())
    RuleDetail.model_validate(c.get("/api/app/data/quality/rules/c.rule").json())
    RuleDetail.model_validate(c.get("/api/app/data/quality/rules/T1.table_contract").json())


def test_data_routes_disabled_and_no_write_methods(make):
    c = make(assets=False, catalog=False)
    for url in ("/api/app/data/quality", "/api/app/data/quality/rules/x"):
        r = c.get(url)
        assert r.status_code == 200 and r.json() == {"schema_version": 1, "enabled": False}, url
    assert c.get("/api/app/data/quality/samples/r/a.parquet").status_code == 404
    from tests.webapp.test_webapp_contracts_generated import _api_routes
    methods = {m for r in _api_routes(make().app.routes) if str(getattr(r, "path", "")).startswith("/api/app/data")
               for m in getattr(r, "methods", set())}
    assert methods and methods <= {"GET", "HEAD"}


def test_monitor_cli_wires_data_queries_with_background_coverage(tmp_path, monkeypatch):
    """生产入口显式构造数据组查询：覆盖统计后台刷新，样本根按运行记录目录定位。"""
    from qsys.live.config import DeployConfig
    import qsys.live.config as live_config
    from qsys.webapp import cli as monitor_cli
    monkeypatch.setattr(live_config, "load_deploy_config", lambda _: DeployConfig())
    captured = {}
    monkeypatch.setattr(monitor_cli, "create_app", lambda *a, **kw: captured.update(kwargs=kw, args=a))
    monitor_cli.build_app(tmp_path / "warehouse", WebConfig(), None, workspace_dir=tmp_path / "workspace",
                          service_runs_dir=tmp_path / "var" / "service-runs")
    deps = captured["args"][0]
    assert deps.data_assets._background is True
    assert deps.data_catalog is not None and deps.data_quality is not None
    assert deps.data_quality._sample_root == (tmp_path / "artifacts/metric_checks").resolve()
