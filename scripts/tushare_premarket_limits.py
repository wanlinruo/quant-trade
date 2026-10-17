#!/usr/bin/env python3
"""Compatibility wrapper for the packaged premarket-limits entrypoint."""

from qsys.data.premarket_limits_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
