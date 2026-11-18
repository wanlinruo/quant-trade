from __future__ import annotations

from fastapi import APIRouter

from qsys.webapp.contracts.market import MarketDisabled, MarketOverview
from qsys.webapp.queries.market import MarketQuery


def market_router(query: MarketQuery | None) -> APIRouter:
    from qsys.webapp.app import no_store

    router = APIRouter()

    @router.get("/api/app/market", response_model=MarketOverview | MarketDisabled)
    def market():
        return no_store(MarketDisabled() if query is None else query.overview())

    return router
