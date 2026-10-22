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
