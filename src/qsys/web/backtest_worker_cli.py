"""Dedicated single-task worker for the personal backtest workbench."""

from __future__ import annotations

import argparse
import signal
from pathlib import Path

from qsys.web.workbench import BacktestQueue, WorkbenchStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace", type=Path, required=True,
        help="Workbench state directory containing workbench.sqlite3 and job logs",
    )
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=21_600)
    args = parser.parse_args(argv)
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")

    store = WorkbenchStore(args.workspace / "workbench.sqlite3")
    queue = BacktestQueue(
        store, args.warehouse, args.runs,
        timeout_seconds=args.timeout_seconds,
    )

    def stop(_signum: int, _frame: object) -> None:
        queue.stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    queue.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
