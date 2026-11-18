"""研究侧薄路由（#662 R3）：读接口总是注册（未启用时返回 enabled=false）；
写接口只在对应服务启用时注册，未启用研究侧的部署保持完全只读。"""

from __future__ import annotations

from fastapi import APIRouter

from qsys.webapp.contracts.research import (
    BacktestList, BacktestPending, BacktestReport, BacktestRequest, Cancelled, Created, ResearchDisabled,
    ResearchJobList, ResearchJobRequest, Resubmitted, StrategyDetail, StrategyInput, StrategyList,
    TemplateImport,
)
from qsys.webapp.queries.research import ResearchQuery, ResearchWrites


def research_router(query: ResearchQuery) -> APIRouter:
    from qsys.webapp.app import no_store

    router = APIRouter()

    @router.get("/api/app/strategies", response_model=StrategyList | ResearchDisabled)
    def strategies():
        return no_store(query.strategies())

    @router.get("/api/app/strategies/{ident}", response_model=StrategyDetail | ResearchDisabled)
    def strategy(ident: str):
        return no_store(query.strategy(ident))

    @router.get("/api/app/backtests", response_model=BacktestList | ResearchDisabled)
    def backtests():
        return no_store(query.backtests())

    @router.get("/api/app/backtests/{name}",
                response_model=BacktestReport | BacktestPending | ResearchDisabled)
    def backtest(name: str):
        return no_store(query.backtest(name))

    @router.get("/api/app/backtests/{name}/files/{filename}")
    def backtest_file(name: str, filename: str):
        return query.backtest_file(name, filename)

    @router.get("/api/app/research-jobs", response_model=ResearchJobList | ResearchDisabled)
    def research_jobs():
        return no_store(query.research())

    @router.get("/api/app/research-jobs/{ident}/files/{path:path}")
    def research_artifact(ident: str, path: str):
        return query.research_artifact(ident, path)

    w = ResearchWrites(query)
    _writes(router, w, no_store)
    return router


def _writes(router: APIRouter, w: ResearchWrites, no_store) -> None:
    from fastapi.responses import JSONResponse

    def created(model) -> JSONResponse:
        r = no_store(model)
        r.status_code = 201
        return r

    q = w.q
    if q.workbench is not None:
        @router.post("/api/app/strategies", status_code=201, response_model=Created)
        def create_strategy(body: StrategyInput):
            return created(w.create_strategy(body))

        @router.put("/api/app/strategies/{ident}", response_model=Created)
        def save_strategy(ident: str, body: StrategyInput):
            return no_store(w.save_strategy(ident, body))

        if q.strategy_templates is not None:
            @router.post("/api/app/strategies/import", status_code=201, response_model=Created)
            def import_template(body: TemplateImport):
                return created(w.import_template(body))

        @router.post("/api/app/backtests", status_code=201, response_model=Created)
        def enqueue_backtest(body: BacktestRequest):
            return created(w.enqueue_backtest(body))

        @router.post("/api/app/backtests/{ident}/cancel", response_model=Cancelled)
        def cancel_backtest(ident: str):
            return no_store(w.cancel_backtest(ident))

    if q.research_jobs is not None:
        @router.post("/api/app/research-jobs", status_code=201, response_model=Created)
        def submit_research(body: ResearchJobRequest):
            return created(w.submit_research(body))

        @router.post("/api/app/research-jobs/{ident}/cancel", response_model=Cancelled)
        def cancel_research(ident: str):
            return no_store(w.cancel_research(ident))

        @router.post("/api/app/research-jobs/{ident}/resubmit", status_code=201, response_model=Resubmitted)
        def resubmit_research(ident: str):
            return created(w.resubmit_research(ident))
