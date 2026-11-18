from __future__ import annotations

from datetime import date

from fastapi import APIRouter

from qsys.webapp.contracts.ailu import ResearchBoard, ShadowBoard
from qsys.webapp.queries.ailu import research_board, shadow_board


def ailu_router(shadow, research) -> APIRouter:
    from qsys.webapp.app import no_store

    router = APIRouter()

    @router.get("/api/app/ailu/shadow", response_model=ShadowBoard)
    def ailu_shadow(day: date | None = None):
        return no_store(shadow_board(shadow, day))

    @router.get("/api/app/ailu/research", response_model=ResearchBoard)
    def ailu_research(day: date | None = None):
        return no_store(research_board(research, day))

    return router
