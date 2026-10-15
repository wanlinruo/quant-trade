"""CLI for the isolated, single-concurrency qsys Research Runner."""

from __future__ import annotations

import argparse
import signal
from pathlib import Path

from qsys.research.jobs import JobLimits, JobStore
from qsys.research.runner import ResearchRunner, RunnerIdentity


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", required=True, type=Path)
    parser.add_argument("--snapshots", required=True, type=Path)
    parser.add_argument("--code-sha", required=True)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--memory-max-mb", required=True, type=int)
    parser.add_argument("--cpu-quota-percent", required=True, type=int)
    parser.add_argument("--io-weight", required=True, type=int)
    parser.add_argument("--timeout-seconds", required=True, type=int)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--allow-test-drills", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    runner = ResearchRunner(
        JobStore(args.jobs),
        args.snapshots,
        RunnerIdentity(
            args.code_sha,
            args.wheel,
            JobLimits(
                args.memory_max_mb,
                args.cpu_quota_percent,
                args.io_weight,
                args.timeout_seconds,
            ),
        ),
        poll_seconds=args.poll_seconds,
        allow_test_drills=args.allow_test_drills,
    )
    signal.signal(signal.SIGTERM, lambda *_: runner.stop())
    signal.signal(signal.SIGINT, lambda *_: runner.stop())
    runner.serve()
    return 0
