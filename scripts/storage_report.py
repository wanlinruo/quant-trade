"""Compatibility wrapper for the packaged storage-report CLI."""

from qsys.ops.storage_report_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
