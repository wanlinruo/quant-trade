"""监控台与可选的个人回测工作台。

未启用工作台时监控台仍严格只读；写接口只随显式传入的 WorkbenchStore 注册。
Web 层不持有业务逻辑——数据全部来自 MonitorService。

页面零外部依赖：内联 CSS、服务端渲染 SVG、约 20 行原生 JS。
监控页面最需要的性质是「出事的时候一定打得开」。
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import structlog
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.templating import Jinja2Templates

from qsys.web.backtest_views import BacktestService
from qsys.web.catalog_views import CatalogService, Status
from qsys.web.config import WebConfig
from qsys.web.data_views import DataService
from qsys.web.svg import line_chart, multi_line_chart
from qsys.web.views import MonitorService
from qsys.web.workbench import BacktestQueue, WorkbenchStore
from qsys.strategy.parameters import validate_parameters

log = structlog.get_logger(__name__)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _elapsed(started: str, finished: str = "") -> str:
    if not started:
        return "—"
    try:
        start = datetime.fromisoformat(started)
        end = datetime.fromisoformat(finished) if finished else datetime.now()
        seconds = max(0, int((end - start).total_seconds()))
    except ValueError:
        return "—"
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f"{hours}小时{minutes:02d}分"
    if minutes:
        return f"{minutes}分{seconds:02d}秒"
    return f"{seconds}秒"


def _backtest_rows(backtests: BacktestService, workbench: WorkbenchStore | None) -> list[dict]:
    """Merge queue jobs and published reports into one user-facing history."""
    runs = {run.name: run for run in backtests.runs()}
    rows: list[dict] = []
    seen: set[str] = set()
    for job in workbench.jobs(limit=500) if workbench else []:
        run_name = job.run_name or job.id
        run = runs.get(run_name)
        metrics = backtests.metric_map(run_name) if run else {}
        rows.append({
            "id": job.id,
            "strategy": job.strategy_name,
            "strategy_id": job.strategy_id,
            "start": str(job.config.get("start", "")),
            "end": str(job.config.get("end", "")),
            "created_at": job.created_at,
            "duration": _elapsed(job.started_at, job.finished_at),
            "status": job.status,
            "cost_model": run.cost_model if run else (
                "聚宽兼容" if job.config.get("jq_compat") else "qsys 实盘档位"
            ),
            "return_pct": run.return_pct if run else None,
            "benchmark_pct": (
                metrics.get("benchmark_return") * 100
                if "benchmark_return" in metrics else None
            ),
            "max_drawdown_pct": (
                metrics.get("max_drawdown") * 100
                if "max_drawdown" in metrics else None
            ),
            "has_report": run is not None,
        })
        seen.add(run_name)
    for run_name, run in runs.items():
        if run_name in seen:
            continue
        metrics = backtests.metric_map(run_name)
        rows.append({
            "id": run.name,
            "strategy": run.strategy,
            "strategy_id": "",
            "start": run.start,
            "end": run.end,
            "created_at": run.generated_at,
            "duration": "—",
            "status": "succeeded",
            "cost_model": run.cost_model,
            "return_pct": run.return_pct,
            "benchmark_pct": (
                metrics.get("benchmark_return") * 100
                if "benchmark_return" in metrics else None
            ),
            "max_drawdown_pct": (
                metrics.get("max_drawdown") * 100
                if "max_drawdown" in metrics else None
            ),
            "has_report": True,
        })
    return rows


def create_app(
    service: MonitorService,
    config: WebConfig,
    data: DataService | None = None,
    backtests: BacktestService | None = None,
    catalog: CatalogService | None = None,
    workbench: WorkbenchStore | None = None,
    queue: BacktestQueue | None = None,
    strategy_templates: Path | None = None,
) -> FastAPI:
    app = FastAPI(title=config.title, docs_url=None, redoc_url=None)

    if queue is not None:
        @app.on_event("startup")
        def _start_queue():
            queue.start()

        @app.on_event("shutdown")
        def _stop_queue():
            queue.stop()

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

    @app.get("/data/catalog", response_class=HTMLResponse)
    def catalog_overview(
        request: Request,
        dataset: str | None = None,
        kind: str | None = None,
        status: str | None = None,
    ):
        if catalog is None:
            raise HTTPException(status_code=404, detail="catalog not enabled")
        try:
            selected_status = Status(status) if status else None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="unknown catalog status") from exc
        return TEMPLATES.TemplateResponse(
            request=request,
            name="catalog_overview.html",
            context={
                "title": config.title,
                "session": None,
                "page": "data_catalog",
                "summary": catalog.summary(),
                "rows": catalog.rows(
                    dataset=dataset, kind=kind, status=selected_status
                ),
                "datasets": catalog.datasets(),
                "kinds": catalog.kinds(),
                "statuses": catalog.statuses(),
                "sel_dataset": dataset,
                "sel_kind": kind,
                "sel_status": status,
            },
        )

    @app.get("/data/catalog/sources", response_class=HTMLResponse)
    def catalog_sources(request: Request):
        if catalog is None:
            raise HTTPException(status_code=404, detail="catalog not enabled")
        return TEMPLATES.TemplateResponse(
            request=request,
            name="catalog_sources.html",
            context={
                "title": config.title,
                "session": None,
                "page": "data_catalog",
                "rows": catalog.sources(),
            },
        )

    @app.get("/data/catalog/sample")
    def catalog_sample(path: str):
        if catalog is None:
            raise HTTPException(status_code=404, detail="catalog not enabled")
        found = catalog.sample_bytes(path)
        if found is None:
            raise HTTPException(status_code=404, detail="no such sample")
        body, media_type = found
        return Response(
            content=body,
            media_type=media_type,
            headers={"Content-Disposition": 'attachment; filename="sample.parquet"'},
        )

    @app.get("/data/catalog/{metric_id}", response_class=HTMLResponse)
    def catalog_detail(request: Request, metric_id: str):
        if catalog is None:
            raise HTTPException(status_code=404, detail="catalog not enabled")
        detail = catalog.detail(metric_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"no metric {metric_id}")
        points = [float(row["violations"]) for row in reversed(detail.history)]
        return TEMPLATES.TemplateResponse(
            request=request,
            name="catalog_detail.html",
            context={
                "title": config.title,
                "session": None,
                "page": "data_catalog",
                "d": detail,
                "chart": line_chart(points) if points else "",
            },
        )

    @app.get("/backtests", response_class=HTMLResponse)
    def backtest_index(
        request: Request,
        q: str = "",
        status: str = "",
        sort: str = "created",
        page_number: int = Query(1, alias="page", ge=1),
    ):
        if backtests is None:
            raise HTTPException(status_code=404, detail="backtest console not enabled")
        rows = _backtest_rows(backtests, workbench)
        query = q.strip().casefold()
        if query:
            rows = [row for row in rows if query in (
                f"{row['id']} {row['strategy']} {row['start']} {row['end']}"
            ).casefold()]
        if status == "active":
            rows = [row for row in rows if row["status"] in ("queued", "running")]
        elif status in ("succeeded", "failed", "cancelled", "interrupted"):
            rows = [row for row in rows if row["status"] == status]
        if sort == "return":
            rows.sort(key=lambda row: row["return_pct"] if row["return_pct"] is not None else float("-inf"), reverse=True)
        elif sort == "drawdown":
            rows.sort(key=lambda row: row["max_drawdown_pct"] if row["max_drawdown_pct"] is not None else float("inf"))
        else:
            sort = "created"
            rows.sort(key=lambda row: row["created_at"], reverse=True)
        page_size = 25
        page_count = max(1, (len(rows) + page_size - 1) // page_size)
        page_number = min(page_number, page_count)
        page_rows = rows[(page_number - 1) * page_size:page_number * page_size]
        return TEMPLATES.TemplateResponse(
            request=request, name="backtests.html",
            context={
                "title": config.title, "session": None, "page": "backtests",
                "rows": page_rows, "total": len(rows), "query": q,
                "selected_status": status, "selected_sort": sort,
                "page_number": page_number, "page_count": page_count,
            },
        )

    @app.get("/strategies", response_class=HTMLResponse)
    def strategy_index(request: Request):
        if workbench is None:
            raise HTTPException(status_code=404, detail="workbench not enabled")
        templates = []
        if strategy_templates and strategy_templates.is_dir():
            templates = sorted(p.name for p in strategy_templates.glob("*.py"))
        return TEMPLATES.TemplateResponse(
            request=request, name="strategies.html",
            context={"title": config.title, "session": None, "page": "strategies",
                     "strategies": workbench.strategies(), "templates": templates},
        )

    @app.get("/strategies/{ident}", response_class=HTMLResponse)
    def strategy_editor(request: Request, ident: str):
        if workbench is None:
            raise HTTPException(status_code=404, detail="workbench not enabled")
        strategy = workbench.strategy(ident)
        if strategy is None:
            raise HTTPException(status_code=404, detail="no such strategy")
        return TEMPLATES.TemplateResponse(
            request=request, name="strategy_editor.html",
            context={"title": config.title, "session": None, "page": "strategies",
                     "strategy": strategy, "versions": workbench.versions(ident)},
        )

    @app.post("/api/strategies", status_code=201)
    async def create_strategy(request: Request):
        if workbench is None:
            raise HTTPException(status_code=404, detail="workbench not enabled")
        body = await request.json()
        try:
            strategy = workbench.create_strategy(str(body.get("name", "")),
                                                  str(body.get("source", "")))
        except (ValueError, SyntaxError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"id": strategy.id, "location": f"/strategies/{strategy.id}"}

    @app.put("/api/strategies/{ident}")
    async def save_strategy(ident: str, request: Request):
        if workbench is None:
            raise HTTPException(status_code=404, detail="workbench not enabled")
        body = await request.json()
        try:
            strategy = workbench.save_strategy(ident, str(body.get("name", "")),
                                                str(body.get("source", "")))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, SyntaxError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"id": strategy.id, "updated_at": strategy.updated_at}

    @app.post("/api/strategies/import", status_code=201)
    async def import_strategy(request: Request):
        if workbench is None or strategy_templates is None:
            raise HTTPException(status_code=404, detail="strategy import not enabled")
        body = await request.json()
        name = str(body.get("filename", ""))
        if not name or Path(name).name != name or not name.endswith(".py"):
            raise HTTPException(status_code=422, detail="invalid strategy filename")
        root = strategy_templates.resolve()
        target = (root / name).resolve()
        if root not in target.parents or not target.is_file():
            raise HTTPException(status_code=404, detail="no such strategy template")
        strategy = workbench.import_file(target)
        return {"id": strategy.id, "location": f"/strategies/{strategy.id}"}

    @app.post("/api/backtests", status_code=201)
    async def enqueue_backtest(request: Request):
        if workbench is None:
            raise HTTPException(status_code=404, detail="workbench not enabled")
        body = await request.json()
        strategy = workbench.strategy(str(body.get("strategy_id", "")))
        if strategy is None:
            raise HTTPException(status_code=404, detail="no such strategy")
        try:
            start, end = date.fromisoformat(str(body["start"])), date.fromisoformat(str(body["end"]))
            cash = float(body.get("cash", 100_000))
            if start > end or cash <= 0:
                raise ValueError("日期区间或初始资金无效")
            params = validate_parameters(strategy.parameters, body.get("params", {}))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        job = workbench.enqueue(strategy.id, {
            "start": str(start), "end": str(end), "cash": cash,
            "jq_compat": bool(body.get("jq_compat", False)), "params": params,
        })
        return {"id": job.id, "location": f"/backtests/{job.id}"}

    @app.post("/api/backtests/{ident}/cancel")
    def cancel_backtest(ident: str):
        if workbench is None:
            raise HTTPException(status_code=404, detail="workbench not enabled")
        if not workbench.cancel(ident):
            raise HTTPException(status_code=409, detail="task cannot be cancelled")
        return {"status": "cancelled"}

    @app.get("/api/backtests/{ident}")
    def backtest_status(ident: str):
        if workbench is None:
            raise HTTPException(status_code=404, detail="workbench not enabled")
        job = workbench.job(ident)
        if job is None:
            raise HTTPException(status_code=404, detail="no such backtest job")
        report_name = job.run_name or job.id
        report_ready = backtests is not None and backtests.run(report_name) is not None
        return {
            "id": job.id,
            "status": job.status,
            "progress": job.progress,
            "total": job.total,
            "percent": job.percent,
            "current_session": job.current_session,
            "error": job.error,
            "log": workbench.job_log(job.id, limit=20_000),
            "report_ready": report_ready,
            "location": f"/backtests/{report_name}",
        }

    @app.get("/backtests/{name}", response_class=HTMLResponse)
    def backtest_detail(request: Request, name: str):
        if backtests is None:
            raise HTTPException(status_code=404, detail="backtest console not enabled")
        job = workbench.job(name) if workbench else None
        run = backtests.run(name)
        if run is None and job is not None:
            return TEMPLATES.TemplateResponse(
                request=request, name="backtest_job.html",
                context={"title": config.title, "session": None, "page": "backtests",
                         "job": job, "strategy": workbench.strategy(job.strategy_id),
                         "job_log": workbench.job_log(job.id) if workbench else ""},
            )
        if run is None:
            raise HTTPException(status_code=404, detail=f"no run named {name}")
        points = backtests.equity_points(name)
        benchmark_points = backtests.benchmark_points(name)
        excess_points = []
        if points and len(points) == len(benchmark_points):
            initial = points[0][1]
            excess_points = [initial * strategy_value / benchmark_value
                             for (_, strategy_value), (_, benchmark_value)
                             in zip(points, benchmark_points, strict=True)
                             if benchmark_value]
        metrics = backtests.metric_map(name)
        job = workbench.job(name) if workbench else None
        return TEMPLATES.TemplateResponse(
            request=request, name="backtest_detail.html",
            context={
                "title": config.title, "session": None, "page": "backtests",
                "run": run, "job": job, "metric_map": metrics,
                "files": backtests.files(name),
                "zip_name": f"{run.name}.zip",
                "metrics": backtests.metrics(name),
                "branches": backtests.branch_summary(name),
                "chart": multi_line_chart([
                    ("策略", [v for _, v in points], "#2563eb"),
                    ("沪深300", [v for _, v in benchmark_points], "#94a3b8"),
                    ("超额", excess_points, "#16a34a"),
                ]) if points else "",
                "drawdown_chart": line_chart(backtests.drawdown_points(name), stroke="#dc2626"),
                "months": backtests.rows(name, "monthly_returns.csv"),
                "positions": backtests.rows(name, "daily_positions.csv", limit=100),
                "fills": backtests.rows(name, "fills.csv", limit=100),
                "reject_rows": backtests.rows(name, "rejects.csv", limit=100),
                "run_log": backtests.text(name, "run.log"),
                "strategy_source": backtests.text(name, "strategy.py"),
                "chart_data": backtests.chart_series(name),
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

    if workbench is None:
        # 保留监控台的历史只读契约：不仅请求返回 404，路由表里也不能出现
        # 写方法。既有部署未启用工作台时，攻击面与升级前完全相同。
        app.router.routes[:] = [
            route for route in app.router.routes
            if not getattr(route, "path", "").startswith(("/api/strategies", "/api/backtests"))
        ]
    return app
