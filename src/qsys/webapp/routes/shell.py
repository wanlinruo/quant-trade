from __future__ import annotations

from fastapi import APIRouter

from qsys.webapp.contracts.shell import ShellStatus
from qsys.webapp.queries.shell import ShellQuery


def shell_router(query: ShellQuery) -> APIRouter:
    from qsys.webapp.app import no_store

    router = APIRouter()

    @router.get("/api/app/shell/status", response_model=ShellStatus)
    def shell_status():
        return no_store(query.get())

    return router
