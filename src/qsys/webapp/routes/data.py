"""数据组薄路由（#662 R4）：全部只读 GET；服务未配置时返回 enabled=false。
列表类接口经 60 秒单飞缓存，缓存键只收合法筛选值。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from qsys.webapp.cache import SingleFlightCache
from qsys.webapp.contracts.data import (
    DataDisabled, DataOverview, DataTableDetail, DataTableList, TableSample,
)

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

    return router
