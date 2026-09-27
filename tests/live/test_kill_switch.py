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


# ---- Redis 不可达（2026-09-27 故障演练发现）----

class BrokenRedis(MemoryTransport):
    """连不上的 Redis：真实的 RedisTransport 不吞异常，redis-py 直接抛。

    以前测试一律用 MemoryTransport，它从不抛错，于是整类故障被测试盖住了。
    """

    def get_key(self, key):
        raise ConnectionError("Redis 不可达")

    def set_key(self, key, value):
        raise ConnectionError("Redis 不可达")

    def delete_key(self, key):
        raise ConnectionError("Redis 不可达")


@pytest.fixture
def broken(tmp_path) -> KillSwitch:
    return KillSwitch(BrokenRedis(), local_path=tmp_path / "KILL")


def test_engage_still_writes_the_local_mark_when_redis_is_down(broken, tmp_path):
    """核心熔断停机时，坏的往往正是 Redis；先写 Redis 会让本地标记永远写不下去。"""
    broken.engage("三次连续失败熔断")

    assert (tmp_path / "KILL").exists()
    assert "三次连续失败熔断" in (tmp_path / "KILL").read_text()


def test_a_local_mark_still_stops_trading_when_redis_is_down(tmp_path):
    """停机的本地路径本就不该依赖任何服务——这是这个模块存在的理由。"""
    (tmp_path / "KILL").write_text("人工放置")
    switch = KillSwitch(BrokenRedis(), local_path=tmp_path / "KILL")

    assert switch.is_engaged() is True
    assert "人工放置" in switch.reason()


def test_an_unreachable_redis_counts_as_engaged(broken):
    """读不到就按停机算：宁可误停，不可停不下来。"""
    assert broken.is_engaged() is True
    assert "Redis 不可达" in broken.reason()


def test_release_clears_the_local_mark_even_when_redis_is_down(broken, tmp_path):
    broken.engage("x")
    broken.release()

    assert not (tmp_path / "KILL").exists()
