"""新前端数据组只读接口 /api/app/data/*（#662，设计文档 §8.13、§10）。

全部 GET、no-store；依赖服务未配置时返回 {"enabled": false}。列表类接口经 60 秒
单飞缓存（多个标签页共用一次计算）。表样本只读最新分区最后一个行组的一批。
本模块没有任何写接口。
"""

from __future__ import annotations

from typing import Any

import polars as pl
from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from qsys.data.catalog import DATASETS
from qsys.data.catalog.checks import CHECKS
from qsys.data.catalog.health import ALL_DIMENSIONS
from qsys.web.asset_views import _CHECK_METADATA, _DIMENSION_LABELS, _STATUS_LABELS
from qsys.web.catalog_views import Status
from qsys.web.data_app_views import (
    BLOCKING,
    lineage_graph,
    quality_runs,
    table_sample,
    warning_sources,
)
from qsys.web.domain_views import domain_label
from qsys.web.read_cache import SingleFlightCache

LIST_TTL_SECONDS = 60.0
NO_STORE = {"Cache-Control": "no-store"}
DISABLED = {"enabled": False}
DIMENSION_LABELS = {str(k): v for k, v in _DIMENSION_LABELS.items()}
STATUS_LABELS = {str(k): v for k, v in _STATUS_LABELS.items()}
_TABLE_RULE_STATUS = {"red": "blocking", "yellow": "warning", "gray": "unknown", "green": "passed"}
_RANK = {"blocking": 0, "warning": 1, "unknown": 2, "passed": 3}


