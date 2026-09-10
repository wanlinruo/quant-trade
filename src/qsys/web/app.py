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
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.templating import Jinja2Templates

from qsys.web.backtest_views import BacktestService
from qsys.web.config import WebConfig
from qsys.web.data_views import DataService
from qsys.web.svg import line_chart
from qsys.web.views import MonitorService

log = structlog.get_logger(__name__)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(
    service: MonitorService,
    config: WebConfig,
    data: DataService | None = None,
    backtests: BacktestService | None = None,
) -> FastAPI:
    app = FastAPI(title=config.title, docs_url=None, redoc_url=None)

    def _require_session(session: date) -> None:
        if not service.has_session(session):
            raise HTTPException(status_code=404, detail=f"no data for {session}")

    def _page(request: Request, template: str, session: date, page: str, **ctx):
        return TEMPLATES.TemplateResponse(
            request=request, name=template,
            context={"title": config.title, "session": session, "page": page, **ctx},
        )

    @app.get("/data", response_class=HTMLResponse)
    def data_overview(request: Request):
        if data is None:
            raise HTTPException(status_code=404, detail="data console not enabled")
        rows, symbols = data.warehouse_totals()
        return TEMPLATES.TemplateResponse(
            request=request, name="data_overview.html",
            context={
                "title": config.title, "session": None, "page": "data",
                "coverage": data.coverage(), "total_rows": rows,
                "total_symbols": symbols, "latest": data.latest_session(),
            },
        )

    @app.get("/data/selection", response_class=HTMLResponse)
    def data_selection(request: Request, tag: str | None = None):
        if data is None:
            raise HTTPException(status_code=404, detail="data console not enabled")
        summary = data.selection_summary()
        rate = f"{summary.allowed / summary.total * 100:.0f}%" if summary.total else "—"
        return TEMPLATES.TemplateResponse(
            request=request, name="data_selection.html",
            context={
                "title": config.title, "session": None, "page": "data_selection",
                "summary": summary, "pass_rate": rate, "tag": tag,
                "tags": data.selection_tags(),
                "rows": data.selections(tag=tag),
            },
        )

    @app.get("/data/regime", response_class=HTMLResponse)
    def data_regime(request: Request):
        if data is None:
            raise HTTPException(status_code=404, detail="data console not enabled")
        limit_up = data.regime_series("limit_up_count")
        slope = data.regime_series("index_ma20_slope_5d")
        return TEMPLATES.TemplateResponse(
            request=request, name="data_regime.html",
            context={
                "title": config.title, "session": None, "page": "data_regime",
                "rows": data.regime().sort("dt", descending=True).to_dicts(),
                "limit_up": limit_up,
                "limit_up_chart": line_chart(limit_up, stroke="#dc2626"),
                "slope_chart": line_chart(slope),
            },
        )

    @app.get("/backtests", response_class=HTMLResponse)
    def backtest_index(request: Request):
        if backtests is None:
            raise HTTPException(status_code=404, detail="backtest console not enabled")
        return TEMPLATES.TemplateResponse(
            request=request, name="backtests.html",
            context={
                "title": config.title, "session": None, "page": "backtests",
                "runs": backtests.runs(),
            },
        )

    @app.get("/backtests/{name}", response_class=HTMLResponse)
    def backtest_detail(request: Request, name: str):
        if backtests is None:
            raise HTTPException(status_code=404, detail="backtest console not enabled")
        run = backtests.run(name)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no run named {name}")
        points = backtests.equity_points(name)
        return TEMPLATES.TemplateResponse(
            request=request, name="backtest_detail.html",
            context={
                "title": config.title, "session": None, "page": "backtests",
                "run": run,
                "files": backtests.files(name),
                "zip_name": f"{run.name}.zip",
                "metrics": backtests.metrics(name),
                "branches": backtests.branch_summary(name),
                "chart": line_chart([v for _, v in points]) if points else "",
            },
        )

    @app.get("/backtests/{name}/file/{filename}")
    def backtest_file(name: str, filename: str):
        if backtests is None:
            raise HTTPException(status_code=404, detail="backtest console not enabled")
        found = backtests.file_bytes(name, filename)
        if found is None:
            raise HTTPException(status_code=404, detail="no such file")
        body, media = found
        return Response(
            content=body, media_type=media,
            # 让浏览器直接下载而不是内联渲染——这些文件是拿去对账的
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
                "<p>实盘或影子会话尚未写入任何记录。</p>"
                "<p><a href='/data'>查看数据台 →</a></p></body>"
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
