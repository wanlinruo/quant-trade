from pathlib import Path
from types import SimpleNamespace
import stat

import pytest

from qsys.live import state_binding as binding
from qsys.live.transport import MemoryTransport
from qsys.web.live_kill_status import LiveKillReader


def test_absent_binding_keeps_legacy_and_explicit_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(binding, "BINDING_FILE", tmp_path / "absent")
    for path in [None, Path("var/KILL"), binding.LEGACY_KILL, tmp_path / "custom"]:
        assert binding.local_kill_path(path) == path


@pytest.mark.parametrize("kind", ["symlink", "writable", "nonroot", "content"])
def test_invalid_marker_rejected(kind, monkeypatch):
    mode = stat.S_IFLNK | 0o644 if kind == "symlink" else stat.S_IFREG | (0o666 if kind == "writable" else 0o644)
    marker = SimpleNamespace(lstat=lambda: SimpleNamespace(st_mode=mode, st_uid=123 if kind == "nonroot" else 0,
                                                           st_size=len(binding.BINDING_CONTENT)),
                             read_bytes=lambda: b"invalid" if kind == "content" else binding.BINDING_CONTENT)
    monkeypatch.setattr(binding, "BINDING_FILE", marker)
    with pytest.raises(ValueError):
        binding.current_binding()


def test_binding_remains_after_code_compensation_and_reader_refreshes_each_read(tmp_path, monkeypatch):
    current = [None]
    monkeypatch.setattr(binding, "current_binding", lambda: current[0])
    reader = LiveKillReader(MemoryTransport(), binding.LEGACY_KILL)
    bound = binding.LiveStateBinding(tmp_path / "private")
    bound.kill.parent.mkdir(parents=True)
    bound.kill.write_text("latest stop")
    current[0] = bound
    assert reader._local().state == "stopped"
    dropin = tmp_path / "zz-isolated-release.conf"
    dropin.write_text("new code")
    dropin.unlink()  # 安装器代码补偿不能把状态绑定读回旧路径。
    assert binding.local_kill_path(binding.LEGACY_KILL) == bound.kill
    assert reader._local().reason == "latest stop"
    bound.kill.unlink()
    assert reader._local().state == "clear"
    assert binding.local_kill_path(tmp_path / "explicit") == tmp_path / "explicit"


def test_broken_binding_shows_unknown_instead_of_old_clear(monkeypatch):
    monkeypatch.setattr(binding, "current_binding", lambda: (_ for _ in ()).throw(ValueError("bad marker")))
    reader = LiveKillReader(MemoryTransport(), binding.LEGACY_KILL)
    assert reader._local().state == "unknown"


def test_valid_root_owned_marker_selects_fixed_paths(monkeypatch, tmp_path):
    marker = SimpleNamespace(lstat=lambda: SimpleNamespace(st_mode=stat.S_IFREG | 0o644,
                                                           st_uid=0, st_size=len(binding.BINDING_CONTENT)),
                             read_bytes=lambda: binding.BINDING_CONTENT)
    monkeypatch.setattr(binding, "BINDING_FILE", marker)
    monkeypatch.setattr(binding, "STATE_ROOT", tmp_path)
    actual = binding.current_binding()
    assert actual.state == tmp_path / "var/live-state.sqlite3"
    assert actual.kill == tmp_path / "var/KILL"
    assert actual.lock == tmp_path / "var/live-core.lock"
    assert actual.workspace == tmp_path / "var/live"


def test_existing_readers_and_legacy_writer_follow_latest_bound_state(tmp_path, monkeypatch):
    import sqlite3
    from qsys.live.store import LiveStore

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(binding, 'current_binding', lambda: None)
    original = LiveStore('var/live-state.sqlite3')
    reader = LiveStore('var/live-state.sqlite3', read_only=True)
    target = binding.LiveStateBinding(tmp_path / 'private')
    fresh = LiveStore(target.state)
    with fresh._connect() as con:
        con.execute("INSERT INTO live_events(event_key,scheduled_at,phase,status) VALUES ('new-fact','t','auction','completed')")
    monkeypatch.setattr(binding, 'current_binding', lambda: target)
    assert original.path == reader.path == target.state
    with reader._connect() as con:
        assert con.execute('SELECT event_key FROM live_events').fetchall()[0][0] == 'new-fact'
    # 删除组件代码drop-in不影响绑定；旧入口写者追加最新事实，不回读/覆盖原库。
    with original._connect() as con:
        con.execute("INSERT INTO live_events(event_key,scheduled_at,phase,status) VALUES ('after-compensation','t','auction','completed')")
    with reader._connect() as con:
        assert con.execute('SELECT count(*) FROM live_events').fetchone()[0] == 2
    with sqlite3.connect(tmp_path / 'var/live-state.sqlite3') as con:
        assert con.execute('SELECT count(*) FROM live_events').fetchone()[0] == 0


def test_legacy_entry_redirects_defaults_but_keeps_validation_and_custom_paths(tmp_path, monkeypatch):
    from types import SimpleNamespace

    target = binding.LiveStateBinding(tmp_path / 'private')
    monkeypatch.setattr(binding, 'current_binding', lambda: target)
    def args():
        return SimpleNamespace(validation_namespace=None, state='var/live-state.sqlite3',
                               workspace='var/live', lock='var/live-core.lock', kill_switch='var/KILL')
    normal = args()
    binding.apply_entry_binding(normal)
    assert Path(normal.state) == target.state
    assert Path(normal.workspace) == target.workspace
    assert Path(normal.lock) == target.lock
    assert Path(normal.kill_switch) == target.kill
    custom = args()
    custom.state = str(tmp_path / 'custom.sqlite3')
    binding.apply_entry_binding(custom)
    assert custom.state == str(tmp_path / 'custom.sqlite3')
    validation = args()
    validation.validation_namespace = 'component-validation:explicit'
    binding.apply_entry_binding(validation)
    assert validation.state == 'var/live-state.sqlite3'
