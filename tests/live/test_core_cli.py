from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from qsys.live import core_cli
from qsys.live.config import AlertConfig, DeployConfig, SafetyConfig
from qsys.live.kill_switch import KillSwitch
from qsys.live.transport import MemoryTransport
from qsys.live.validation_transport import ValidationTransport

PREFIX = "component-validation:issue497"


class Stop(BaseException):
    pass


def isolated_args(root):
    return ["--validation-namespace", PREFIX, "--validation-root", str(root),
            "--workspace", str(root / "workspace"), "--state", str(root / "isolated/state.sqlite3"),
            "--lock", str(root / "isolated/core.lock"), "--kill-switch", str(root / "isolated/KILL"),
            "--journal-warehouse", str(root / "journal"), "--alert-ledger", str(root / "alerts")]


@pytest.mark.parametrize("mode,enabled", [
    ("disabled", False), ("disabled", True), ("shadow", False),
    ("shadow", True), ("live", False),
])
def test_live_posture_guard_precedes_transport_and_state(monkeypatch, mode, enabled):
    config = DeployConfig(safety=SafetyConfig(mode=mode, orders_enabled=enabled))
    monkeypatch.setattr(core_cli, "load_deploy_config", lambda _: config)
    forbidden = Mock(side_effect=AssertionError("guard too late"))
    monkeypatch.setattr(core_cli.RedisTransport, "from_config", forbidden)
    monkeypatch.setattr(core_cli, "LiveStore", forbidden)
    with pytest.raises(RuntimeError, match="实盘核心只在"):
        core_cli.main(["--validation-namespace", PREFIX])
    forbidden.assert_not_called()


