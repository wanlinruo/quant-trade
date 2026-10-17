"""Compatibility wrapper for the independently packaged health observer CLI."""

import sys

from qsys.ops.health_observer_cli import main

__all__ = ["main"]


if __name__ == "__main__":
    sys.exit(main())
