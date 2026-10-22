import pytest

from qsys.live.kill_switch import KillSwitch
from qsys.live.transport import MemoryTransport
from qsys.live.validation_transport import ValidationTransport

PREFIX = "component-validation:issue493"


@pytest.mark.parametrize("namespace", ["", "production", "shadow", "component-validation:", "component-validation:../prod", "component-validation:x\n", "component-validation:" + "x" * 97])
def test_invalid_namespace_is_rejected(namespace):
    with pytest.raises(ValueError):
        ValidationTransport(MemoryTransport(), namespace)


def test_hardcoded_kill_and_intents_cannot_touch_production(tmp_path):
    raw = MemoryTransport()
    raw.set_key("control:kill", "live-existing")
    raw.set_key("shadow:control:kill", "shadow-existing")
    raw.ensure_group("shadow:intents", "production", "0")
    transport = ValidationTransport(raw, PREFIX)
    kill = KillSwitch(transport, key="shadow:control:kill", local_path=tmp_path / "kill")
    assert not kill.is_engaged()
    kill.engage("validation only")
    assert kill.is_engaged()
    assert raw.get_key("control:kill") == "live-existing"
    assert raw.get_key("shadow:control:kill") == "shadow-existing"
    assert raw.get_key(PREFIX + ":shadow:control:kill")
    kill.release()
    assert not kill.is_engaged()
    assert raw.get_key("shadow:control:kill") == "shadow-existing"
    transport.append("shadow:intents", {"symbol": "600000.SH"})
    assert raw.read_group("shadow:intents", "production", "observer") == []


def test_stream_delivery_pending_ack_and_reset_remain_in_same_namespace():
    raw = MemoryTransport()
    transport = ValidationTransport(raw, PREFIX)
    transport.ensure_group("quotes", "workers", "0")
    first = transport.append("quotes", {"request_id": "one", "payload": "untouched"})
    second = transport.append("quotes", {"request_id": "two"})
    rows = transport.read_group("quotes", "workers", "worker", count=1)
    assert [(r.id, r.payload) for r in rows] == [(first, {"request_id": "one", "payload": "untouched"})]
    assert [r.id for r in transport.pending("quotes", "workers")] == [first]
    transport.ack("quotes", "workers", first)
    assert transport.pending("quotes", "workers") == []
    transport.reset_group("quotes", "workers")
    assert transport.read_group("quotes", "workers", "worker") == []
    third = transport.append("quotes", {"request_id": "three"})
    assert [r.id for r in transport.read_group("quotes", "workers", "worker")] == [third]
    assert first != second != third
    raw.ensure_group("quotes", "outside", "0")
    assert raw.read_group("quotes", "outside", "outside") == []


def test_values_are_preserved_and_delete_is_local():
    raw = MemoryTransport()
    raw.set_key("state:account", "production")
    transport = ValidationTransport(raw, PREFIX)
    assert transport.get_key("state:account") is None
    transport.set_key("state:account", '{"cash": 1.23, "unknown": null}')
    assert transport.get_key("state:account") == '{"cash": 1.23, "unknown": null}'
    transport.delete_key("state:account")
    assert transport.get_key("state:account") is None
    assert raw.get_key("state:account") == "production"


@pytest.mark.parametrize("mode,enabled", [("live", True), ("live", False), ("shadow", True), ("disabled", False)])
def test_shadow_entry_rejects_production_posture_before_any_transport(
    tmp_path, monkeypatch, mode, enabled,
):
    import sys
    from qsys.live import shadow_cli
    from qsys.live.config import DeployConfig, SafetyConfig

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["shadow", "--validation-namespace", PREFIX])
    monkeypatch.setattr(shadow_cli, "load_deploy_config", lambda path: DeployConfig(safety=SafetyConfig(mode=mode, orders_enabled=enabled)))
    monkeypatch.setattr(shadow_cli.RedisTransport, "from_config", lambda cfg: pytest.fail("拒绝姿态不得构造Redis传输"))
    with pytest.raises(RuntimeError, match="影子核心只在"):
        shadow_cli.main()


@pytest.mark.parametrize("namespace", [None, PREFIX])
def test_shadow_entry_passes_wrapped_or_legacy_transport_and_always_shadow(
    tmp_path, monkeypatch, namespace,
):
    import sys
    from contextlib import nullcontext
    from types import SimpleNamespace
    from qsys.core.calendar import AShareCalendar
    from qsys.live import shadow_cli
    from qsys.live.config import DeployConfig, SafetyConfig
    from qsys.live.lineage import StrategySource

    class Stop(BaseException):
        pass

    monkeypatch.chdir(tmp_path)
    args = ["shadow"] + (["--validation-namespace", namespace] if namespace else [])
    monkeypatch.setattr(sys, "argv", args)
    cfg = DeployConfig(safety=SafetyConfig(mode="shadow", orders_enabled=False))
    raw = MemoryTransport()
    seen = []
    monkeypatch.setattr(shadow_cli, "load_deploy_config", lambda path: cfg)
    monkeypatch.setattr(shadow_cli.RedisTransport, "from_config", lambda config: raw)
    monkeypatch.setattr(shadow_cli, "load_trading_calendar", lambda path: AShareCalendar([]))
    monkeypatch.setattr(shadow_cli, "resolve_strategy", lambda *a: StrategySource(tmp_path / "strategy.py"))

    def runtime(*args, **kwargs):
        seen.append((args[4], kwargs))
        return SimpleNamespace(heartbeats=nullcontext(), tick=lambda: (_ for _ in ()).throw(Stop()))

    monkeypatch.setattr(shadow_cli, "LiveRuntime", runtime)
    with pytest.raises(Stop):
        shadow_cli.main()
    transport, options = seen[0]
    assert options["shadow"] is True
    if namespace:
        assert isinstance(transport, ValidationTransport) and transport.namespace == namespace
    else:
        assert transport is raw


@pytest.mark.parametrize("namespace", [None, PREFIX])
def test_shadow_held_instance_lock_prevents_initialization(tmp_path, monkeypatch, namespace):
    import sys
    from unittest.mock import Mock
    from qsys.agent.locking import AlreadyRunning, single_instance
    from qsys.live import shadow_cli
    from qsys.live.config import DeployConfig, SafetyConfig

    lock = tmp_path / "shadow-core.lock"
    argv = ["shadow", "--lock", str(lock)]
    if namespace:
        argv += ["--validation-namespace", namespace]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(shadow_cli, "load_deploy_config", lambda _: DeployConfig(
        safety=SafetyConfig(mode="shadow", orders_enabled=False)))
    forbidden = Mock(side_effect=AssertionError("duplicate initialized before locking"))
    monkeypatch.setattr(shadow_cli.RedisTransport, "from_config", forbidden)
    for name in ("resolve_strategy", "LiveStore", "LiveRuntime", "ledger", "strategy_sinks"):
        monkeypatch.setattr(shadow_cli, name, forbidden)
    with single_instance(lock):
        with pytest.raises(AlreadyRunning):
            shadow_cli.main()
    forbidden.assert_not_called()
    assert not (tmp_path / "var").exists()
