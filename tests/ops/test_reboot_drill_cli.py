"""危险命令全部 fake；任何未声明网络调用会使测试直接失败。"""
from datetime import date
from types import SimpleNamespace
import subprocess
from unittest.mock import Mock

import pytest

from qsys.ops import reboot_drill_cli as cli
from qsys.ops.services import ServiceRun

SSH = cli._ssh
DAY = date(2026, 10, 31)
HOST = "operator@example.invalid"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(cli, "_today", lambda: DAY)
    for name in ("_ssh", "probe_windows", "probe_windows_readonly", "probe_heartbeats", "_alerts",
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
    monkeypatch.setattr(cli, "probe_windows", lambda *_, **kw: {})
    monkeypatch.setattr(cli, "probe_heartbeats", lambda *_, **kw: {})
    assert cli.main(["--dry-run", "--day", simulated.isoformat(),
                     "--service-runs", str(tmp_path)]) == 0
    assert "没有重启" in capsys.readouterr().out
    records = list(tmp_path.rglob("*.json"))
    assert len(records) == 1
    assert "取不到开机时刻" in records[0].read_text()
    import json
    assert json.loads(records[0].read_text())["status"] == "skipped"


def args():
    return SimpleNamespace(execute=True, dry_run=False, approved_day=DAY,
                           approved_host=HOST, host=HOST, day=None,
                           key="unused", config="unused", known_hosts=None, alert_ledger=None)


@pytest.mark.parametrize("response", [
    SimpleNamespace(returncode=1),
    OSError("sensitive stderr must not escape"),
    subprocess.TimeoutExpired("ssh", 30),
])
def test_shutdown_failure_stops_without_wait_or_retry(monkeypatch, response):
    ssh = Mock(side_effect=response) if isinstance(response, Exception) else Mock(return_value=response)
    monkeypatch.setattr(cli, "_ssh", ssh)
    monkeypatch.setattr(cli, "probe_windows", lambda *_, **kw: {})
    monkeypatch.setattr(cli, "probe_heartbeats", lambda *_, **kw: {})
    bus = Mock()
    monkeypatch.setattr(cli, "_alerts", lambda *_: bus)
    with ServiceRun.open(None, "reboot-drill") as run:
        assert cli._run(args(), DAY, "test", run) == 1
    assert run.status == "failed"
    assert len(ssh.call_args_list) == 1
    assert "shutdown /r" in ssh.call_args.args[2]
    assert len(run.steps) == 3
    bus.critical.assert_called_once()
    assert "sensitive" not in str(bus.critical.call_args)


def test_confirmation_rechecked_after_baseline(monkeypatch):
    monkeypatch.setattr(cli, "probe_windows", lambda *_, **kw: {})
    def cross_midnight(_):
        monkeypatch.setattr(cli, "_today", lambda: date(2026, 11, 1))
        return {}
    monkeypatch.setattr(cli, "probe_heartbeats", cross_midnight)
    with ServiceRun.open(None, "reboot-drill") as run:
        assert cli._run(args(), DAY, "test", run) == 1
    assert run.status == "failed"
    assert "确认已失效" in run.steps[-1].detail


def test_accepted_shutdown_continues_to_recovery(monkeypatch):
    monkeypatch.setattr(cli, "probe_windows", lambda *_, **kw: {
        "boot_seconds_ago": 60, "session_name": "console", "rdp_logons": 0,
        "broker_endpoint": "example.invalid:1"})
    monkeypatch.setattr(cli, "probe_heartbeats", lambda *_, **kw: {"agent_age": 10, "market_age": 10})
    ssh = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(cli, "_ssh", ssh)
    cli.time.sleep = Mock()
    bus = Mock()
    monkeypatch.setattr(cli, "_alerts", lambda *_: bus)
    with ServiceRun.open(None, "reboot-drill") as run:
        assert cli._run(args(), DAY, "test", run) == 0
    assert run.status == "succeeded"
    assert ssh.call_count == 2
    bus.info.assert_called_once()
    bus.critical.assert_not_called()


def test_legacy_script_uses_same_guarded_entry():
    from scripts import reboot_drill
    assert reboot_drill.main is cli.main


def test_ssh_pins_readonly_hosts_and_identity(monkeypatch, tmp_path):
    subprocess_run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(cli.subprocess, "run", subprocess_run)
    hosts = tmp_path / "known_hosts"
    SSH(HOST, tmp_path / "key", "echo up", known_hosts=hosts)
    command = subprocess_run.call_args.args[0]
    assert "StrictHostKeyChecking=yes" in command
    assert "IdentitiesOnly=yes" in command
    assert "IdentityAgent=none" in command
    assert f"UserKnownHostsFile={hosts}" in command
    assert command[-2:] == [HOST, "echo up"]
    assert subprocess_run.call_args.kwargs["timeout"] == 30


def test_custom_paths_reach_baseline_recovery_and_alerts(monkeypatch, tmp_path):
    calendar(monkeypatch)
    hosts = tmp_path / "known_hosts"
    ledger = tmp_path / "alerts"
    probes = Mock(return_value={"boot_seconds_ago": 60, "session_name": "console",
                                "rdp_logons": 0, "broker_endpoint": "example.invalid:1"})
    monkeypatch.setattr(cli, "probe_windows", probes)
    monkeypatch.setattr(cli, "probe_heartbeats", lambda *_: {"agent_age": 1, "market_age": 1})
    ssh = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(cli, "_ssh", ssh)
    cli.time.sleep = Mock()
    alerts = Mock(return_value=Mock())
    monkeypatch.setattr(cli, "_alerts", alerts)
    assert cli.main(execution("--known-hosts", str(hosts), "--alert-ledger", str(ledger),
                              "--service-runs", str(tmp_path / "runs"))) == 0
    assert probes.call_count == 2
    assert all(c.kwargs["known_hosts"] == hosts for c in probes.call_args_list)
    assert all(c.kwargs["known_hosts"] == hosts for c in ssh.call_args_list)
    assert alerts.call_args.args[1] == ledger


@pytest.mark.parametrize('entry', ['shared-unit', 'component-spec'])
@pytest.mark.parametrize('branch', ['trading-day', 'other-closed-day', 'monthly-day'])
def test_timer_entrypoints_use_explicit_dry_run_and_preserve_calendar_gates(
    entry, branch, monkeypatch, tmp_path,
):
    """将受 Git 管理的真实 ExecStart 交给 CLI，而不只断言 unit 文本。"""
    import json
    from pathlib import Path
    import shlex

    if entry == 'shared-unit':
        text = Path('deploy/systemd/qsys-reboot-drill.service').read_text()
        logical = text.replace('\\\n', '')
        command = next(line.removeprefix('ExecStart=') for line in logical.splitlines()
                       if line.startswith('ExecStart='))
        argv = shlex.split(command)
        argv = argv[argv.index('scripts/reboot_drill.py') + 1:]
    else:
        spec = json.loads(Path('deploy/component-releases.json').read_text())
        # 复用正式制品规格的 ExecStart，确认与共享入口遵循同一 CLI 契约。
        lines = spec['components']['reboot-drill']['dropin_lines']
        command = next(line.removeprefix('ExecStart=') for line in lines
                       if line.startswith('ExecStart=') and line != 'ExecStart=')
        argv = shlex.split(command)[1:]
    assert '--dry-run' in argv
    assert not {'--execute', '--force', '--day', '--approved-day', '--approved-host'} & set(argv)
    runs = tmp_path / 'runs'
    argv[argv.index('--service-runs') + 1] = str(runs)
    calendar(monkeypatch, trading=branch == 'trading-day',
             drill_day=DAY if branch == 'monthly-day' else date(2026, 10, 30))
    windows = Mock(return_value={'boot_seconds_ago': 3600})
    beats = Mock(return_value={'agent_age': 1, 'market_age': 1})
    monkeypatch.setattr(cli, 'probe_windows', windows)
    monkeypatch.setattr(cli, 'probe_windows_readonly', windows)
    monkeypatch.setattr(cli, 'probe_heartbeats', beats)
    assert cli.main(argv) == 0
    records = list(runs.rglob('*.json'))
    if branch == 'monthly-day':
        windows.assert_called_once()
        beats.assert_called_once()
        assert len(records) == 1
        record = json.loads(records[0].read_text())
        assert record['status'] == 'skipped'
        assert '未执行重启演练' in records[0].read_text()
    else:
        windows.assert_not_called()
        beats.assert_not_called()
        assert records == []  # 交易日/非执行日只记退出日志，不伪造基线或演练结果。
