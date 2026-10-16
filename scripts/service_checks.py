import sys
from qsys.ops.service_checks_cli import _exit_code, checks_for_mode, main

__all__ = ["_exit_code", "checks_for_mode", "main"]


if __name__ == "__main__":
    sys.exit(main())
