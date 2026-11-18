from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException

from qsys.webapp.contracts.alerts import AlertCenter, AlertDetail, AlertsDisabled
from qsys.webapp.queries.alerts import AlertCenterQuery


def alerts_router(query: AlertCenterQuery | None) -> APIRouter:
    from qsys.webapp.app import no_store

    router = APIRouter()

    @router.get("/api/app/alerts", response_model=AlertCenter | AlertsDisabled)
    def center(day: date | None = None):
        return no_store(AlertsDisabled() if query is None else query.center(day))

    @router.get("/api/app/alerts/detail", response_model=AlertDetail)
    def detail(key: str, subject: str = "", day: date | None = None):
        d = None if query is None else query.detail(key, subject, day)
        if d is None:
            raise HTTPException(status_code=404, detail="告警不存在")
        return no_store(d)

    return router
