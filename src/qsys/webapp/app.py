"""挂载新后端：错误处理、各领域路由，最后是 /app 单页应用（catch-all）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from qsys.webapp.errors import install_error_handlers
from qsys.webapp.spa import SPA_DIST, mount_spa

NO_STORE = {"Cache-Control": "no-store"}


def no_store(model: BaseModel) -> JSONResponse:
    return JSONResponse(model.model_dump(mode="json"), headers=NO_STORE)


@dataclass
class WebappDeps:
    live_kill: Any
    market: Any | None = None       # queries.market.MarketQuery
    shadow: Any | None = None       # 影子榜单读取器
    research: Any | None = None     # 研究回测读取器
    board: Any | None = None        # queries.board_reader.LimitUpBoardReader
    # 研究侧（R3）
    workbench: Any | None = None    # web.workbench.WorkbenchStore（合入时搬到 qsys.backtest）
    runs: Any | None = None         # queries.backtest_runs.BacktestRuns
    paper: Any | None = None        # paper.store.PaperStore
    research_jobs: Any | None = None  # web.research_jobs.ResearchJobConsole
    strategy_templates: Path | None = None
    shadow_lineage: Any | None = None
    core_label: str = "影子盘"
    # 数据组（R4）
    data_assets: Any | None = None   # queries.data_assets.DataAssets
    data_catalog: Any | None = None  # queries.data_catalog.DataCatalog
    spa_root: Path | None = None


def mount_webapp(app: FastAPI, deps: WebappDeps) -> None:
    from qsys.webapp.queries.shell import ShellQuery
    from qsys.webapp.queries.board import BoardQuery
    from qsys.webapp.routes.ailu import ailu_router
    from qsys.webapp.queries.research import ResearchQuery
    from qsys.webapp.routes.board import board_router
    from qsys.webapp.routes.data import data_router
    from qsys.webapp.routes.research import research_router
    from qsys.webapp.routes.market import market_router
    from qsys.webapp.routes.shell import shell_router

    install_error_handlers(app)
    app.state.webapp = deps
    app.include_router(shell_router(ShellQuery(deps.live_kill)))
    app.include_router(market_router(deps.market))
    app.include_router(ailu_router(deps.shadow, deps.research))
    app.include_router(board_router(BoardQuery(deps.board, deps.market)))
    app.include_router(research_router(ResearchQuery(
        workbench=deps.workbench, runs=deps.runs, paper=deps.paper, research_jobs=deps.research_jobs,
        strategy_templates=deps.strategy_templates, shadow_lineage=deps.shadow_lineage,
        core_label=deps.core_label)))
    data = data_router(deps.data_assets, deps.data_catalog)
    app.state.webapp_routers = [data]
    app.include_router(data)
    mount_spa(app, deps.spa_root if deps.spa_root is not None else SPA_DIST)
