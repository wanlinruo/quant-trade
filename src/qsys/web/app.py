"""监控台与可选的个人回测工作台。

未启用工作台时监控台仍严格只读；写接口只随显式传入的 WorkbenchStore 注册。
Web 层不持有业务逻辑——数据全部来自 MonitorService。

页面零外部依赖：内联 CSS、服务端渲染 SVG、约 20 行原生 JS。
监控页面最需要的性质是「出事的时候一定打得开」。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
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

from qsys.web.backtest_views import BacktestService, param_rows
from qsys.web.asset_views import DataAssetService
from qsys.web.catalog_views import CatalogService, Status
from qsys.web.config import WebConfig
from qsys.web.data_views import DataService, MarketService
from qsys.web.paper_chart import history_series, intraday_series
from qsys.web.service_views import ServiceConsole
from qsys.web.svg import line_chart, multi_line_chart
from qsys.web.views import MonitorService
from qsys.web.workbench import BacktestQueue, WorkbenchStore
from qsys.backtest.runner import ENTRY_FILL_LABELS, ENTRY_FILL_MODES
from qsys.strategy.parameters import validate_parameters
from qsys.core.calendar import AShareCalendar
from qsys.ops.services import SERVICE_BY_ID
from qsys.ops.strategy_log import PHASE_LABELS
from qsys.paper.calendar import first_complete_session
from qsys.paper.store import PaperStore

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
            "source": "web",
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
            "source": run.source,
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
    paper: PaperStore | None = None,
    market: MarketService | None = None,
    paper_calendar: Callable[[], AShareCalendar] | None = None,
    services: ServiceConsole | None = None,
    assets: DataAssetService | None = None,
    # 影子核心承袭的模拟账户（只读查询；没配承袭时返回 None）
    shadow_lineage: Callable[[], object | None] | None = None,
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
            context={
                "title": config.title,
                "session": session,
                "page": page,
                "phase_labels": PHASE_LABELS,
                **ctx,
            },
        )

    @app.get("/data", response_class=HTMLResponse)
    def data_overview(request: Request):
        if data is None:
            raise HTTPException(status_code=404, detail="data console not enabled")
        rows, symbols = data.warehouse_totals()
        pool_day, pool = data.delisting_pool()
        return TEMPLATES.TemplateResponse(
            request=request, name="data_overview.html",
            context={
                "pool_day": pool_day, "pool": pool.to_dicts() if pool.height else [],
                "title": config.title, "session": None, "page": "data",
                "coverage": data.coverage(), "total_rows": rows,
                "total_symbols": symbols, "latest": data.latest_session(),
                "catalog_summary": catalog.summary() if catalog else None,
            },
        )

    @app.get("/data/tables", response_class=HTMLResponse)
    def data_tables(request: Request, include_raw: bool = False):
        if assets is None:
            raise HTTPException(status_code=404, detail="data assets not enabled")
        return TEMPLATES.TemplateResponse(
            request=request, name="data_tables.html",
            context={
                "title": config.title, "session": None, "page": "data_tables",
                "rows": assets.tables(include_raw=include_raw),
                "include_raw": include_raw,
            },
        )

    @app.get("/data/tables/{dataset}", response_class=HTMLResponse)
    def data_table_detail(request: Request, dataset: str):
        if assets is None:
            raise HTTPException(status_code=404, detail="data assets not enabled")
        detail = assets.table(dataset)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"no dataset {dataset}")
        return TEMPLATES.TemplateResponse(
            request=request, name="data_table_detail.html",
            context={"title": config.title, "session": None,
                     "page": "data_tables", "d": detail},
        )

    @app.get("/data/relations/{upstream}/{downstream}", response_class=HTMLResponse)
    def data_relation_detail(request: Request, upstream: str, downstream: str):
        if assets is None:
            raise HTTPException(status_code=404, detail="data assets not enabled")
        detail = assets.relation(upstream, downstream)
        if detail is None:
            raise HTTPException(status_code=404, detail="no such dataset relation")
        return TEMPLATES.TemplateResponse(
            request=request, name="data_relation_detail.html",
            context={"title": config.title, "session": None,
                     "page": "data_tables", "d": detail},
        )

    @app.get("/data/quality/rules/{check_id}", response_class=HTMLResponse)
    def data_rule_detail(request: Request, check_id: str):
        if assets is None:
            raise HTTPException(status_code=404, detail="data assets not enabled")
        detail = assets.rule(check_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"no rule {check_id}")
        return TEMPLATES.TemplateResponse(
            request=request, name="data_rule_detail.html",
            context={"title": config.title, "session": None,
                     "page": "data_quality", "d": detail},
        )

    def _services() -> ServiceConsole:
        if services is None:
            raise HTTPException(status_code=404, detail="service console not enabled")
        return services

    @app.get("/services", response_class=HTMLResponse)
    def service_overview(request: Request, days: int = Query(14, ge=7, le=60)):
        console = _services()
        columns, rows, has_calendar = console.board(days)
        _, acceptance_rows, _ = console.acceptance_board(days)
        periodic_columns, periodic_rows, _ = console.periodic_board()
        return TEMPLATES.TemplateResponse(
            request=request, name="services.html",
            context={"title": config.title, "session": None, "page": "services",
                     "columns": columns, "rows": rows, "has_calendar": has_calendar,
                     "acceptance_rows": acceptance_rows,
                     "periodic_columns": periodic_columns,
                     "periodic_rows": periodic_rows,
                     "timeline": console.health_timeline(),
                     "processes": console.process_status(),
                     "timers": console.services("timer"),
                     "residents": console.services("resident"),
                     "supervisors": console.services("supervisor")},
        )

    @app.get("/services/runs/{day}/{run_id}", response_class=HTMLResponse)
    def service_run(request: Request, day: date, run_id: str):
        console = _services()
        run = console.run(day, run_id)
        if run is None or run["spec"] is None:
            raise HTTPException(status_code=404, detail="run not found")
        return TEMPLATES.TemplateResponse(
            request=request, name="service_detail.html",
            context={"title": config.title, "session": None, "page": "services_run",
                     "spec": run["spec"], "run": run,
                     "recent": console.recent_runs(run["service"])},
        )

    @app.get("/services/{service_id}", response_class=HTMLResponse)
    def service_detail(request: Request, service_id: str):
        console = _services()
        spec = SERVICE_BY_ID.get(service_id)
        if spec is None:
            raise HTTPException(status_code=404, detail="unknown service")
        return TEMPLATES.TemplateResponse(
            request=request, name="service_detail.html",
            context={"title": config.title, "session": None, "page": "services_detail",
                     "spec": spec, "run": None, "recent": console.recent_runs(service_id),
                     "health": console.service_health(service_id)},
        )

    @app.get("/data/selection")
    def retired_data_selection():
        return RedirectResponse("/backtests", status_code=308)

    @app.get("/data/regime")
    def retired_data_regime():
        return RedirectResponse("/market", status_code=308)

    @app.get("/market", response_class=HTMLResponse)
    def market_overview(request: Request):
        if market is None:
            raise HTTPException(status_code=404, detail="market console not enabled")
        live = market.live()
        schedule = market.schedule()
        rows = market.history()
        advance = market.series("market_advance_ratio")
        limits = market.series("limit_up_count")
        slope = market.series("index_ma20_slope_5d")
        return TEMPLATES.TemplateResponse(
            request=request, name="market.html",
            context={
                "title": config.title, "session": None, "page": "market",
                "live": live, "schedule": schedule, "rows": rows,
                "limit_check": market.limit_check(),
                "latest": market.latest_session(),
                "advance_chart": line_chart(advance),
                "limit_chart": line_chart(limits, stroke="#dc2626"),
                "slope_chart": line_chart(slope),
            },
        )

    @app.get("/api/market/overview")
    def market_snapshot():
        if market is None:
            raise HTTPException(status_code=404, detail="market console not enabled")
        view = market.live()
        schedule = market.schedule()
        return {
            "available": view.available,
            "stale": view.stale,
            "snapshot": (
                view.snapshot.model_dump(mode="json") if view.snapshot else None
            ),
            "market_open": schedule.is_open if schedule else False,
            "next_open_at": (
                schedule.next_open_at.isoformat()
                if schedule and schedule.next_open_at else None
            ),
        }

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
                "page": "data_sources",
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

    @app.get("/paper", response_class=HTMLResponse)
    def paper_index(request: Request, status: str = ""):
        if paper is None:
            raise HTTPException(status_code=404, detail="paper trading not enabled")
        valid = {"scheduled", "running", "paused", "error", "ended"}
        selected = status if status in valid else ""
        rows = []
        for account in paper.accounts(status=selected):
            rows.append({"account": account, **paper.summary(account.id)})
        return TEMPLATES.TemplateResponse(
            request=request, name="paper_accounts.html",
            context={"title": config.title, "session": None, "page": "paper",
                     "rows": rows, "selected_status": selected},
        )

    @app.get("/paper/{ident}", response_class=HTMLResponse)
    def paper_detail(
        request: Request, ident: str, run_session: date | None = None,
        chart_day: date | None = None,
    ):
        if paper is None:
            raise HTTPException(status_code=404, detail="paper trading not enabled")
        account = paper.account(ident)
        if account is None:
            raise HTTPException(status_code=404, detail="no such paper account")
        initial = account.initial_cash
        daily = paper.equity_daily(ident)
        lineage = shadow_lineage() if shadow_lineage is not None else None
        follows_this = bool(lineage and lineage.paper_account_id == ident)
        shadow_daily = (
            {str(row["dt"]): float(row["total_value"])
             for row in service.equity_by_session()}
            if follows_this else {}
        )
        sessions = [row["session"] for row in daily]
        chart_notice = ""
        if chart_day is not None and chart_day.isoformat() not in sessions:
            chart_notice = f"{chart_day} 没有收益记录，已显示历史曲线"
            chart_day = None
        chart = {
            "view": "intraday" if chart_day else "history",
            "day": chart_day.isoformat() if chart_day else None,
            "sessions": sessions[::-1],
            "history": history_series(daily, initial, shadow_daily),
            "shadow": follows_this,
            "intraday": (
                intraday_series(paper.equity_intraday(ident, chart_day), daily,
                                chart_day, initial)
                if chart_day else []
            ),
            "notice": chart_notice,
        }
        run_sessions = paper.run_sessions(ident)
        selected_run_session = run_session or (
            run_sessions[0] if run_sessions else date.today()
        )
        return TEMPLATES.TemplateResponse(
            request=request, name="paper_detail.html",
            context={
                "title": config.title, "session": None, "page": "paper",
                "account": account, "summary": paper.summary(ident),
                "shadow_follows_this": follows_this,
                "param_rows": param_rows(account.config.get("params", {})),
                "entry_fill_label": ENTRY_FILL_LABELS.get(
                    str(account.config.get("entry_fill", "")), "开盘价成交（原口径）"
                ),
                "notify_mask": _mask(str(account.config.get("notify_webhook", ""))),
                "positions": paper.rows(ident, "positions"),
                "orders": paper.rows(ident, "orders"),
                "fills": paper.rows(ident, "fills"),
                "rejects": paper.rows(ident, "rejects"),
                "events": paper.rows(ident, "events", limit=200),
                "logs": paper.rows(ident, "logs", limit=200),
                "chart": chart,
                "market_health": service.heartbeat_status("market"),
                "paper_health": service.heartbeat_status("paper"),
                "runs": paper.strategy_runs(ident, selected_run_session),
                "run_sessions": run_sessions,
                "selected_run_session": selected_run_session,
                "phase_labels": PHASE_LABELS,
            },
        )

    @app.post("/api/paper-accounts", status_code=201)
    async def create_paper_account(request: Request):
        if paper is None:
            raise HTTPException(status_code=404, detail="paper trading not enabled")
        body = await request.json()
        activation = _paper_activation(datetime.now(), paper_calendar)
        try:
            account = paper.create(
                str(body.get("backtest_id", "")), str(body.get("name", "")),
                float(body.get("cash", 0)), activation,
            )
        except LookupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"id": account.id, "location": f"/paper/{account.id}"}

    @app.get("/api/paper-accounts/{ident}")
    def paper_status(ident: str):
        if paper is None:
            raise HTTPException(status_code=404, detail="paper trading not enabled")
        account = paper.account(ident)
        if account is None:
            raise HTTPException(status_code=404, detail="no such paper account")
        return {"id": ident, "status": account.status, "error": account.error,
                "activation_date": account.activation_date, **paper.summary(ident)}

    @app.post("/api/paper-accounts/{ident}/notify")
    def set_paper_notify(ident: str, body: dict):
        """设置这个账户的企微机器人链接；空串关闭通知。

        链接是凭据，只在这里写入、页面上永远只回掩码。
        """
        if paper is None:
            raise HTTPException(status_code=404, detail="paper trading not enabled")
        url = str(body.get("webhook", "")).strip()
        if url and not url.startswith("https://"):
            raise HTTPException(status_code=400, detail="通知链接必须是 https")
        try:
            account = paper.set_notify_webhook(ident, url)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"id": ident, "notify": _mask(account.config.get("notify_webhook", ""))}

    @app.post("/api/paper-accounts/{ident}/{action}")
    def control_paper(ident: str, action: str):
        if paper is None:
            raise HTTPException(status_code=404, detail="paper trading not enabled")
        if action not in ("pause", "resume", "end"):
            raise HTTPException(status_code=404, detail="unknown paper action")
        activation = (_paper_activation(datetime.now(), paper_calendar)
                      if action == "resume" else None)
        try:
            account = paper.transition(ident, action, activation=activation)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"id": ident, "status": account.status}

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
            entry_fill = str(body.get("entry_fill", "continuous"))
            if entry_fill not in ENTRY_FILL_MODES:
                raise ValueError(f"未知的竞价入场口径：{entry_fill}")
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        job = workbench.enqueue(strategy.id, {
            "start": str(start), "end": str(end), "cash": cash,
            "jq_compat": bool(body.get("jq_compat", False)), "params": params,
            "entry_fill": entry_fill,
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
                "gate_summary": backtests.gate_summary(name),
                "gate_rows": backtests.gate_rows(name),
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
                "paper_enabled": paper is not None and job is not None
                    and job.status == "succeeded",
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
    def root():
        if market is not None:
            return RedirectResponse("/market")
        return RedirectResponse("/live")

    @app.get("/live", response_class=HTMLResponse)
    def live_console(request: Request):
        latest = service.latest_session()
        if latest is None:
            statuses = [
                service.heartbeat_status(source) for source in config.watch_sources
            ]
            statuses.append(service.heartbeat_status("market"))
            return TEMPLATES.TemplateResponse(
                request=request, name="live_empty.html",
                context={"title": config.title, "session": None, "page": "live",
                         "statuses": statuses},
            )
        view = service.overview(latest)
        lineage = shadow_lineage() if shadow_lineage is not None else None
        compare = _shadow_vs_paper(service, paper, lineage)
        return _page(request, "overview.html", latest, "live",
                     view=view, chart=line_chart(view.equity_curve),
                     lineage=lineage, compare=compare,
                     lineage_params=param_rows(getattr(lineage, "params", {}) or {}))

    def _shadow_vs_paper(service, store, lineage) -> dict | None:
        """影子盘与它承袭的模拟账户，按累计收益率逐交易日对比。

        两者本金不同（影子盘启用资金、模拟账户初始资金），只有收益率可比；
        只取两边都有记录的交易日，缺的一天不插值。
        """
        if lineage is None or store is None or not lineage.paper_account_id:
            return None
        account = store.account(lineage.paper_account_id)
        if account is None:
            return None
        shadow = {str(row["dt"]): float(row["total_value"])
                  for row in service.equity_by_session()}
        paper_daily = {row["session"]: float(row["total_value"])
                       for row in store.equity_daily(lineage.paper_account_id)}
        sessions = sorted(set(shadow) & set(paper_daily))
        if not sessions:
            return None
        shadow_base, paper_base = shadow[sessions[0]], account.initial_cash
        if not shadow_base or not paper_base:
            return None
        return {
            "sessions": sessions,
            "chart": multi_line_chart([
                ("影子盘", [shadow[s] / shadow_base - 1.0 for s in sessions], "#7a5bb5"),
                ("模拟交易", [paper_daily[s] / paper_base - 1.0 for s in sessions], "#2463b5"),
            ]),
            "shadow_return": shadow[sessions[-1]] / shadow_base - 1.0,
            "paper_return": paper_daily[sessions[-1]] / paper_base - 1.0,
        }

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

    @app.get("/s/{session}/runs", response_class=HTMLResponse)
    def strategy_runs(request: Request, session: date):
        _require_session(session)
        return _page(
            request,
            "strategy_runs.html",
            session,
            "runs",
            runs=service.strategy_runs(session),
            run_sessions=service.run_sessions(),
        )

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
            if not getattr(route, "path", "").startswith(
                ("/api/strategies", "/api/backtests", "/api/paper-accounts")
            )
        ]
    elif paper is None:
        app.router.routes[:] = [
            route for route in app.router.routes
            if not getattr(route, "path", "").startswith("/api/paper-accounts")
        ]
    return app


def _mask(url: str) -> str:
    """通知链接是凭据，页面上只露尾部几位，够你认出是哪个机器人就行。"""
    if not url:
        return ""
    return f"…{url[-6:]}"


def _paper_activation(
    now: datetime, calendar: Callable[[], AShareCalendar] | None,
) -> date:
    """第一个完整交易日，按 Tushare trade_cal 算（paper.calendar）。

    拿不到交易日历就拒绝创建/恢复，不退回「周一到周五」去猜。
    """
    try:
        if calendar is None:
            raise RuntimeError("未配置交易日历")
        return first_complete_session(now, calendar())
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"交易日历不可用，暂不能确定生效日：{exc}"
        ) from exc
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day
