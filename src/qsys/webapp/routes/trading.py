"""交易组薄路由（#662 计划 06c）。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from qsys.webapp.contracts.trading import (
    LiveTradingOverview, PaperAccountDetail, PaperAccounts, PaperActionRequest,
    PaperActionResult, PaperNotifyRequest, TradingDisabled, TradingOrders,
    TradingReconciliation, TradingRuns,
)
from qsys.webapp.queries.trading import TradingQuery, TradingWrites, _mask


def trading_router(query: TradingQuery | None, *, activation=None) -> APIRouter:
    from qsys.webapp.app import no_store

    router = APIRouter()

    @router.get("/api/app/trading/live", response_model=LiveTradingOverview | TradingDisabled)
    def live(day: date | None = None):
        return no_store(query.live(day) if query else TradingDisabled())

    @router.get("/api/app/trading/orders", response_model=TradingOrders | TradingDisabled)
    def orders(day: date | None = None):
        return no_store(query.orders(day) if query else TradingDisabled())

    @router.get("/api/app/trading/reconciliation",
                response_model=TradingReconciliation | TradingDisabled)
    def reconciliation(day: date | None = None):
        return no_store(query.reconciliation(day) if query else TradingDisabled())

    @router.get("/api/app/trading/runs", response_model=TradingRuns | TradingDisabled)
    def runs(day: date | None = None):
        return no_store(query.runs(day) if query else TradingDisabled())

    @router.get("/api/app/trading/runs/report.txt", response_class=PlainTextResponse)
    def report_text(day: date | None = None):
        result = query.runs(day) if query else TradingDisabled()
        if not isinstance(result, TradingRuns) or result.report_text is None:
            raise HTTPException(status_code=404, detail="日报不存在")
        return PlainTextResponse(result.report_text, headers={"Cache-Control": "no-store"})

    @router.get("/api/app/trading/paper", response_model=PaperAccounts | TradingDisabled)
    def paper_accounts():
        return no_store(query.paper_accounts() if query else TradingDisabled())

    @router.get("/api/app/trading/paper/{ident}",
                response_model=PaperAccountDetail | TradingDisabled)
    def paper_detail(ident: str, day: date | None = None):
        return no_store(query.paper_detail(ident, day) if query else TradingDisabled())

    if query is not None and query.paper is not None:
        writes = TradingWrites(query, activation=activation)

        @router.post("/api/app/trading/paper/{ident}/action", response_model=PaperActionResult)
        def paper_action(ident: str, body: PaperActionRequest):
            account = writes.action(ident, body.action)
            return no_store(PaperActionResult(id=ident, status=account.status))

        @router.post("/api/app/trading/paper/{ident}/notify", response_model=PaperActionResult)
        def paper_notify(ident: str, body: PaperNotifyRequest):
            account = writes.notify(ident, body.webhook)
            return no_store(PaperActionResult(id=ident, status=account.status,
                                              notify_mask=_mask(str(account.config.get("notify_webhook", "")))))

    return router
