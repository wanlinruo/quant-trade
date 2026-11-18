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
    spa_root: Path | None = None


def mount_webapp(app: FastAPI, deps: WebappDeps) -> None:
    install_error_handlers(app)
    app.state.webapp = deps
    mount_spa(app, deps.spa_root if deps.spa_root is not None else SPA_DIST)
