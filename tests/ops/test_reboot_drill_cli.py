"""危险命令全部 fake；任何未声明网络调用会使测试直接失败。"""
from datetime import date
from types import SimpleNamespace
import subprocess
from unittest.mock import Mock

import pytest

from qsys.ops import reboot_drill_cli as cli
from qsys.ops.services import ServiceRun

DAY = date(2026, 10, 31)
HOST = "operator@example.invalid"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(cli, "_today", lambda: DAY)
    for name in ("_ssh", "probe_windows", "probe_heartbeats", "_alerts",
                 "load_trading_calendar"):
        monkeypatch.setattr(cli, name, Mock(side_effect=AssertionError(f"unexpected {name}")))
    # 只替换本模块 time 引用，避免修改 pytest 或其他模块的全局时钟。
    monkeypatch.setattr(cli, "time", SimpleNamespace(
        monotonic=Mock(return_value=0), sleep=Mock(side_effect=AssertionError("unexpected wait"))))


def execution(*extra):
    return ["--execute", "--host", HOST, "--approved-host", HOST,
            "--approved-day", DAY.isoformat(), *extra]


@pytest.mark.parametrize("argv", [
    [], ["--force"], ["--execute"], ["--execute", "--force"],
    ["--execute", "--dry-run"],
    execution("--approved-day", "2026-10-30"),
    execution("--approved-host", "other@example.invalid"),
    execution("--day", "2026-10-30"),
    ["--dry-run", "--approved-day", DAY.isoformat()],
    ["--dry-run", "--approved-host", HOST],
    ["--dry-run", "--host=-oProxyCommand=bad"],
])
def test_reject_before_any_config_or_network(argv):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 2


def calendar(monkeypatch, trading=False, drill_day=DAY):
    monkeypatch.setattr(cli, "load_trading_calendar",
                        lambda _: SimpleNamespace(is_trading_day=lambda _: trading))
    monkeypatch.setattr(cli, "last_closed_day_of_month", lambda *_: drill_day)


def test_force_cannot_bypass_trading_day(monkeypatch):
    calendar(monkeypatch, trading=True)
    assert cli.main(execution("--force")) == 0


def test_non_monthly_day_skips_even_when_confirmed(monkeypatch):
    calendar(monkeypatch, drill_day=date(2026, 10, 30))
    assert cli.main(execution()) == 0


def test_force_only_bypasses_monthly_date(monkeypatch, tmp_path):
    calendar(monkeypatch, drill_day=date(2026, 10, 30))
    run = Mock(return_value=7)
    monkeypatch.setattr(cli, "_run", run)
    assert cli.main(execution("--force", "--service-runs", str(tmp_path))) == 7
    assert run.call_args.args[1] == DAY


def test_dry_run_with_simulated_day_never_reboots(monkeypatch, tmp_path, capsys):
    simulated = date(2026, 10, 30)
    calendar(monkeypatch, drill_day=simulated)
    monkeypatch.setattr(cli, "probe_windows", lambda *_: {})
    monkeypatch.setattr(cli, "probe_heartbeats", lambda *_: {})
    assert cli.main(["--dry-run", "--day", simulated.isoformat(),
                     "--service-runs", str(tmp_path)]) == 0
    assert "没有重启" in capsys.readouterr().out
    records = list(tmp_path.rglob("*.json"))
    assert len(records) == 1
    assert "取不到开机时刻" in records[0].read_text()


def args():
    return SimpleNamespace(execute=True, dry_run=False, approved_day=DAY,
                           approved_host=HOST, host=HOST, day=None,
                           key="unused", config="unused")


@pytest.mark.parametrize("response", [
    SimpleNamespace(returncode=1),
    OSError("sensitive stderr must not escape"),
    subprocess.TimeoutExpired("ssh", 30),
])
def test_shutdown_failure_stops_without_wait_or_retry(monkeypatch, response):
    ssh = Mock(side_effect=response) if isinstance(response, Exception) else Mock(return_value=response)
    monkeypatch.setattr(cli, "_ssh", ssh)
    monkeypatch.setattr(cli, "probe_windows", lambda *_: {})
    monkeypatch.setattr(cli, "probe_heartbeats", lambda *_: {})
    bus = Mock()
    monkeypatch.setattr(cli, "_alerts", lambda _: bus)
    with ServiceRun.open(None, "reboot-drill") as run:
        assert cli._run(args(), DAY, "test", run) == 1
    assert run.status == "failed"
    assert len(ssh.call_args_list) == 1
    assert "shutdown /r" in ssh.call_args.args[2]
    assert len(run.steps) == 3
    bus.critical.assert_called_once()
    assert "sensitive" not in str(bus.critical.call_args)


def test_confirmation_rechecked_after_baseline(monkeypatch):
    monkeypatch.setattr(cli, "probe_windows", lambda *_: {})
    def cross_midnight(_):
        monkeypatch.setattr(cli, "_today", lambda: date(2026, 11, 1))
        return {}
    monkeypatch.setattr(cli, "probe_heartbeats", cross_midnight)
    with ServiceRun.open(None, "reboot-drill") as run:
        assert cli._run(args(), DAY, "test", run) == 1
    assert run.status == "failed"
    assert "确认已失效" in run.steps[-1].detail


def test_accepted_shutdown_continues_to_recovery(monkeypatch):
    monkeypatch.setattr(cli, "probe_windows", lambda *_: {
        "boot_seconds_ago": 60, "session_name": "console", "rdp_logons": 0,
        "broker_endpoint": "example.invalid:1"})
    monkeypatch.setattr(cli, "probe_heartbeats", lambda *_: {"agent_age": 10, "market_age": 10})
    ssh = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(cli, "_ssh", ssh)
    cli.time.sleep = Mock()
    bus = Mock()
    monkeypatch.setattr(cli, "_alerts", lambda _: bus)
    with ServiceRun.open(None, "reboot-drill") as run:
        assert cli._run(args(), DAY, "test", run) == 0
    assert run.status == "succeeded"
    assert ssh.call_count == 2
    bus.info.assert_called_once()
    bus.critical.assert_not_called()


def test_legacy_script_uses_same_guarded_entry():
    from scripts import reboot_drill
    assert reboot_drill.main is cli.main
