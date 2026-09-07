import pytest

from qsys.live.kill_switch import KillSwitch
from qsys.live.transport import MemoryTransport


@pytest.fixture
def ks(tmp_path) -> KillSwitch:
    return KillSwitch(MemoryTransport(), local_path=tmp_path / "KILL")


def test_starts_disengaged(ks):
    assert ks.is_engaged() is False
    assert ks.reason() == ""


def test_engage_sets_the_flag(ks):
    ks.engage("manual stop")
    assert ks.is_engaged() is True
    assert "manual stop" in ks.reason()


def test_release_clears_the_flag(ks):
    ks.engage("x")
    ks.release()
    assert ks.is_engaged() is False


def test_local_file_alone_engages_the_switch(tmp_path):
    """不依赖 Redis 的独立路径：文件存在即停机。"""
    path = tmp_path / "KILL"
    ks = KillSwitch(MemoryTransport(), local_path=path)
    path.write_text("emergency")
    assert ks.is_engaged() is True
    assert "emergency" in ks.reason()


def test_redis_key_alone_engages_the_switch(tmp_path):
    transport = MemoryTransport()
    ks = KillSwitch(transport, local_path=tmp_path / "KILL")
    transport.set_key("control:kill", "from web")
    assert ks.is_engaged() is True
    assert "from web" in ks.reason()


def test_release_clears_both_paths(tmp_path):
    path = tmp_path / "KILL"
    transport = MemoryTransport()
    ks = KillSwitch(transport, local_path=path)
    ks.engage("x")
    assert path.exists()
    ks.release()
    assert not path.exists()
    assert transport.get_key("control:kill") is None


def test_engage_is_idempotent(ks):
    ks.engage("first")
    ks.engage("second")
    assert ks.is_engaged() is True


def test_release_when_not_engaged_is_not_an_error(ks):
    ks.release()


def test_works_without_a_local_path():
    """未配置本地路径时退化为纯 Redis 单路径，但仍可用。"""
    ks = KillSwitch(MemoryTransport(), local_path=None)
    ks.engage("x")
    assert ks.is_engaged() is True
    ks.release()
    assert ks.is_engaged() is False


def test_custom_key_is_honored(tmp_path):
    transport = MemoryTransport()
    ks = KillSwitch(transport, key="control:halt", local_path=tmp_path / "K")
    ks.engage("x")
    assert transport.get_key("control:halt") is not None
