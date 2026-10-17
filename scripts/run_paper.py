"""Compatibility wrapper for the packaged Paper runner."""

from qsys.paper.runner_cli import (
    _poll_delay,
    main,
    run_account_loop,
    run_supervisor,
    run_worker,
)

__all__ = ["main", "run_account_loop", "run_supervisor", "run_worker"]


if __name__ == "__main__":
    raise SystemExit(main())
