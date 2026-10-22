import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from qsys.ops import reboot_drill_cli as cli
from qsys.ops import windows_reboot_baseline as baseline


def payload(**changes):
    reading = dict(boot_seconds_ago=123, session_name="console", rdp_logons=0,
                   broker_endpoint=None)
    reading.update(changes)
    return json.dumps(dict(protocol=1, reading=reading))


@pytest.mark.parametrize("raw", [
    "{}", '{"protocol":1,"reading":{}}', payload(boot_seconds_ago=-1),
    payload(boot_seconds_ago=float("nan")), payload(boot_seconds_ago=True),
    payload(rdp_logons=False), payload(rdp_logons=1.5), payload(rdp_logons=-1),
    payload(extra="unexpected"), payload(session_name=""),
    payload(broker_endpoint="x" * 257), " " * 4097,
    payload().replace('"protocol": 1', '"protocol": true'),
])
def test_missing_or_invalid_facts_are_rejected(raw):
    with pytest.raises(ValueError):
        baseline.decode_reading(raw)


def test_valid_absence_is_preserved_without_defaults():
    reading = baseline.decode_reading(payload(session_name=None))
    assert reading == dict(boot_seconds_ago=123, session_name=None,
                          rdp_logons=0, broker_endpoint=None)


@pytest.mark.parametrize("command", ["", "powershell", "shutdown /r", "internal-sftp",
                                      baseline.COMMAND + " extra", baseline.COMMAND + "\n"])
def test_forced_entry_rejects_every_other_command_before_subprocess(monkeypatch, command):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", command)
    monkeypatch.setattr(sys, "argv", ["baseline.py"])
    monkeypatch.setattr(baseline.subprocess, "run", Mock(side_effect=AssertionError("command escaped")))
    assert baseline.main() == 2


def test_forced_entry_accepts_only_fixed_read_script(monkeypatch, capsys):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", baseline.COMMAND)
    monkeypatch.setattr(sys, "argv", ["baseline.py"])
    monkeypatch.setattr(baseline, "os", SimpleNamespace(name="nt", environ={"SSH_ORIGINAL_COMMAND": baseline.COMMAND}))
    run = Mock(return_value=SimpleNamespace(stdout=payload().encode()))
    monkeypatch.setattr(baseline.subprocess, "run", run)
    assert baseline.main() == 0
    assert baseline.decode_reading(capsys.readouterr().out)["rdp_logons"] == 0
    command = run.call_args.args[0]
    import base64
    assert base64.b64decode(command[-1]).decode("utf-16le") == baseline.PROBE
    assert run.call_args.kwargs == dict(capture_output=True, timeout=28, check=True)


def test_probe_error_does_not_emit_fabricated_baseline(monkeypatch, capsys):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", baseline.COMMAND)
    monkeypatch.setattr(sys, "argv", ["baseline.py"])
    monkeypatch.setattr(baseline, "os", SimpleNamespace(name="nt", environ={"SSH_ORIGINAL_COMMAND": baseline.COMMAND}))
    monkeypatch.setattr(baseline.subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("probe", 28)))
    assert baseline.main() == 1
    assert not capsys.readouterr().out


def test_readonly_client_uses_fixed_command_and_checks_schema(monkeypatch, tmp_path):
    ssh = Mock(return_value=SimpleNamespace(returncode=0, stdout=payload().encode()))
    monkeypatch.setattr(cli, "_ssh", ssh)
    assert cli.probe_windows_readonly("example.invalid", tmp_path / "key", known_hosts=tmp_path / "hosts")["rdp_logons"] == 0
    assert ssh.call_args.args[2] == baseline.COMMAND
    ssh.return_value = SimpleNamespace(returncode=1, stdout=b"sensitive")
    with pytest.raises(RuntimeError, match="只读基线入口失败"):
        cli.probe_windows_readonly("example.invalid", tmp_path / "key", known_hosts=tmp_path / "hosts")


@pytest.mark.parametrize("args", [
    ["--execute", "--known-hosts", "/unused"], ["--dry-run"],
])
def test_readonly_cli_cannot_execute_or_use_implicit_host_trust(monkeypatch, args):
    monkeypatch.setattr(cli, "load_trading_calendar", Mock(side_effect=AssertionError("too late")))
    with pytest.raises(SystemExit) as exc:
        cli.main(["--win-transport", "readonly-baseline", *args])
    assert exc.value.code == 2


def test_readonly_dry_run_collects_real_probe_and_never_shutdown(monkeypatch, tmp_path):
    from datetime import date

    day = date(2026, 10, 31)
    monkeypatch.setattr(cli, "load_trading_calendar", lambda _: SimpleNamespace(is_trading_day=lambda _: False))
    monkeypatch.setattr(cli, "last_closed_day_of_month", lambda *_: day)
    monkeypatch.setattr(cli, "probe_windows_readonly", lambda *a, **kw: baseline.decode_reading(payload()))
    monkeypatch.setattr(cli, "probe_heartbeats", lambda _: {"agent_age": 1, "market_age": 1})
    monkeypatch.setattr(cli, "_ssh", Mock(side_effect=AssertionError("shutdown forbidden")))
    assert cli.main(["--win-transport", "readonly-baseline", "--dry-run", "--day", str(day),
                     "--known-hosts", "/unused", "--service-runs", str(tmp_path)]) == 0
    record, = tmp_path.rglob("*.json")
    data = json.loads(record.read_text())
    assert data["status"] == "skipped"
    assert "开机于" in record.read_text()
