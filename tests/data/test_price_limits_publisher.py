"""验证限定发布能力、原始 payload 和旧消费者键协议。"""

import io
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from qsys.data import price_limits_publisher as p
from qsys.data.pipeline.premarket import publish_key
from qsys.live.config import StreamConfig


def request(value='{"600000.SH": [10.0, null], "900001.BJ": [0, -1]}', **changes):
    return json.dumps({"protocol": 1, "day": "2026-10-21", "value": value,
                       **changes}).encode()


def test_original_values_and_legacy_key_are_preserved():
    raw = request()
    day, value, rows = p.validate_request(raw)
    assert value == json.loads(raw)["value"]
    assert rows == 2
    streams = StreamConfig(price_limits="validation:limits")
    assert p._key(streams, day) == publish_key(streams, day)


@pytest.mark.parametrize("raw", [
    b"", b"x" * (p.MAX_BYTES + 1), b"[]", b"null",
    b'{"protocol":1,"protocol":1,"day":"2026-10-21","value":"{}"}',
    request(command="GET"), request(key="cmd:orders"), request(protocol=True),
    request(protocol=2), request(day="20261021"), request(day="2026-02-30"),
    request(day=None), request(value={}), request(value="{}"), request(value="[]"),
    request(value='{"600000.SH":[1,2],"600000.SH":[3,4]}'),
    request(value='{"x;rm -rf /":[1,2]}'),
    request(value='{"600000.SH":[1]}'), request(value='{"600000.SH":[true,2]}'),
    request(value='{"600000.SH":[NaN,2]}'), request(value='{"600000.SH":[Infinity,2]}'),
    request(value='{"600000.SH":["10",2]}'),
])
def test_invalid_requests_cannot_reach_redis(raw):
    with pytest.raises((ValueError, UnicodeError)):
        p.validate_request(raw)


def test_symbol_count_budget(monkeypatch):
    monkeypatch.setattr(p, "MAX_SYMBOLS", 1)
    with pytest.raises(ValueError):
        p.validate_request(request())


def test_client_uses_only_pinned_loopback_ssh_and_checks_ack(monkeypatch):
    calls = []
    day, value, rows = p.validate_request(request())

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        assert p.validate_request(kwargs["input"]) == (day, value, rows)
        return SimpleNamespace(returncode=0, stdout=json.dumps(p.acknowledgement(day, value, rows)).encode())

    monkeypatch.setattr(subprocess, "run", run)
    client = p.SshPriceLimitsPublisher(Path("/private/key"), Path("/private/hosts"), StreamConfig())
    client.set_key("state:price_limits:20261021", value)
    argv, kwargs = calls[0]
    assert argv[-2:] == ["qsys-price-limits-publisher@127.0.0.2", p.COMMAND]
    assert "StrictHostKeyChecking=yes" in argv
    assert "UserKnownHostsFile=/private/hosts" in argv
    assert "IdentitiesOnly=yes" in argv
    assert kwargs["timeout"] == 25
    for key in ["cmd:orders", "state:price_limits:20261021:extra", "state:price_limits:20260230"]:
        with pytest.raises(ValueError):
            client.set_key(key, value)
    assert len(calls) == 1


@pytest.mark.parametrize("returncode,stdout", [(1, b"secret"), (0, b"{}"), (0, b"x" * 1025)])
def test_client_failure_is_closed_and_output_is_not_exposed(monkeypatch, returncode, stdout):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=returncode, stdout=stdout))
    client = p.SshPriceLimitsPublisher(Path("key"), Path("hosts"), StreamConfig())
    with pytest.raises(RuntimeError) as exc:
        client.set_key("state:price_limits:20261021", '{"600000.SH":[1,null]}')
    assert "secret" not in str(exc.value)


def test_server_fixed_config_controls_key_and_original_value(monkeypatch, capsys):
    calls = []
    config = SimpleNamespace(streams=StreamConfig(price_limits="fixed:validation"))
    monkeypatch.setattr(sys, "argv", ["publisher", "--config", "/private/config"])
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(request())))
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", p.COMMAND)
    monkeypatch.setattr(p, "load_deploy_config", lambda path: config)
    monkeypatch.setattr(p.RedisTransport, "from_config", lambda cfg: SimpleNamespace(set_key=lambda *a: calls.append(a)))
    assert p.main() == 0
    assert calls == [("fixed:validation:20261021", json.loads(request())["value"])]
    assert json.loads(capsys.readouterr().out)["rows"] == 2


@pytest.mark.parametrize("command", [None, "", "bash", "GET cmd:orders", "internal-sftp", p.COMMAND + " extra"])
def test_shell_sftp_and_other_commands_are_rejected_before_config(monkeypatch, command, capsys):
    monkeypatch.setattr(sys, "argv", ["publisher", "--config", "/must-not-read"])
    if command is None:
        monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)
    else:
        monkeypatch.setenv("SSH_ORIGINAL_COMMAND", command)
    monkeypatch.setattr(p, "load_deploy_config", lambda path: pytest.fail("不得读取配置"))
    assert p.main() == 1
    assert capsys.readouterr().err == "受限涨跌停发布失败\n"
