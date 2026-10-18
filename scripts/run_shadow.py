"""Compatibility wrapper for the packaged Shadow Core runner."""

from qsys.live.shadow_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
