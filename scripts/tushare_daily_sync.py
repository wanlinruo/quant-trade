#!/usr/bin/env python3
"""Compatibility wrapper for the packaged tushare-sync entrypoint."""

from qsys.data.tushare_sync_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
