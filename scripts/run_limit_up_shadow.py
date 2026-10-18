"""Compatibility wrapper for the packaged limit-up shadow entrypoint."""

from qsys.research.limit_up_shadow_publish_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
