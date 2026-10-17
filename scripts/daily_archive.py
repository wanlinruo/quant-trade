"""Backward-compatible wrapper for the packaged daily archive CLI."""

from __future__ import annotations

import sys

from qsys.data.daily_archive_cli import main


if __name__ == "__main__":
    sys.exit(main())
