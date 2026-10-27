"""Keep an existing WAL database readable without starting its trading producer.

Only SQLite shared-memory/WAL bookkeeping may be created.  The database is
opened with mode=ro; no schema, journal-mode or business writes are performed.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import logging
import math
from pathlib import Path
import signal
import sqlite3
import stat
from threading import Event

log = logging.getLogger(__name__)


def _identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("state and sidecars must be regular files, not symlinks")
    return info.st_dev, info.st_ino


@contextmanager
def reader_anchor(path: Path, *, identity_path: Path | None = None):
    """Pin a read-only, transaction-free connection to one existing WAL file."""
    if not path.is_absolute():
        raise ValueError("state path must be absolute")
    original = _identity(path)
    visible_path = path if identity_path is None else identity_path
    if not visible_path.is_absolute() or _identity(visible_path) != original:
        raise ValueError("identity path must expose the same existing state file")
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            _identity(sidecar)
    connection = sqlite3.connect(
        path.as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=5,
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA journal_mode").fetchone() != ("wal",):
            raise ValueError("state must already use WAL; journal mode is never changed")
        # This read materializes SQLite's sidecars without retaining a snapshot.
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if not {"live_events", "live_strategy_runs"} <= tables:
            raise ValueError("state is missing the existing Live history tables")
        sidecars = {
            Path(str(path) + suffix): _identity(Path(str(path) + suffix))
            for suffix in ("-wal", "-shm")
        }

        def verify() -> None:
            if _identity(path) != original:
                raise ValueError("state file was replaced; refusing to pin stale history")
            if _identity(visible_path) != original:
                raise ValueError("state file was replaced outside the read-only file mount")
            if any(_identity(file) != identity for file, identity in sidecars.items()):
                raise ValueError("SQLite sidecar was replaced")
            # Finalize each statement: a long read transaction would pin a
            # stale snapshot and prevent normal writer checkpoints.
            connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
            if connection.in_transaction:
                raise RuntimeError("reader anchor must not hold a transaction")

        verify()
        yield connection, verify
    finally:
        connection.close()


def _positive_seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("seconds must be finite and positive")
    return seconds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument(
        "--identity-state", type=Path,
        help="同一主库的目录级只读视图；用于检测文件只读挂载外的替换",
    )
    parser.add_argument("--poll-seconds", type=_positive_seconds, default=5.0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    stopped = Event()
    previous = {}
    try:
        for number in (signal.SIGTERM, signal.SIGINT):
            previous[number] = signal.signal(number, lambda *_: stopped.set())
        with reader_anchor(args.state, identity_path=args.identity_state) as (_, verify):
            log.info("historical state reader ready; trading execution is not running here")
            while not stopped.wait(args.poll_seconds):
                verify()
        return 0
    except (OSError, ValueError, sqlite3.Error, RuntimeError):
        log.exception("historical state reader unavailable")
        return 1
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == "__main__":
    raise SystemExit(main())
