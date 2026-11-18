from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Query

from qsys.webapp.contracts.board import Board, BoardSeries, BoardUnavailable
from qsys.webapp.queries.board import BoardQuery

SYMBOL = r"^[0-9A-Z]{1,10}\.(SH|SZ|BJ)$"


def board_router(query: BoardQuery) -> APIRouter:
    from qsys.webapp.app import no_store

    router = APIRouter()

    @router.get("/api/app/board", response_model=Board | BoardUnavailable)
    def board(day: date | None = None, at: datetime | None = None):
        return no_store(query.board(day, at))

    @router.get("/api/app/board/series", response_model=BoardSeries)
    def board_series(day: date, symbol: Annotated[str, Query(pattern=SYMBOL)]):
        return no_store(query.series(day, symbol))

    return router
