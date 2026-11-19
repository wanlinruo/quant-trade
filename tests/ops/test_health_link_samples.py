"""链路采样时间序列（#662 计划 03b）：健康观察每轮记录 agent / market / executor 的心跳年龄与新鲜度。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from qsys.live.messages import Heartbeat
from qsys.live.transport import MemoryTransport
from qsys.ops.health import HealthStore, HealthStoreReader, LinkSample
from qsys.ops.health_observer_cli import link_rows

NOW = datetime(2026, 11, 18, 10, 0, 0)
PREFIX = "state:heartbeat"


def transport(agent_age=1.0, market_age=2.0, executor_lag=1.5, **agent_kw):
    t = MemoryTransport()
    at = NOW - timedelta(seconds=agent_age)
    t.set_key(f"{PREFIX}:agent", Heartbeat(
        source="agent", at=at, expected_interval_s=1.0, trading_mode="live", local_kill_engaged=False,
        executor_epoch="E1", executor_latest_seq=9, executor_ring_full=False,
        executor_heartbeat_at=None if executor_lag is None else at - timedelta(seconds=executor_lag),
        **agent_kw).model_dump_json())
    t.set_key(f"{PREFIX}:market", Heartbeat(
        source="market", at=NOW - timedelta(seconds=market_age), expected_interval_s=15.0,
        order_book_ratio=0.98).model_dump_json())
    return t


def by_source(rows):
    return {r.source: r for r in rows}


def test_link_rows_from_heartbeats():
    rows = by_source(link_rows(transport(), PREFIX, NOW))
    assert set(rows) == {"agent", "market", "executor"}
    a, m = rows["agent"], rows["market"]
    assert (a.age_s, a.fresh, a.threshold_s) == (1.0, True, 10.0)
    assert a.details == {"trading_mode": "live", "local_kill_engaged": False}
    assert (m.age_s, m.fresh, m.threshold_s) == (2.0, True, 45.0) and m.details == {"order_book_ratio": 0.98}
    assert a.beat_at == NOW - timedelta(seconds=1)


def test_executor_age_uses_agent_clock():
    e = by_source(link_rows(transport(agent_age=30, executor_lag=1.5), PREFIX, NOW))["executor"]
    # agent 心跳本身已陈旧 30 秒，但执行器年龄按网关同一时钟计算：只差 1.5 秒
    assert (e.age_s, e.fresh, e.threshold_s) == (1.5, True, 5.0)
    assert e.details == {"epoch": "E1", "latest_seq": 9, "ring_full": False}
    stale = by_source(link_rows(transport(executor_lag=8), PREFIX, NOW))["executor"]
    assert stale.fresh is False
    unknown = by_source(link_rows(transport(executor_lag=None), PREFIX, NOW))["executor"]
    assert (unknown.age_s, unknown.fresh, unknown.beat_at) == (None, None, None)


def test_redis_down_records_unknown_rows():
    class Down:
        def get_key(self, key):
            raise ConnectionError("tunnel down")
    rows = link_rows(Down(), PREFIX, NOW)
    assert [r.source for r in rows] == ["agent", "market", "executor"]
    assert all(r.age_s is None and r.fresh is None and r.beat_at is None for r in rows)
    assert rows[0].details == {"error": "ConnectionError"}
    missing = by_source(link_rows(MemoryTransport(), PREFIX, NOW))
    assert all(r.fresh is None for r in missing.values())


def test_store_records_and_prunes_48h(tmp_path):
    store = HealthStore(tmp_path / "h.sqlite3")
    old = NOW - timedelta(hours=49)
    store.record_link_samples(link_rows(transport(), PREFIX, old), now=old)
    store.record_link_samples(link_rows(transport(), PREFIX, NOW), now=NOW)
    rows = store.link_samples(since=NOW - timedelta(days=7))
    assert len(rows) == 3 and {r["source"] for r in rows} == {"agent", "market", "executor"}
    assert rows[0]["sampled_at_utc"] == "2026-11-18T02:00:00+00:00"  # 无时区按上海时间解释
    reader = HealthStoreReader((tmp_path / "h.sqlite3",))
    assert len(reader.link_samples(since=NOW - timedelta(hours=1))) == 3


def test_reader_on_old_db_returns_empty(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE health_observations (id INTEGER PRIMARY KEY)")
    assert HealthStoreReader((path,)).link_samples(since=NOW) == []
    assert HealthStore(path, initialize=False, read_only=True).link_samples(since=NOW) == []


def test_record_failure_does_not_raise(tmp_path):
    store = HealthStore(tmp_path / "h.sqlite3")
    (tmp_path / "h.sqlite3").unlink()
    (tmp_path / "h.sqlite3").mkdir()  # 库路径变成目录：写入必然失败
    store.record_link_samples([LinkSample("agent", None, None, None, None, {})], now=NOW)


def test_observer_loop_records_link_samples_each_cycle():
    """主循环在有 Redis 只读通道时每轮写一次链路采样（源码约束，避免接线遗漏）。"""
    import inspect

    from qsys.ops import health_observer_cli
    source = inspect.getsource(health_observer_cli.main)
    assert "store.record_link_samples(link_rows(transport, deploy.streams.heartbeat, now), now=now)" in source
