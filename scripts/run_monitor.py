"""Compatibility wrapper for the packaged qsys Web entrypoint."""

from qsys.web.monitor_cli import build_app, main

__all__ = ["build_app", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
