"""单个工作台任务的子进程入口。"""

from __future__ import annotations

import argparse
import traceback
from datetime import date
from pathlib import Path

from qsys.backtest.runner import execute_backtest
from qsys.web.workbench import WorkbenchStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--warehouse", required=True)
    parser.add_argument("--runs", required=True)
    args = parser.parse_args()
    store = WorkbenchStore(Path(args.db))
    job = store.job(args.job)
    if job is None or job.status != "running":
        return 2
    workspace = Path(args.db).parent / "jobs" / job.id
    workspace.mkdir(parents=True, exist_ok=True)
    source = store.source_for_job(job.id)
    strategy_file = workspace / "strategy.py"
    strategy_file.write_text(source, encoding="utf-8")
    config = job.config
    try:
        run = execute_backtest(
            strategy_file, start=date.fromisoformat(str(config["start"])),
            end=date.fromisoformat(str(config["end"])), cash=float(config["cash"]),
            warehouse=Path(args.warehouse), out_dir=Path(args.runs) / job.id,
            params=config.get("params", {}), jq_compat=bool(config.get("jq_compat", False)),
            progress=lambda done, total, session: store.progress(
                job.id, done, total, str(session)), strategy_source=source,
            # 没有此键的旧任务一律按原口径复现
            entry_fill=str(config.get("entry_fill", "open")),
        )
        store.finish(job.id, "succeeded", run_name=run.name)
        return 0
    except Exception as exc:
        detail = "".join(traceback.format_exception(exc))
        with (workspace / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(detail)
        store.finish(job.id, "failed", error=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
