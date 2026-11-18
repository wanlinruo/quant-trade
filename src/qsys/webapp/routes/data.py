"""数据组薄路由（#662 R4）：全部只读 GET；服务未配置时返回 enabled=false。
列表类接口经 60 秒单飞缓存，缓存键只收合法筛选值。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.exceptions import RequestValidationError

from qsys.webapp.cache import SingleFlightCache
from qsys.webapp.contracts.data import (
    DataDisabled, DataOverview, DataTableDetail, DataTableList, MetricDetail, MetricList, QualitySummary, RuleDetail,
    TableSample,
)
from qsys.webapp.queries.data_catalog import STATUSES

LIST_TTL_SECONDS = 60.0


def data_router(assets, catalog=None, quality=None) -> APIRouter:
    from qsys.webapp.app import no_store

    router = APIRouter()
    caches: dict[str, SingleFlightCache] = {}
    router.caches = caches  # type: ignore[attr-defined]  # 供测试检查缓存键

    def cached(key: str, fn):
        if key not in caches:
            caches[key] = SingleFlightCache(fn, LIST_TTL_SECONDS)
        return caches[key].get()

    @router.get("/api/app/data/overview", response_model=DataOverview | DataDisabled)
    def overview():
        return no_store(DataDisabled() if assets is None else cached("overview", assets.overview))

    @router.get("/api/app/data/tables", response_model=DataTableList | DataDisabled)
    def tables(include_raw: bool = False):
        if assets is None:
            return no_store(DataDisabled())
        return no_store(cached(f"tables:{include_raw}", lambda: assets.tables(include_raw)))

    @router.get("/api/app/data/tables/{dataset_id}", response_model=DataTableDetail | DataDisabled)
    def table(dataset_id: str):
        if assets is None:
            return no_store(DataDisabled())
        d = assets.table(dataset_id)
        if d is None:
            raise HTTPException(status_code=404, detail="数据表不存在")
        return no_store(d)

    @router.get("/api/app/data/tables/{dataset_id}/sample", response_model=TableSample | DataDisabled)
    def sample(dataset_id: str):
        if assets is None:
            return no_store(DataDisabled())
        s = assets.sample(dataset_id)
        if s is None:
            raise HTTPException(status_code=404, detail="数据表不存在")
        return no_store(s)

    @router.get("/api/app/data/catalog", response_model=MetricList | DataDisabled)
    def catalog_list(dataset: str | None = None, kind: str | None = None, status: str | None = None):
        if catalog is None:
            return no_store(DataDisabled())
        if status is not None and status not in STATUSES:
            raise RequestValidationError([{"loc": ("query", "status"), "msg": "未知的指标状态",
                                           "type": "value_error"}])
        build = lambda: catalog.list(dataset, kind, status)  # noqa: E731
        # 只为合法筛选值建缓存，任意字符串不会让缓存无限增长
        if (dataset is None or dataset in catalog.datasets()) and (kind is None or kind in catalog.kinds()):
            return no_store(cached(f"catalog:{dataset}:{kind}:{status}", build))
        return no_store(build())

    @router.get("/api/app/data/catalog/{metric_id}", response_model=MetricDetail | DataDisabled)
    def catalog_detail(metric_id: str):
        if catalog is None:
            return no_store(DataDisabled())
        d = catalog.detail(metric_id)
        if d is None:
            raise HTTPException(status_code=404, detail="指标不存在")
        return no_store(d)

    @router.get("/api/app/data/quality", response_model=QualitySummary | DataDisabled)
    def quality_summary():
        return no_store(DataDisabled() if quality is None else cached("quality", quality.summary))

    @router.get("/api/app/data/quality/rules/{check_id}", response_model=RuleDetail | DataDisabled)
    def quality_rule(check_id: str):
        if quality is None:
            return no_store(DataDisabled())
        d = quality.rule(check_id)
        if d is None:
            raise HTTPException(status_code=404, detail="规则不存在")
        return no_store(d)

    @router.get("/api/app/data/quality/samples/{run_id}/{filename}")
    def quality_sample(run_id: str, filename: str):
        if quality is None:
            raise HTTPException(status_code=404, detail="文件不存在")
        return quality.sample(run_id, filename)

    return router
