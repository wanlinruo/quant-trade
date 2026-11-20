"""旧服务端页面到新单页应用的显式永久重定向。"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse


STATIC_REDIRECTS: dict[str, str] = {
    "/": "/app/today",
    "/live": "/app/live",
    "/paper": "/app/paper",
    "/market": "/app/market",
    "/market/limit-up-shadow": "/app/ailu",
    "/market/limit-up-research": "/app/ailu",
    "/market/limit-up-board": "/app/board",
    "/strategies": "/app/strategies",
    "/backtests": "/app/backtest",
    "/research-jobs": "/app/backtest",
    "/data": "/app/dataov",
    "/data/tables": "/app/tables",
    "/data/catalog": "/app/catalog",
    "/data/catalog/sources": "/app/catalog",
    "/data/domains": "/app/dataov",
    "/services": "/app/services",
}

DYNAMIC_REDIRECTS: dict[str, str] = {
    "/paper/{ident}": "/app/paper",
    "/strategies/{ident}": "/app/strategies",
    "/backtests/{name}": "/app/backtest",
    "/research-jobs/{ident}": "/app/backtest",
    "/data/tables/{dataset}": "/app/tables",
    "/data/relations/{upstream}/{downstream}": "/app/dataov",
    "/data/quality/rules/{check_id}": "/app/quality",
    "/data/catalog/{metric_id}": "/app/catalog",
    "/data/domains/{domain}": "/app/dataov",
    "/services/{service_id}": "/app/services",
    "/services/runs/{day}/{run_id}": "/app/services",
    "/s/{session}": "/app/live",
    "/s/{session}/trades": "/app/orders",
    "/s/{session}/health": "/app/recon",
    "/s/{session}/report": "/app/runs",
    "/s/{session}/runs": "/app/runs",
}


def _redirect(target: str) -> Callable[[Request], RedirectResponse]:
    def endpoint(request: Request) -> RedirectResponse:
        query = request.url.query
        location = f"{target}?{query}" if query else target
        return RedirectResponse(location, status_code=308)

    return endpoint


def mount_legacy_redirects(app: FastAPI) -> None:
    """只注册旧 HTML 页面；旧 API 与下载入口保持不存在。"""
    for index, (path, target) in enumerate((*STATIC_REDIRECTS.items(), *DYNAMIC_REDIRECTS.items())):
        app.add_route(
            path,
            _redirect(target),
            methods=["GET", "HEAD"],
            name=f"legacy-redirect-{index}",
        )