def setup_entry(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    config = DeployConfig(safety=SafetyConfig(mode="live", orders_enabled=True))
    raw = MemoryTransport()
    monkeypatch.setattr(core_cli, "load_deploy_config", lambda _: config)
    monkeypatch.setattr(core_cli.RedisTransport, "from_config", lambda _: raw)
    monkeypatch.setattr(core_cli, "load_trading_calendar", lambda _: object())
    monkeypatch.setattr(core_cli, "resolve_strategy", lambda *a: SimpleNamespace(
        path=tmp_path / "strategy.py", params={"fixed": 7}, paper_account_id="",
    ))
    monkeypatch.setattr(core_cli.time, "sleep", lambda _: None)
    return raw


@pytest.mark.parametrize("isolated", [False, True])
def test_default_protocol_and_explicit_isolated_paths(monkeypatch, tmp_path, isolated):
    raw = setup_entry(monkeypatch, tmp_path)
    seen = []
    ledger_calls = []
    monkeypatch.setattr(core_cli, "ledger", lambda root: ledger_calls.append(root))

    def runtime(*args, **kwargs):
        seen.append((args, kwargs))
        return SimpleNamespace(heartbeats=nullcontext(), tick=lambda: (_ for _ in ()).throw(Stop()))

    monkeypatch.setattr(core_cli, "LiveRuntime", runtime)
    argv = isolated_args(tmp_path) if isolated else []
    with pytest.raises(Stop):
        core_cli.main(argv)
    args, options = seen[0]
    assert options["shadow"] is False and options["params"] == {"fixed": 7}
    if isolated:
        assert isinstance(args[4], ValidationTransport)
        assert args[4].inner is raw and args[4].namespace == PREFIX
        assert args[6].path == tmp_path / "isolated/state.sqlite3"
        assert options["local_kill_path"] == tmp_path / "isolated/KILL"
        assert options["journal_warehouse"] == tmp_path / "journal"
        assert ledger_calls == [str(tmp_path / "alerts")]
        args[4].append("cmd:orders", {"payload": "test only"})
        raw.ensure_group("cmd:orders", "outside", "0")
        assert raw.read_group("cmd:orders", "outside", "outside") == []
    else:
        assert args[4] is raw
        assert args[6].path == Path("var/live-state.sqlite3")
        assert options["local_kill_path"] == Path("var/KILL")
        assert options["journal_warehouse"] is None and ledger_calls == [None]


def test_three_error_fuse_keeps_isolated_kill_and_existing_cancel_behavior(monkeypatch, tmp_path):
    raw = setup_entry(monkeypatch, tmp_path)
    raw.set_key("control:kill", "production untouched")
    cancel = Mock()
    critical = Mock()
    local = tmp_path / "isolated/KILL"

    def runtime(*args, **kwargs):
        calls = 0

        def tick():
            nonlocal calls
            calls += 1
            if calls == 4:
                raise Stop()
            raise RuntimeError("isolated failure")

        return SimpleNamespace(
            heartbeats=nullcontext(), tick=tick,
            kill_switch=KillSwitch(args[4], key="control:kill", local_path=kwargs["local_kill_path"]),
            broker=SimpleNamespace(cancel_all=cancel), alerts=SimpleNamespace(critical=critical),
        )

    monkeypatch.setattr(core_cli, "LiveRuntime", runtime)
    with pytest.raises(Stop):
        core_cli.main(isolated_args(tmp_path))
    cancel.assert_called_once()
    critical.assert_called_once()
    assert local.is_file()
    assert raw.get_key("control:kill") == "production untouched"
    assert "three consecutive" in raw.get_key(PREFIX + ":control:kill")
    assert not (tmp_path / "var/KILL").exists()


@pytest.mark.parametrize("kind", ["missing-root", "filesystem-root", "outside-journal", "symlink-escape", "live-notification", "ops-notification"])
def test_isolation_rejects_unsafe_paths_and_notifications_before_transport(monkeypatch, tmp_path, kind):
    argv = isolated_args(tmp_path)
    alerts = AlertConfig()
    if kind == "missing-root":
        offset = argv.index("--validation-root")
        del argv[offset:offset + 2]
    elif kind == "filesystem-root":
        argv[argv.index("--validation-root") + 1] = "/"
    elif kind == "outside-journal":
        argv[argv.index("--journal-warehouse") + 1] = str(tmp_path.parent / "production")
    elif kind == "symlink-escape":
        (tmp_path / "journal").symlink_to(tmp_path.parent)
    elif kind == "live-notification":
        alerts = AlertConfig(webhook_url="https://example.invalid/hook")
    else:
        alerts = AlertConfig(ops_webhook_url="https://example.invalid/hook")
    config = DeployConfig(safety=SafetyConfig(mode="live", orders_enabled=True), alerts=alerts)
    monkeypatch.setattr(core_cli, "load_deploy_config", lambda _: config)
    forbidden = Mock(side_effect=AssertionError("isolation guard too late"))
    monkeypatch.setattr(core_cli.RedisTransport, "from_config", forbidden)
    monkeypatch.setattr(core_cli, "LiveStore", forbidden)
    with pytest.raises(SystemExit) as exc:
        core_cli.main(argv)
    assert exc.value.code == 2
    forbidden.assert_not_called()


def test_held_instance_lock_prevents_all_runtime_initialization(monkeypatch, tmp_path):
    from qsys.agent.locking import AlreadyRunning, single_instance

    lock = tmp_path / "live-core.lock"
    config = DeployConfig(safety=SafetyConfig(mode="live", orders_enabled=True))
    monkeypatch.setattr(core_cli, "load_deploy_config", lambda _: config)
    forbidden = Mock(side_effect=AssertionError("duplicate instance initialized before locking"))
    monkeypatch.setattr(core_cli.RedisTransport, "from_config", forbidden)
    for name in ("resolve_strategy", "LiveStore", "LiveRuntime"):
        monkeypatch.setattr(core_cli, name, forbidden)
    with single_instance(lock):
        with pytest.raises(AlreadyRunning):
            core_cli.main(["--lock", str(lock)])
    forbidden.assert_not_called()
