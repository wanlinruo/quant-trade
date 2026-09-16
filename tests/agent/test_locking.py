import pytest

from qsys.agent.locking import AlreadyRunning, single_instance


def test_same_agent_lock_cannot_be_acquired_twice(tmp_path):
    target = tmp_path / "agent.lock"
    with single_instance(target):
        with pytest.raises(AlreadyRunning):
            with single_instance(target):
                pass
