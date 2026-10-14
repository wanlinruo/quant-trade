"""Source-checkout wrapper for the packaged Warehouse snapshot CLI."""

from qsys.data.snapshot_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
