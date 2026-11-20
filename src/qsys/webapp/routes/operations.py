"""运维组三页薄路由。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter

from qsys.webapp.contracts.operations import NodesView, OperationsDisabled, ReleasesView, ServicesView


def operations_router(query) -> APIRouter:
    from qsys.webapp.app import no_store

    router = APIRouter()

    @router.get("/api/app/operations/services", response_model=ServicesView | OperationsDisabled)
    def services(day: date | None = None):
        return no_store(query.services(day) if query else OperationsDisabled())

    @router.get("/api/app/operations/nodes", response_model=NodesView | OperationsDisabled)
    def nodes():
        return no_store(query.nodes() if query else OperationsDisabled())

    @router.get("/api/app/operations/releases", response_model=ReleasesView | OperationsDisabled)
    def releases():
        return no_store(query.releases() if query else OperationsDisabled())

    return router
