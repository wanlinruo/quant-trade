"""研究侧查询（#662 R3，设计文档 §6.3、§8.10、§8.11）：策略、回测、研究任务。

读工作台存储（回测队列与策略）、回测运行目录（BacktestRuns）、模拟账户与影子承袭、研究任务控制台，
映射为对外契约。推进位置、使用中只读、回测警告的口径沿用 08c（research_derive）。
存储里的时间串解析不了时为 None，不让整个接口失败。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

import structlog
from fastapi import HTTPException

from qsys.webapp.contracts.research import (
    ArtifactInfo, BacktestJobInfo, BacktestList, BacktestPending, BacktestReport, BacktestRequest,
    BacktestRow, Cancelled, Created, ResearchJobRequest, Resubmitted, StrategyInput, TemplateImport,
    Episode, MonthReturn, ParameterSpec, ResearchDisabled, ResearchJob, ResearchJobList,
    RunFileInfo, ReturnPoint, RunSummary, StrategyDetail, StrategyList, StrategyRow, StrategyVersion,
)
from qsys.webapp.files import Expected, controlled_file
from qsys.webapp.queries.backtest_runs import RUN_FILE_SUFFIXES, BacktestRuns, RunInfo
from qsys.webapp.queries.research_derive import (
    aligned_series, backtest_warnings, drawdown_episodes, in_use_versions, strategy_stage,
)

log = structlog.get_logger(__name__)

FILLS_LIMIT = 500
REJECTS_LIMIT = 200
LOG_TAIL = 4000
JOB_SCAN = 500


def parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def parse_day(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _duration(started: str, finished: str) -> int | None:
    a, b = parse_time(started), parse_time(finished)
    if a is None or b is None or (a.tzinfo is None) != (b.tzinfo is None):
        return None
    return max(0, int((b - a).total_seconds()))


def _missing() -> HTTPException:
    return HTTPException(status_code=404, detail="文件不存在")


class ResearchQuery:
    def __init__(self, *, workbench=None, runs: BacktestRuns | None = None, paper=None,
                 research_jobs=None, strategy_templates: Path | None = None,
                 shadow_lineage: Callable[[], object | None] | None = None,
                 core_label: str = "影子盘") -> None:
        self.workbench = workbench
        self.runs = runs
        self.paper = paper
        self.research_jobs = research_jobs
        self.strategy_templates = strategy_templates
        self._lineage = shadow_lineage
        self.core_label = core_label

    # ---- 上下文 ----
    def lineage(self):
        if self._lineage is None:
            return None
        try:
            return self._lineage()
        except Exception as exc:  # 承袭关系读不到：只是少显示一个推进位置
            log.info("webapp.research.lineage_unavailable", error_type=type(exc).__name__)
            return None

    def accounts(self) -> list:
        return self.paper.accounts() if self.paper is not None else []

    def used(self) -> dict[str, list[str]]:
        return in_use_versions(self.accounts(), self.lineage(), core_label=self.core_label)

    def templates(self) -> list[str]:
        root = self.strategy_templates
        if root and root.is_dir():
            return sorted(p.name for p in root.glob("*.py"))
        return []

    def _jobs(self) -> list:
        return self.workbench.jobs(limit=JOB_SCAN) if self.workbench is not None else []

    # ---- 回测列表 ----
    def _row_from_run(self, run: RunInfo, job=None) -> dict:
        m = self.runs.metrics(run.name)
        return {"total_return": run.cumulative_return, "benchmark_return": m.get("benchmark_return"),
                "max_drawdown": m.get("max_drawdown"), "has_report": True,
                "cost_model": run.cost_model}

    def backtest_rows(self) -> list[BacktestRow]:
        if self.runs is None:
            return []
        runs = {r.name: r for r in self.runs.runs()}
        rows: list[BacktestRow] = []
        seen: set[str] = set()
        for job in self._jobs():
            run_name = job.run_name or job.id
            run = runs.get(run_name)
            report = self._row_from_run(run) if run else {
                "total_return": None, "benchmark_return": None, "max_drawdown": None, "has_report": False,
                "cost_model": "聚宽兼容" if job.config.get("jq_compat") else "qsys 实盘档位"}
            rows.append(BacktestRow(
                id=job.id, strategy=job.strategy_name, strategy_id=job.strategy_id,
                start=parse_day(job.config.get("start")), end=parse_day(job.config.get("end")),
                created_at=parse_time(job.created_at), started_at=parse_time(job.started_at),
                finished_at=parse_time(job.finished_at),
                duration_seconds=_duration(job.started_at, job.finished_at), status=job.status,
                source="web", progress=job.progress, total=job.total,
                current_session=parse_day(job.current_session), **report))
            seen.add(run_name)
        for name, run in runs.items():
            if name in seen:
                continue
            rows.append(BacktestRow(
                id=name, strategy=run.strategy, strategy_id=None, start=parse_day(run.start),
                end=parse_day(run.end), created_at=parse_time(run.generated_at), started_at=None,
                finished_at=None, duration_seconds=None, status="succeeded", source=run.source,
                progress=None, total=None, current_session=None, **self._row_from_run(run)))
        # 没有时间的排在最后，不猜它的先后
        rows.sort(key=lambda r: (r.created_at is not None, r.created_at.timestamp() if r.created_at else 0),
                  reverse=True)
        return rows

    def backtests(self) -> BacktestList | ResearchDisabled:
        if self.runs is None:
            return ResearchDisabled()
        return BacktestList(rows=self.backtest_rows())

    # ---- 策略 ----
    def _strategy_row(self, s, *, rows, used, succeeded, accts, lin) -> dict:
        from qsys.backtest.workbench import version_id_for

        version = version_id_for(s.id, s.draft)
        stage = strategy_stage(s.id, succeeded_strategy_ids=succeeded, active_accounts=accts,
                               lineage=lin, core_label=self.core_label)
        mine = [r for r in rows if r.strategy_id == s.id and r.status == "succeeded" and r.has_report]
        latest = max(mine, key=lambda r: r.created_at.timestamp() if r.created_at else 0) if mine else None
        return {"id": s.id, "name": s.name, "updated_at": parse_time(s.updated_at), "version_id": version,
                "stage": stage["stage"], "stage_label": stage["label"], "stage_detail": stage["detail"],
                "in_use": version in used, "used_by": used.get(version, []), "latest_backtest": latest}

    def _context(self):
        rows, accts, lin = self.backtest_rows(), self.accounts(), self.lineage()
        used = in_use_versions(accts, lin, core_label=self.core_label)
        succeeded = {j.strategy_id for j in self._jobs() if j.status == "succeeded"}
        return {"rows": rows, "used": used, "succeeded": succeeded, "accts": accts, "lin": lin}

    def strategies(self) -> StrategyList | ResearchDisabled:
        if self.workbench is None:
            return ResearchDisabled()
        ctx = self._context()
        return StrategyList(core_label=self.core_label, templates=self.templates(),
                            strategies=[StrategyRow(**self._strategy_row(s, **ctx))
                                        for s in self.workbench.strategies()])

    def strategy(self, ident: str) -> StrategyDetail | ResearchDisabled:
        if self.workbench is None:
            return ResearchDisabled()
        s = self.workbench.strategy(ident)
        if s is None:
            raise HTTPException(status_code=404, detail="策略不存在")
        ctx = self._context()
        return StrategyDetail(
            **self._strategy_row(s, **ctx), core_label=self.core_label, source=s.draft,
            parameters=[ParameterSpec(**{**asdict(p), "choices": list(p.choices)}) for p in s.parameters],
            versions=[StrategyVersion(id=v["id"], created_at=parse_time(v["created_at"]))
                      for v in self.workbench.versions(s.id)],
            backtests=[r for r in ctx["rows"] if r.strategy_id == s.id])

    # ---- 回测详情 ----
    @staticmethod
    def _job_info(job) -> BacktestJobInfo:
        return BacktestJobInfo(
            id=job.id, strategy_name=job.strategy_name, status=job.status, progress=job.progress,
            total=job.total, percent=job.percent, current_session=parse_day(job.current_session),
            error=job.error or None, config=dict(job.config))

    def _run_name(self, name: str) -> tuple[object | None, str]:
        job = self.workbench.job(name) if self.workbench is not None else None
        return job, (job.run_name or job.id) if job else name

    def backtest(self, name: str) -> BacktestReport | BacktestPending | ResearchDisabled:
        if self.runs is None:
            return ResearchDisabled()
        job, run_name = self._run_name(name)
        run = self.runs.run(run_name)
        if run is None:
            if job is None:
                raise HTTPException(status_code=404, detail="回测不存在")
            return BacktestPending(strategy_id=job.strategy_id, job=self._job_info(job),
                                   log_tail=self.workbench.job_log(job.id, limit=LOG_TAIL))
        nav = self.runs.nav(run_name)
        bench = self.runs.benchmark(run_name)
        series = aligned_series([{"date": d, "total_value": v} for d, v in nav], bench)
        files = self.runs.files(run_name)
        fills = self.runs.table(run_name, "fills.csv")
        daily = self.runs.table(run_name, "daily_nav.csv")
        summary = asdict(run) | {"start": parse_day(run.start), "end": parse_day(run.end),
                                 "generated_at": parse_time(run.generated_at)}
        return BacktestReport(
            name=run_name, strategy_id=job.strategy_id if job else None,
            job=self._job_info(job) if job else None, summary=RunSummary(**summary),
            metrics=self.runs.metrics(run_name),
            series=[ReturnPoint(**p) for p in series],
            episodes=[Episode(**e) for e in drawdown_episodes(nav)],
            monthly=[MonthReturn(month=str(r.get("month")), value=r.get("return"))
                     for r in self.runs.table(run_name, "monthly_returns.csv")],
            fills=fills[-FILLS_LIMIT:], fills_total=len(fills),
            rejects=self.runs.table(run_name, "rejects.csv", limit=REJECTS_LIMIT),
            files=[RunFileInfo(name=f.name, size=f.size) for f in files],
            warnings=backtest_warnings(
                rejects=run.rejects or 0, has_benchmark=any(f.name == "benchmark_nav.csv" for f in files),
                missing_benchmark_days=sum(1 for p in series if p["benchmark"] is None) if bench else 0,
                missing_daily_returns=sum(1 for r in daily[1:] if r.get("daily_return") is None),
                cost_model=run.cost_model or "", status=job.status if job else None))

    def backtest_file(self, name: str, filename: str):
        if self.runs is None:
            raise _missing()
        _, run_name = self._run_name(name)
        if filename not in {f.name for f in self.runs.files(run_name)}:
            raise _missing()
        return controlled_file(self.runs.root / run_name, filename, suffixes=RUN_FILE_SUFFIXES)

    # ---- 研究任务 ----
    @staticmethod
    def _artifacts(job) -> list[dict]:
        art = job.artifacts if isinstance(job.artifacts, dict) else {}
        return [f for f in art.get("files", []) if isinstance(f, dict) and f.get("path")]

    def _research_job(self, j) -> ResearchJob:
        req = j.request
        return ResearchJob(
            id=j.job_id, state=j.state, detail=j.detail, task=req.get("task"),
            parameters=dict(req.get("parameters") or {}),
            timeout_seconds=(req.get("limits") or {}).get("timeout_seconds"),
            created_at=parse_time(req.get("created_at")), code_sha=req.get("code_sha"),
            snapshot_id=req.get("snapshot_id"), snapshot_content_sha256=req.get("snapshot_content_sha256"),
            artifacts=[ArtifactInfo(path=str(f["path"]), bytes=f.get("bytes"), sha256=f.get("sha256"))
                       for f in self._artifacts(j)])

    def research(self) -> ResearchJobList | ResearchDisabled:
        if self.research_jobs is None:
            return ResearchDisabled()
        return ResearchJobList(submission_enabled=self.research_jobs.submission_enabled,
                               jobs=[self._research_job(j) for j in self.research_jobs.jobs()])

    def research_artifact(self, ident: str, path: str):
        if self.research_jobs is None:
            raise _missing()
        job = self.research_jobs.job(ident, log_limit=1)
        if job is None:
            raise _missing()
        entry = next((f for f in self._artifacts(job) if f["path"] == path), None)
        if entry is None or not isinstance(entry.get("bytes"), int) or not entry.get("sha256"):
            raise _missing()
        root = self.research_jobs.root / "jobs" / job.job_id / "artifacts"
        return controlled_file(root, path, suffixes=None,
                               expected=Expected(bytes=entry["bytes"], sha256=str(entry["sha256"])))


# ---- 写操作（只在对应服务启用时由路由调用）----
RESUBMITTABLE = frozenset({"failed", "cancelled", "interrupted"})


def _http(status: int, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail=message)


class ResearchWrites:
    """写操作：校验与调用口径与旧接口相同（入队配置键是回测 worker 的输入契约）。"""

    def __init__(self, query: ResearchQuery) -> None:
        self.q = query

    def create_strategy(self, body: StrategyInput) -> Created:
        try:
            s = self.q.workbench.create_strategy(body.name, body.source)
        except (ValueError, SyntaxError) as exc:
            raise _http(422, str(exc)) from exc
        return Created(id=s.id)

    def save_strategy(self, ident: str, body: StrategyInput) -> Created:
        from qsys.backtest.workbench import version_id_for

        s = self.q.workbench.strategy(ident)
        if s is None:
            raise _http(404, "策略不存在")
        users = self.q.used().get(version_id_for(s.id, s.draft))
        if users:
            raise _http(409, f"该版本正被{'、'.join(users)}使用，只读；请另存为新策略")
        try:
            saved = self.q.workbench.save_strategy(ident, body.name, body.source)
        except LookupError as exc:
            raise _http(404, "策略不存在") from exc
        except (ValueError, SyntaxError) as exc:
            raise _http(422, str(exc)) from exc
        return Created(id=saved.id)

    def import_template(self, body: TemplateImport) -> Created:
        name = body.filename
        if not name or Path(name).name != name or not name.endswith(".py") or name.startswith("."):
            raise _http(422, "模板文件名不合法")
        root = self.q.strategy_templates.resolve()
        target = root / name
        if target.is_symlink() or not target.is_file() or target.resolve().parent != root:
            raise _http(404, "模板不存在")
        return Created(id=self.q.workbench.import_file(target).id)

    def enqueue_backtest(self, body: BacktestRequest) -> Created:
        from qsys.backtest.runner import ENTRY_FILL_MODES
        from qsys.strategy.parameters import validate_parameters

        s = self.q.workbench.strategy(body.strategy_id)
        if s is None:
            raise _http(404, "策略不存在")
        if body.start > body.end or body.cash <= 0:
            raise _http(422, "日期区间或初始资金无效")
        if body.entry_fill not in ENTRY_FILL_MODES:
            raise _http(422, f"未知的竞价入场口径：{body.entry_fill}")
        try:
            params = validate_parameters(s.parameters, body.params)
        except (TypeError, ValueError) as exc:
            raise _http(422, str(exc)) from exc
        job = self.q.workbench.enqueue(s.id, {
            "start": str(body.start), "end": str(body.end), "cash": float(body.cash),
            "jq_compat": body.jq_compat, "params": params, "entry_fill": body.entry_fill})
        return Created(id=job.id)

    def cancel_backtest(self, ident: str) -> Cancelled:
        if not self.q.workbench.cancel(ident):
            raise _http(409, "该回测已结束或不存在，不能取消")
        return Cancelled(status="cancelled")

    def submit_research(self, body: ResearchJobRequest) -> Created:
        try:
            job = self.q.research_jobs.submit(body.task, body.parameters, timeout_seconds=body.timeout_seconds)
        except RuntimeError as exc:
            raise _http(403, "本节点未开启网页提交") from exc
        except (OSError, TypeError, ValueError) as exc:
            raise _http(422, str(exc)) from exc
        return Created(id=job.job_id)

    def cancel_research(self, ident: str) -> Cancelled:
        try:
            self.q.research_jobs.cancel(ident)
        except LookupError as exc:
            raise _http(404, "研究任务不存在") from exc
        except ValueError as exc:
            raise _http(409, str(exc)) from exc
        return Cancelled(status="cancellation-requested")

    def resubmit_research(self, ident: str) -> Resubmitted:
        rj = self.q.research_jobs
        job = rj.job(ident, log_limit=1)
        if job is None:
            raise _http(404, "研究任务不存在")
        if not rj.submission_enabled:
            raise _http(403, "本节点未开启网页提交")
        if job.state not in RESUBMITTABLE:
            raise _http(409, "只有失败、取消或中断的任务可以重新提交")
        req = job.request
        new = self.submit_research(ResearchJobRequest(
            task=str(req.get("task", "")), parameters=dict(req.get("parameters") or {}),
            timeout_seconds=(req.get("limits") or {}).get("timeout_seconds")))
        return Resubmitted(id=new.id, resubmitted_from=ident)
