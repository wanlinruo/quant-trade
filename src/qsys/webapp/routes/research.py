"""研究侧薄路由（#662 R3）：读接口总是注册（未启用时返回 enabled=false）；
写接口只在对应服务启用时注册，未启用研究侧的部署保持完全只读。"""

from __future__ import annotations

from fastapi import APIRouter

from qsys.webapp.contracts.research import (
    BacktestList, BacktestPending, BacktestReport, ResearchDisabled, ResearchJobList, StrategyDetail,
    StrategyList,
)
from qsys.webapp.queries.research import ResearchQuery


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

    return router
