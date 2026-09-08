"""只读监控台。

一期 Web 只读：不提供任何写接口（启停策略、改参数属四期）。
Web 层不持有业务逻辑——数据全部来自 MonitorService。

页面零外部依赖：内联 CSS、服务端渲染 SVG、约 20 行原生 JS。
监控页面最需要的性质是「出事的时候一定打得开」。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from qsys.web.config import WebConfig
from qsys.web.svg import line_chart
from qsys.web.views import MonitorService

log = structlog.get_logger(__name__)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(service: MonitorService, config: WebConfig) -> FastAPI:
    app = FastAPI(title=config.title, docs_url=None, redoc_url=None)

    def _require_session(session: date) -> None:
        if not service.has_session(session):
            raise HTTPException(status_code=404, detail=f"no data for {session}")

    def _page(request: Request, template: str, session: date, page: str, **ctx):
        return TEMPLATES.TemplateResponse(
            request=request, name=template,
            context={"title": config.title, "session": session, "page": page, **ctx},
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def root(request: Request):
        latest = service.latest_session()
        if latest is None:
            return HTMLResponse(
                "<!doctype html><meta charset='utf-8'>"
                f"<title>{config.title}</title>"
                "<body style='font-family:sans-serif;padding:40px;color:#475569'>"
                "<h1 style='font-size:18px'>暂无会话数据</h1>"
                "<p>实盘或影子会话尚未写入任何记录。</p></body>"
            )
        return RedirectResponse(f"/s/{latest}")

    @app.get("/s/{session}", response_class=HTMLResponse)
    def overview(request: Request, session: date):
        _require_session(session)
        view = service.overview(session)
        return _page(request, "overview.html", session, "overview",
                     view=view, chart=line_chart(view.equity_curve))

    @app.get("/s/{session}/trades", response_class=HTMLResponse)
    def trades(request: Request, session: date):
        _require_session(session)
        return _page(request, "trades.html", session, "trades",
                     view=service.trades(session))

    @app.get("/s/{session}/health", response_class=HTMLResponse)
    def health(request: Request, session: date):
        _require_session(session)
        return _page(request, "health.html", session, "health",
                     view=service.health(session))

    @app.get("/s/{session}/report", response_class=HTMLResponse)
    def report(request: Request, session: date):
        _require_session(session)
        return _page(request, "report.html", session, "report",
                     report_text=service.report(session).as_text())

    @app.get("/s/{session}/report.txt", response_class=PlainTextResponse)
    def report_text(session: date) -> str:
        _require_session(session)
        return service.report(session).as_text()

    @app.get("/fragment/{session}/status", response_class=HTMLResponse)
    def status_fragment(session: date) -> str:
        _require_session(session)
        view = service.overview(session)
        css = "ok" if view.is_healthy else "bad"
        label = "正常" if view.is_healthy else "异常"
        return f'<span class="pill {css}">{label}</span>'

    return app