def _json(body: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(jsonable_encoder(body), status_code=status, headers=NO_STORE)


def _rule_rows(metric_checks: pl.DataFrame, dataset_checks: pl.DataFrame) -> list[dict]:
    """规则表：字段级规则取各指标最新一条结果的最差状态；表级规则取各表最新证据的最差状态。"""
    status: dict[str, str] = {}

    def worse(check_id: str, value: str) -> None:
        if check_id not in status or _RANK[value] < _RANK[status[check_id]]:
            status[check_id] = value

    if metric_checks.height:
        latest = metric_checks.sort("checked_at").group_by(["metric_id", "check_id"]).last()
        for r in latest.iter_rows(named=True):
            worse(r["check_id"], "passed" if r["passed"] else
                  "blocking" if r["severity"] == BLOCKING else "warning")
    if dataset_checks.height:
        latest = dataset_checks.sort("checked_at").group_by(["dataset_id", "check_id"]).last()
        for r in latest.iter_rows(named=True):
            worse(r["check_id"], _TABLE_RULE_STATUS.get(str(r["status"]), "unknown"))
    rows = [{"id": c.id, "name": c.description, "category": str(c.category.value),
             "scope": str(c.scope.value), "known_issues": list(c.known_issues),
             "upstream_self_consistency": c.upstream_self_consistency,
             "latest_status": status.get(c.id, "unknown")} for c in CHECKS.values()]
    rows += [{"id": cid, "name": meta[0], "category": "dataset/relation", "scope": "表/关系",
              "known_issues": [], "upstream_self_consistency": False,
              "latest_status": status.get(cid, "unknown")} for cid, meta in _CHECK_METADATA.items()]
    known = {r["id"] for r in rows}
    rows += [{"id": cid, "name": cid, "category": "persisted", "scope": "仅有历史结果",
              "known_issues": [], "upstream_self_consistency": False, "latest_status": st}
             for cid, st in status.items() if cid not in known]
    return sorted(rows, key=lambda r: (_RANK[r["latest_status"]], r["id"]))


def register_app_data(app: FastAPI, *, assets, catalog) -> None:
    caches: dict[str, SingleFlightCache] = {}

    def cached(key: str, fn) -> Any:
        if key not in caches:
            caches[key] = SingleFlightCache(fn, LIST_TTL_SECONDS)
        return caches[key].get()

    def overview() -> dict:
        tables, status_by_id = [], {}
        for ds in DATASETS.values():
            if str(ds.layer) == "raw":
                continue
            a = assets.assessment(ds.id)
            status_by_id[ds.id] = str(a.status)
            tables.append({
                "id": ds.id, "name": ds.name, "layer": str(ds.layer), "domain": str(getattr(ds.domain, "value", "unclassified")),
                "domain_label": domain_label(ds.domain), "status": str(a.status),
                # 没有证据的维度就是“灰·缺证据”，显式列出，不让前端把缺项当正常
                "dimensions": {**{str(d): "gray" for d in ALL_DIMENSIONS},
                               **{str(d): str(s) for d, s in a.dimensions}},
            })
        order = {"red": 0, "yellow": 1, "gray": 2, "green": 3}
        tables.sort(key=lambda t: (order.get(t["status"], 9), t["id"]))
        graph = lineage_graph()
        for n in graph["nodes"]:
            if n["id"].startswith("dataset:"):
                n["status"] = status_by_id.get(n["ref"], "gray")
        domains: dict[str, dict] = {}
        for t in tables:
            d = domains.setdefault(t["domain"], {"domain": t["domain"], "label": t["domain_label"],
                                                 "tables": 0, "sources": set(),
                                                 "status_counts": dict.fromkeys(("red", "yellow", "gray", "green"), 0)})
            d["tables"] += 1
            d["status_counts"][t["status"]] = d["status_counts"].get(t["status"], 0) + 1
            d["sources"].update(DATASETS[t["id"]].sources)
        domain_rows = [{**d, "sources": len(d["sources"])} for d in domains.values()]
        domain_rows.sort(key=lambda d: d["label"])
        return {"enabled": True, "dimension_labels": DIMENSION_LABELS, "status_labels": STATUS_LABELS,
                "tables": tables, "domains": domain_rows, "lineage": graph}

    def table_rows(include_raw: bool) -> dict:
        rows = []
        for r in assets.tables(include_raw=include_raw):
            ds = r.dataset
            rows.append({"id": ds.id, "name": ds.name, "layer": str(ds.layer),
                         "domain_label": domain_label(ds.domain), "status": str(r.status),
                         "status_label": STATUS_LABELS.get(str(r.status)), "purpose": ds.purpose,
                         "grain": ds.grain, "coverage": jsonable_encoder(r.coverage),
                         "coverage_pending": r.coverage_pending, "coverage_failed": r.coverage_failed})
        return {"enabled": True, "rows": rows}

    def quality() -> dict:
        m, d = assets.query.metric_checks(), assets.query.dataset_checks()
        return {"enabled": True, "runs": quality_runs(m, d), "warning_sources": warning_sources(m),
                "rules": _rule_rows(m, d)}

    @app.get("/api/app/data/overview")
    def data_overview():
        return _json(DISABLED) if assets is None else _json(cached("overview", overview))

    @app.get("/api/app/data/tables")
    def data_tables(include_raw: bool = False):
        if assets is None:
            return _json(DISABLED)
        return _json(cached(f"tables:{include_raw}", lambda: table_rows(include_raw)))

    @app.get("/api/app/data/tables/{dataset_id}")
    def data_table(dataset_id: str):
        if assets is None:
            return _json(DISABLED)
        detail = assets.table(dataset_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="数据表不存在")
        body = jsonable_encoder(detail)
        body.update(enabled=True, status_label=STATUS_LABELS.get(str(detail.row.status)),
                    dimension_labels=DIMENSION_LABELS)
        return _json(body)

    @app.get("/api/app/data/tables/{dataset_id}/sample")
    def data_table_sample(dataset_id: str):
        if assets is None:
            return _json(DISABLED)
        ds = DATASETS.get(dataset_id)
        if ds is None:
            raise HTTPException(status_code=404, detail="数据表不存在")
        try:
            body = table_sample(assets.root, ds)
        except Exception as exc:  # 文件损坏等：如实说明不可抽样，不回显路径
            body = {"available": False, "message": f"抽样失败：{type(exc).__name__}", "columns": [], "rows": []}
        return _json({"enabled": True, **body})

    @app.get("/api/app/data/catalog")
    def data_catalog(dataset: str | None = None, kind: str | None = None, status: str | None = None):
        if catalog is None:
            return _json(DISABLED)
        try:
            sel = Status(status) if status else None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="未知的指标状态") from exc
        rows = [{**jsonable_encoder(r), "label": r.label} for r in catalog.rows(dataset=dataset, kind=kind, status=sel)]
        return _json({"enabled": True, "summary": catalog.summary(), "rows": rows,
                      "datasets": catalog.datasets(), "kinds": catalog.kinds(), "statuses": catalog.statuses()})

    @app.get("/api/app/data/catalog/{metric_id}")
    def data_metric(metric_id: str):
        if catalog is None:
            return _json(DISABLED)
        d = catalog.detail(metric_id)
        if d is None:
            raise HTTPException(status_code=404, detail="指标不存在")
        body = jsonable_encoder(d)
        body["row"]["label"] = d.row.label
        body["enabled"] = True
        return _json(body)

    @app.get("/api/app/data/quality")
    def data_quality():
        return _json(DISABLED) if assets is None else _json(cached("quality", quality))

    @app.get("/api/app/data/quality/rules/{check_id}")
    def data_rule(check_id: str):
        if assets is None:
            return _json(DISABLED)
        d = assets.rule(check_id)
        if d is None:
            raise HTTPException(status_code=404, detail="规则不存在")
        body = jsonable_encoder(d)
        for row in body["latest"]:
            if row.get("sample_path"):
                row["sample_url"] = f"/data/catalog/sample?path={row['sample_path']}"
        return _json({"enabled": True, **body})
