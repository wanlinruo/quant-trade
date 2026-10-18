"""Publish service-checks snapshots for legacy readers without rewriting records."""
from __future__ import annotations

import json
import os
import pwd
import re
import stat
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

SOURCE = Path('/home/ubuntu/qsys/var/service-runs/service-checks')
DESTINATION = SOURCE.parent
MAX_BYTES = 2 * 1024 * 1024
FILE = re.compile(r'service-checks-\d{6}-\d+\.json')
FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@contextmanager
def _directory(name, *, parent=None, owner=None):
    if owner is not None:
        try:
            os.mkdir(name, mode=0o755, dir_fd=parent)
        except FileExistsError:
            pass
        else:
            os.chown(name, *owner, dir_fd=parent, follow_symlinks=False)
    fd = os.open(name, FLAGS, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def _snapshot(parent, filename, *, producer_uid, day):
    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != producer_uid:
            raise ValueError('record must be a regular file owned by service-checks')
        if info.st_size > MAX_BYTES:
            raise ValueError('record exceeds size limit')
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('record exceeds size limit')
    data = json.loads(raw)
    if (data.get('service') != 'service-checks' or data.get('run_id') != filename[:-5]
            or datetime.fromisoformat(data['started_at']).strftime('%Y/%m/%d') != day):
        raise ValueError('record identity or date differs from its path')
    return raw


def _publish(parent, filename, raw):
    try:
        fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except FileNotFoundError:
        pass
    else:
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('existing index is not a regular file')
            if stream.read(MAX_BYTES + 1) != raw:
                raise ValueError('refusing to overwrite a different legacy record')
        return False
    temporary = f'.{filename}.index-{uuid4().hex}'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o644, dir_fd=parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        # Hard-link our new temporary file: atomic and never replaces an old path.
        os.link(temporary, filename, src_dir_fd=parent, dst_dir_fd=parent,
                follow_symlinks=False)
    finally:
        os.unlink(temporary, dir_fd=parent)
    return True


def publish(source: Path, destination: Path, *, producer_uid: int,
            legacy_owner: tuple[int, int]) -> int:
    count = 0
    with _directory(source) as src, _directory(destination) as dst:
        for year in sorted(os.listdir(src)):
            if not re.fullmatch(r'\d{4}', year):
                continue
            with _directory(year, parent=src) as sy:
                for month in sorted(os.listdir(sy)):
                    if not re.fullmatch(r'\d{2}', month):
                        continue
                    with _directory(month, parent=sy) as sm:
                        for day in sorted(os.listdir(sm)):
                            if not re.fullmatch(r'\d{2}', day):
                                continue
                            date_path = f'{year}/{month}/{day}'
                            datetime.strptime(date_path, '%Y/%m/%d')
                            with _directory(day, parent=sm) as sd:
                                for filename in sorted(os.listdir(sd)):
                                    if not FILE.fullmatch(filename):
                                        continue
                                    raw = _snapshot(sd, filename, producer_uid=producer_uid,
                                                    day=date_path)
                                    with _directory(year, parent=dst, owner=legacy_owner) as dy:
                                        with _directory(month, parent=dy, owner=legacy_owner) as dm:
                                            with _directory(day, parent=dm, owner=legacy_owner) as dd:
                                                count += _publish(dd, filename, raw)
    return count


def main() -> int:
    producer = pwd.getpwnam('qsys-service-checks')
    legacy = pwd.getpwnam('ubuntu')
    count = publish(SOURCE, DESTINATION, producer_uid=producer.pw_uid,
                    legacy_owner=(legacy.pw_uid, legacy.pw_gid))
    print(json.dumps({'published': count, 'service': 'service-checks'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
