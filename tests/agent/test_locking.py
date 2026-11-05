import pytest

from qsys.agent.locking import AlreadyRunning, single_instance


def test_same_agent_lock_cannot_be_acquired_twice(tmp_path):
    target = tmp_path / "agent.lock"
    with single_instance(target):
        with pytest.raises(AlreadyRunning):
            with single_instance(target):
                pass


def test_windows_failed_acquisition_does_not_unlock_or_append_to_held_lock(tmp_path, monkeypatch):
    import os
    import sys
    from types import SimpleNamespace
    import qsys.agent.locking as module
    calls = []

    def locking(fd, action, size):
        calls.append(action)
        raise OSError('fixture occupied')

    monkeypatch.setattr(module, 'os', SimpleNamespace(name='nt', SEEK_END=os.SEEK_END))
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(
        locking=locking, LK_NBLCK=1, LK_UNLCK=2))
    path = tmp_path / 'held.lock'
    path.write_bytes(b'0')
    with pytest.raises(AlreadyRunning):
        with module.single_instance(path):
            pytest.fail('held instance entered runtime')
    assert calls == [1]
    assert path.read_bytes() == b'0'
