"""Read historical Live state without constructing a trading runtime."""

import sys

from qsys.ops.sqlite_reader_anchor import main


if __name__ == "__main__":
    sys.exit(main())
