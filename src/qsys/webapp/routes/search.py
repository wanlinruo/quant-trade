from __future__ import annotations

from fastapi import APIRouter

from qsys.webapp.contracts.search import SearchIndex
from qsys.webapp.queries.search import SearchQuery


def search_router(query: SearchQuery) -> APIRouter:
    from qsys.webapp.app import no_store

    router = APIRouter()

    @router.get("/api/app/search", response_model=SearchIndex)
    def search_index():
        return no_store(query.get())

    return router
