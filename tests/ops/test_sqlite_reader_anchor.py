from contextlib import closing
import gc
import hashlib
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

from qsys.ops.sqlite_reader_anchor import reader_anchor


def history(path, mode="WAL"):
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(f"PRAGMA journal_mode={mode}")
        connection.execute("CREATE TABLE live_events (id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE live_strategy_runs (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO live_events VALUES (1)")
        connection.commit()


def test_cold_wal_read_and_writes_are_rejected_without_changing_main_file(tmp_path):
    path = tmp_path / "state.sqlite3"
    history(path)
    assert not Path(str(path) + "-shm").exists()
    before = path.stat(), hashlib.sha256(path.read_bytes()).hexdigest()
    with reader_anchor(path) as (anchor, verify):
        gc.collect()
        verify()
        assert not anchor.in_transaction
        assert Path(str(path) + "-shm").exists()
        for sql in ("DELETE FROM live_events", "CREATE TABLE wrong (id)",
                    "PRAGMA user_version=4"):
            with pytest.raises(sqlite3.OperationalError):
                anchor.execute(sql)
        assert anchor.execute("SELECT * FROM live_events").fetchall() == [(1,)]
    after = path.stat(), hashlib.sha256(path.read_bytes()).hexdigest()
    assert after[1] == before[1]
    assert after[0].st_mtime_ns == before[0].st_mtime_ns
    assert after[0].st_mode == before[0].st_mode


def test_anchor_does_not_hold_stale_snapshot_or_block_commits(tmp_path):
    path = tmp_path / "state.sqlite3"
    history(path)
    with reader_anchor(path) as (anchor, verify):
        for value in (2, 3):
            with closing(sqlite3.connect(path)) as writer:
                writer.execute("INSERT INTO live_events VALUES (?)", (value,))
                writer.commit()
                assert writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
            gc.collect()
            verify()
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as reader:
                assert reader.execute("SELECT count(*) FROM live_events").fetchone() == (value,)
            assert not anchor.in_transaction


def test_missing_database_is_never_created(tmp_path):
    path = tmp_path / "missing" / "state.sqlite3"
    with pytest.raises(FileNotFoundError), reader_anchor(path):
        pass
    assert not path.parent.exists()


def test_delete_journal_mode_is_not_migrated(tmp_path):
    path = tmp_path / "state.sqlite3"
    history(path, "DELETE")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="already use WAL"), reader_anchor(path):
        pass
    assert path.read_bytes() == before


def test_missing_history_schema_is_rejected(tmp_path):
    path = tmp_path / "state.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE unrelated (id)")
    with pytest.raises(ValueError, match="history tables"), reader_anchor(path):
        pass


def test_symlink_and_replaced_main_file_are_rejected(tmp_path):
    path = tmp_path / "state.sqlite3"
    history(path)
    link = tmp_path / "alias.sqlite3"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="regular files"), reader_anchor(link):
        pass
    with reader_anchor(path) as (_, verify):
        replacement = tmp_path / "replacement.sqlite3"
        history(replacement)
        os.replace(replacement, path)
        with pytest.raises(ValueError, match="replaced"):
            verify()


def test_replaced_sidecar_is_rejected(tmp_path):
    path = tmp_path / "state.sqlite3"
    history(path)
    with reader_anchor(path) as (_, verify):
        shm = Path(str(path) + "-shm")
        shm.rename(tmp_path / "old-shm")
        shm.write_bytes(b"untrusted")
        with pytest.raises(ValueError, match="replaced"):
            verify()


def test_directory_view_detects_replacement_hidden_by_file_mount(tmp_path):
    visible = tmp_path / "visible.sqlite3"
    pinned = tmp_path / "pinned.sqlite3"
    history(visible)
    # A hardlink models a file bind mount retaining the old inode.
    os.link(visible, pinned)
    with reader_anchor(pinned, identity_path=visible) as (_, verify):
        other = tmp_path / "other.sqlite3"
        history(other)
        os.replace(other, visible)
        with pytest.raises(ValueError, match="outside the read-only file mount"):
            verify()


def test_wrong_directory_view_is_rejected(tmp_path):
    first, second = tmp_path / "first.sqlite3", tmp_path / "second.sqlite3"
    history(first)
    history(second)
    with pytest.raises(ValueError, match="same existing state"), reader_anchor(first, identity_path=second):
        pass


def test_real_cli_stops_on_sigterm_without_loading_trading_modules(tmp_path):
    path = tmp_path / "state.sqlite3"
    history(path)
    script = (
        "import runpy,sys; "
        "runpy.run_module('qsys.ops.sqlite_reader_anchor', run_name='__main__')"
    )
    env = {**os.environ, "PYTHONPATH": str(Path("src").resolve()),
           "PYTHONDONTWRITEBYTECODE": "1"}
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", script, "--state", str(path), "--poll-seconds", "0.02"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        limit = time.monotonic() + 5
        while not Path(str(path) + "-shm").exists() and process.poll() is None:
            if time.monotonic() > limit:
                pytest.fail("CLI did not initialize")
            time.sleep(0.01)
        process.send_signal(signal.SIGTERM)
        _, error = process.communicate(timeout=5)
        assert process.returncode == 0, error
        assert "historical state reader ready" in error
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_module_is_stdlib_only_in_clean_interpreter():
    source = str(Path("src").resolve())
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "import qsys.ops.sqlite_reader_anchor; "
        "assert not any(x in sys.modules for x in "
        "('qsys.live.runtime','qsys.live.store','redis','qsys.ops.alerts','qsys.paper.store'))"
    )
    subprocess.run([sys.executable, "-I", "-B", "-c", code, source], check=True)
