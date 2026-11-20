from __future__ import annotations

from datetime import date

from fastapi import APIRouter

from qsys.webapp.contracts.today import TodayDisabled, TodayView


def today_router(query) -> APIRouter:
    from qsys.webapp.app import no_store
    router = APIRouter()

    @router.get("/api/app/today", response_model=TodayView | TodayDisabled)
    def today(day: date | None = None):
        return no_store(query.overview(day) if query else TodayDisabled())
    return router
