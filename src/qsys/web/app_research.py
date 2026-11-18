"""新前端研究侧接口：策略、回测与研究任务（#662，设计文档 §6.3、§8.10、§8.11）。

读接口一律 no-store；依赖的服务未配置时返回 {"enabled": false}。
写接口只有两个新增：带“使用中只读”保护的策略保存、研究任务按原参数重新提交
（新任务 ID，原记录保留）。其余写操作沿用现有 /api/strategies、/api/backtests、
/api/research-jobs。派生全部在 research_views，前端不重算（§9.5）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from qsys.webapp.queries.research_derive import (
    aligned_series,
    backtest_warnings,
    drawdown_episodes,
    in_use_versions,
    strategy_stage,
)
from qsys.web.workbench import version_id_for

NO_STORE = {"Cache-Control": "no-store"}
DISABLED = {"enabled": False}
RESUBMITTABLE = frozenset({"failed", "cancelled", "interrupted"})
FILLS_LIMIT = 500
REJECTS_LIMIT = 200
LOG_TAIL = 4000
ALL_ROWS = 10**9


def _json(body: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(jsonable_encoder(body), status_code=status, headers=NO_STORE)


def register_app_research(app: FastAPI, *, workbench, backtests, paper, research_jobs,
                          strategy_templates: Path | None,
                          shadow_lineage: Callable[[], object | None] | None,
                          core_label: str, backtest_rows: Callable) -> None:

    def lineage():
        if shadow_lineage is None:
            return None
        try:
            return shadow_lineage()
        except Exception:  # 承袭关系读不到：只是少显示一个推进位置
            return None

    def accounts() -> list:
        return paper.accounts() if paper is not None else []

    def rows() -> list[dict]:
        if backtests is None:
            return []
        out = backtest_rows(backtests, workbench)
        jobs = {j.id: j for j in workbench.jobs(limit=500)} if workbench else {}
        for row in out:
            job = jobs.get(row["id"])
            row["progress"] = job.progress if job else None
            row["total"] = job.total if job else None
            row["current_session"] = (job.current_session or None) if job else None
        out.sort(key=lambda r: str(r["created_at"]), reverse=True)
        return out

    def templates() -> list[str]:
        if strategy_templates and strategy_templates.is_dir():
            return sorted(p.name for p in strategy_templates.glob("*.py"))
        return []

    def strategy_row(s, *, all_rows: list[dict], used: dict, succeeded: set[str], accts, lin) -> dict:
        version = version_id_for(s.id, s.draft)
        stage = strategy_stage(s.id, succeeded_strategy_ids=succeeded, active_accounts=accts,
                               lineage=lin, core_label=core_label)
        mine = [r for r in all_rows if r["strategy_id"] == s.id and r["status"] == "succeeded"
                and r["has_report"]]
        return {
            "id": s.id, "name": s.name, "updated_at": s.updated_at, "version_id": version,
            "stage": stage["stage"], "stage_label": stage["label"], "stage_detail": stage["detail"],
            "in_use": version in used, "used_by": used.get(version, []),
            "latest_backtest": max(mine, key=lambda r: str(r["created_at"])) if mine else None,
        }

    def context():
        all_rows = rows()
        accts, lin = accounts(), lineage()
        used = in_use_versions(accts, lin, core_label=core_label)
        succeeded = {j.strategy_id for j in workbench.jobs(limit=500) if j.status == "succeeded"}
        return all_rows, accts, lin, used, succeeded

    @app.get("/api/app/strategies")
    def app_strategies():
        if workbench is None:
            return _json(DISABLED)
        all_rows, accts, lin, used, succeeded = context()
        return _json({
            "enabled": True, "core_label": core_label, "templates": templates(),
            "strategies": [strategy_row(s, all_rows=all_rows, used=used, succeeded=succeeded,
                                        accts=accts, lin=lin) for s in workbench.strategies()],
        })

    @app.get("/api/app/strategies/{ident}")
    def app_strategy(ident: str):
        if workbench is None:
            return _json(DISABLED)
        s = workbench.strategy(ident)
        if s is None:
            raise HTTPException(status_code=404, detail="策略不存在")
        all_rows, accts, lin, used, succeeded = context()
        body = strategy_row(s, all_rows=all_rows, used=used, succeeded=succeeded, accts=accts, lin=lin)
        body.update({
            "enabled": True, "core_label": core_label, "source": s.draft,
            "parameters": [asdict(p) for p in s.parameters],
            "versions": workbench.versions(s.id),
            "backtests": [r for r in all_rows if r["strategy_id"] == s.id],
        })
        return _json(body)

    async def app_save_strategy(ident: str, request: Request):
        s = workbench.strategy(ident)
        if s is None:
            raise HTTPException(status_code=404, detail="策略不存在")
        used = in_use_versions(accounts(), lineage(), core_label=core_label)
        users = used.get(version_id_for(s.id, s.draft))
        if users:
            raise HTTPException(status_code=409,
                                detail=f"该版本正被{'、'.join(users)}使用，只读；请另存为新策略")
        body = await request.json()
        try:
            saved = workbench.save_strategy(ident, str(body.get("name", "")), str(body.get("source", "")))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, SyntaxError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"id": saved.id, "updated_at": saved.updated_at}

    # 写接口只在对应服务启用时注册：未启用研究侧的部署保持完全只读（test_no_write_endpoints_exist）
    if workbench is not None:
        app.put("/api/app/strategies/{ident}")(app_save_strategy)

    @app.get("/api/app/backtests")
    def app_backtests():
        if backtests is None:
            return _json(DISABLED)
        return _json({"enabled": True, "rows": rows()})

    @app.get("/api/app/backtests/{name}")
    def app_backtest(name: str):
        if backtests is None:
            return _json(DISABLED)
        job = workbench.job(name) if workbench else None
        run_name = (job.run_name or job.id) if job else name
        run = backtests.run(run_name)
        if run is None:
            if job is None:
                raise HTTPException(status_code=404, detail="回测不存在")
            return _json({
                "enabled": True, "report": False, "strategy_id": job.strategy_id,
                "job": {"id": job.id, "strategy_name": job.strategy_name, "status": job.status,
                        "progress": job.progress, "total": job.total, "percent": job.percent,
                        "current_session": job.current_session or None, "error": job.error,
                        "config": dict(job.config)},
                "log_tail": workbench.job_log(job.id, limit=LOG_TAIL),
            })
        nav = backtests.rows(run_name, "daily_nav.csv", limit=ALL_ROWS)
        bench_rows = backtests.rows(run_name, "benchmark_nav.csv", limit=ALL_ROWS)
        files = {f.name for f in backtests.files(run_name)}
        bench = {str(r["date"]): float(r["total_value"]) for r in bench_rows
                 if r.get("total_value") is not None}
        series = aligned_series(nav, bench)
        all_fills = backtests.rows(run_name, "fills.csv", limit=ALL_ROWS)
        summary = asdict(run)
        summary["return_pct"] = run.return_pct
        return _json({
            "enabled": True, "report": True, "name": run_name,
            "strategy_id": job.strategy_id if job else None,
            "job": {"id": job.id, "status": job.status, "config": dict(job.config)} if job else None,
            "summary": summary, "metrics": backtests.metric_map(run_name),
            "series": series,
            "episodes": drawdown_episodes([(str(r["date"]), float(r["total_value"])) for r in nav]),
            "monthly": backtests.rows(run_name, "monthly_returns.csv", limit=ALL_ROWS),
            "fills": all_fills[-FILLS_LIMIT:], "fills_total": len(all_fills),
            "rejects": backtests.rows(run_name, "rejects.csv", limit=REJECTS_LIMIT),
            "files": [f.name for f in backtests.files(run_name)],
            "warnings": backtest_warnings(
                rejects=run.rejects, has_benchmark="benchmark_nav.csv" in files,
                missing_benchmark_days=sum(1 for p in series if p["benchmark"] is None)
                if bench else 0,
                missing_daily_returns=sum(1 for r in nav[1:] if r.get("daily_return") is None),
                cost_model=run.cost_model, status=job.status if job else None),
        })

    def job_row(j) -> dict:
        req = j.request
        files = (j.artifacts or {}).get("files", []) if isinstance(j.artifacts, dict) else []
        return {
            "id": j.job_id, "state": j.state, "detail": j.detail, "task": req.get("task"),
            "parameters": req.get("parameters", {}),
            "timeout_seconds": (req.get("limits") or {}).get("timeout_seconds"),
            "created_at": req.get("created_at"), "code_sha": req.get("code_sha"),
            "snapshot_id": req.get("snapshot_id"),
            "snapshot_content_sha256": req.get("snapshot_content_sha256"),
            "artifacts": [{"path": f.get("path"), "bytes": f.get("bytes"), "sha256": f.get("sha256")}
                          for f in files],
        }

    @app.get("/api/app/research-jobs")
    def app_research_jobs():
        if research_jobs is None:
            return _json(DISABLED)
        return _json({"enabled": True, "submission_enabled": research_jobs.submission_enabled,
                      "jobs": [job_row(j) for j in research_jobs.jobs()]})

    def app_resubmit_research_job(ident: str):
        job = research_jobs.job(ident)
        if job is None:
            raise HTTPException(status_code=404, detail="研究任务不存在")
        if not research_jobs.submission_enabled:
            raise HTTPException(status_code=403, detail="本节点未开启网页提交")
        if job.state not in RESUBMITTABLE:
            raise HTTPException(status_code=409, detail="只有失败、取消或中断的任务可以重新提交")
        req = job.request
        try:
            new = research_jobs.submit(str(req.get("task", "")), dict(req.get("parameters", {})),
                                       timeout_seconds=(req.get("limits") or {}).get("timeout_seconds"))
        except RuntimeError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (OSError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"id": new.job_id, "resubmitted_from": ident}

    if research_jobs is not None:
        app.post("/api/app/research-jobs/{ident}/resubmit", status_code=201)(app_resubmit_research_job)
