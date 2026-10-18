"""Bind and enqueue one monthly study; never execute or activate a Runner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from qsys.research.jobs import JobLimits, JobRequest, JobStore
from qsys.research.monthly_tasks import MONTHLY_TIMEOUTS, validate_monthly_request
from qsys.web.research_jobs import ResearchSubmissionProfile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--task", choices=tuple(MONTHLY_TIMEOUTS), required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--market-data-facade", choices=("legacy", "canonical"), required=True)
    parser.add_argument("--memory-max-mb", type=int, required=True)
    parser.add_argument("--cpu-quota-percent", type=int, required=True)
    parser.add_argument("--io-weight", type=int, required=True)
    args = parser.parse_args(argv)
    limits = JobLimits(args.memory_max_mb, args.cpu_quota_percent, args.io_weight,
                       MONTHLY_TIMEOUTS[args.task])
    profile = ResearchSubmissionProfile.from_release(
        args.release, args.snapshots, limits, tasks=frozenset(MONTHLY_TIMEOUTS),
    )
    bound = profile.request(task=args.task, parameters={
        "start": args.start, "end": args.end, "market_data_facade": args.market_data_facade,
    })
    request = JobRequest.create(
        job_id=args.job_id, code_sha=bound.code_sha, wheel_sha256=bound.wheel_sha256,
        snapshot_id=bound.snapshot_id, snapshot_content_sha256=bound.snapshot_content_sha256,
        task=bound.task, parameters=bound.parameters, limits=bound.limits,
        created_at=bound.created_at,
    )
    snapshot = profile.snapshot_store / "snapshots" / request.snapshot_id
    validate_monthly_request(request, snapshot)
    path = JobStore(args.jobs, initialize=False).enqueue(request)
    print(json.dumps({"job_id": request.job_id, "request": str(path),
                      "code_sha": request.code_sha, "snapshot_id": request.snapshot_id},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
