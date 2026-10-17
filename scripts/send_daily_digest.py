"""Compatibility wrapper for the independently packaged daily digest CLI."""

import sys

from qsys.ops.daily_digest_cli import (
    EXPECTED_SERVICES,
    _since,
    checks_and_missing,
    clean_streak,
    main,
)

__all__ = [
    "EXPECTED_SERVICES",
    "_since",
    "checks_and_missing",
    "clean_streak",
    "main",
]


if __name__ == "__main__":
    sys.exit(main())
