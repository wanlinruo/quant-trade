"""顶栏状态只读接口（#662，设计文档 §5、§10）。"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from fastapi.testclient import TestClient

from qsys.webapp.app import WebappDeps, create_app
from qsys.webapp.config import WebConfig
from qsys.webapp.live_kill_status import KillSource, LiveKillReader, LiveKillStatus
from qsys.webapp.contracts.shell import ShellStatus
from qsys.webapp.queries.shell import ShellQuery

AT = "2026-11-17T10:00:00"


def _status(state: str, valid_for: float = 10.0, agent_at: str | None = AT) -> LiveKillStatus:
    return LiveKillStatus(
        state=state, label=f"label-{state}", message=f"msg-{state}", checked_at=AT,
        valid_for_seconds=valid_for,
        sources=(
            KillSource("linux", "Linux 本地停机闸", state, "r"),
            KillSource("redis", "Redis 实盘停机闸", state, "r"),
            KillSource("agent", "Windows 实盘 agent", state, "r", agent_at),
        ),
    )


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class CountingReader:
    def __init__(self, status: LiveKillStatus | None = None, error: Exception | None = None) -> None:
        self.status = status or _status("clear")
        self.error = error
        self.calls = 0
        self._lock = threading.Lock()

    def read(self, *, now: datetime | None = None) -> LiveKillStatus:
        with self._lock:
            self.calls += 1
        if self.error is not None:
            raise self.error
        return self.status


def test_status_maps_kill_states_to_live_posture() -> None:
    expected = {"stopped": ("reduce_only", "仅允许减仓"), "clear": ("trading", "实盘运行"),
                "unknown": ("unknown", "实盘状态未知")}
    for kill, (live, label) in expected.items():
        body = ShellQuery(CountingReader(_status(kill))).get().model_dump(mode="json")
        assert body["schema_version"] == 1
        assert body["kill"]["state"] == kill
        assert body["live"] == {"state": live, "label": label}


def test_unknown_kill_state_is_never_reported_clear() -> None:
    body = ShellQuery(CountingReader(error=RuntimeError("redis down /var/secret"))).get().model_dump(mode="json")
    assert body["kill"]["state"] == "unknown"
    assert body["kill"]["label"] == "停机状态未知"
    assert body["live"]["state"] == "unknown"
    assert "secret" not in json.dumps(body, ensure_ascii=False)


def test_concurrent_requests_share_one_read_within_ttl() -> None:
    reader = CountingReader()
    clock = FakeClock()
    cache = ShellQuery(reader, ttl_seconds=5, clock=clock)
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda _: cache.get(), range(32)))
    assert reader.calls == 1
    clock.advance(5.01)
    cache.get()
    assert reader.calls == 2


def test_cache_never_outlives_kill_status_validity() -> None:
    # 心跳快过期时读取结果只剩 1 秒有效，缓存不得在其失效后继续返回“已解除”。
    reader = CountingReader(_status("clear", valid_for=1.0))
    clock = FakeClock()
    cache = ShellQuery(reader, ttl_seconds=5, clock=clock)
    cache.get()
    clock.advance(1.01)
    cache.get()
    assert reader.calls == 2


def test_failed_read_is_not_cached_as_clear_later() -> None:
    reader = CountingReader(error=RuntimeError("x"))
    clock = FakeClock()
    cache = ShellQuery(reader, ttl_seconds=5, clock=clock)
    assert cache.get().kill.state == "unknown"
    reader.error = None
    clock.advance(5.01)
    assert cache.get().kill.state == "clear"


def test_agent_heartbeat_taken_from_agent_source() -> None:
    got = ShellQuery(CountingReader(_status("clear", agent_at=AT))).get().model_dump(mode="json")
    assert got["agent_heartbeat_at"] == AT + "+08:00"
    assert ShellQuery(CountingReader(_status("unknown", agent_at=None))).get().agent_heartbeat_at is None


def test_shell_missing_reasons_in_missing_map() -> None:
    body = ShellQuery(CountingReader()).get()
    assert body.qmt is None and body.missing["qmt"]
    assert body.alerts_open is None and body.missing["alerts_open"]
    assert set(body.model_dump()) == set(ShellStatus.model_fields)


def test_shell_times_carry_offset() -> None:
    body = ShellQuery(CountingReader()).get().model_dump(mode="json")
    assert body["checked_at"].endswith("+08:00")
    assert body["kill"]["sources"][0]["recorded_at"] is None or body["kill"]["sources"][0]["recorded_at"].endswith("+08:00")


def test_endpoint_is_no_store_and_escapes_nothing_sensitive(warehouse, tmp_path) -> None:
    kill_root = tmp_path / "live-state-root"
    cfg = WebConfig()
    app = create_app(WebappDeps(live_kill=LiveKillReader(None, kill_root)), cfg)
    r = TestClient(app).get("/api/app/shell/status")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["kill"]["state"] == "unknown"  # 没有 transport，不能显示已解除
    assert str(kill_root) not in r.text
    assert "live-state-root" not in r.text
