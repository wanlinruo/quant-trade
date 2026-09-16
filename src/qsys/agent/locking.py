"""账户级执行代理单实例锁。"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


@contextmanager
def single_instance(path: str | Path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = target.open("a+b")
    try:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AlreadyRunning(f"execution agent already holds {target}") from exc
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
