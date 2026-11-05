"""Diagnostic boundaries for #599; no QMT, network or broker connection."""
import ast
import importlib.util
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2] / "docs/reference"


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_capability_probe_is_python36_and_has_no_io_or_trading_calls():
    tree = ast.parse((ROOT / "qmt-external-capability-probe.py").read_text(),
                     feature_version=(3, 6))
    methods = {n.func.attr for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert methods <= {"update", "strip", "isdigit", "strptime", "now", "strftime", "get"}
    names = {n.func.id for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert names <= {"dict", "print", "repr", "len", "any", "all", "isinstance", "ValueError", "type", "_emit",
                     "tuple", "__import__", "callable", "getattr", "globals"}


def test_probe_import_errors_are_redacted_and_one_shot(monkeypatch, capsys):
    probe = load("qmt-external-capability-probe")
    probe.CLIENT_BUILD = "offline-fixture"
    probe.PROBE_DATE = datetime.now().strftime("%Y%m%d")
    probe.RUN_ID = "a" * 32
    attempts = []

    def import_fixture(name):
        attempts.append(name)
        if name == "ctypes":
            raise ImportError("private token and account must not appear")
        return SimpleNamespace()

    monkeypatch.setitem(probe.__dict__, "__import__", import_fixture)
    probe.init(object())
    probe.init(object())
    probe.handlebar(object())
    output = capsys.readouterr().out
    assert "private token" not in output
    rows = [ast.literal_eval(line) for line in output.splitlines()]
    assert attempts == list(probe._MODULES)
    assert next(r for r in rows if r.get("name") == "ctypes")["error_type"] == "ImportError"
    assert rows[-1]["channel_verified"] is False
    assert rows[-1]["authorizes_trading"] is False
    assert rows[-1]["native_dll_calls"] == 0


@pytest.mark.parametrize("field,value", [("CLIENT_BUILD", ""), ("RUN_ID", "bad"),
                                        ("PROBE_DATE", "20000101")])
def test_invalid_probe_metadata_rejects_before_imports(field, value, monkeypatch, capsys):
    probe = load("qmt-external-capability-probe")
    probe.CLIENT_BUILD, probe.RUN_ID = "offline-fixture", "a" * 32
    probe.PROBE_DATE = datetime.now().strftime("%Y%m%d")
    setattr(probe, field, value)
    monkeypatch.setitem(probe.__dict__, "__import__", lambda _: pytest.fail("unexpected import"))
    probe.init(object())
    assert ast.literal_eval(capsys.readouterr().out)["status"] == "rejected"


def test_invalid_correlation_value_is_not_echoed(capsys):
    probe = load("qmt-external-capability-probe")
    probe.RUN_ID = "private-invalid-value"
    probe.init(object())
    raw = capsys.readouterr().out
    assert "private-invalid-value" not in raw
    assert ast.literal_eval(raw)["run_id"] is None


def ping(**changes):
    value = dict(protocol="qsys.external-echo.v1", kind="ping", run_id="a" * 32,
                 request_id="b" * 32, seq=1, issued_at_ms=1000,
                 expires_at_ms=2000, payload="diagnostic-中文")
    value.update(changes)
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def test_echo_retransmission_preserves_original_reply_and_correlation():
    module = load("qmt-external-echo-protocol")
    receiver = module.EchoReceiver("a" * 32)
    first = receiver.receive(ping(), 1100)
    assert receiver.receive(ping(), 1200) == first
    row = json.loads(first)
    assert row["payload"] == "diagnostic-中文"
    assert row["authorizes_trading"] is False
    assert row["received_at_ms"] == 1100
    with pytest.raises(ValueError):
        receiver.receive(ping(payload="changed"), 1200)
    receiver.receive(ping(request_id="c" * 32, seq=2), 1300)


@pytest.mark.parametrize("changes", [
    {"kind": "order"}, {"account": "fixture"}, {"symbol": "fixture"},
    {"protocol": "unknown"}, {"run_id": "c" * 32}, {"request_id": "bad"},
    {"seq": True}, {"seq": -1}, {"issued_at_ms": True},
    {"issued_at_ms": 1600}, {"expires_at_ms": 1500},
    {"expires_at_ms": 9000}, {"payload": {}}, {"payload": "x" * 257},
])
def test_invalid_or_trading_messages_never_consume_a_sequence(changes):
    module = load("qmt-external-echo-protocol")
    receiver = module.EchoReceiver("a" * 32)
    with pytest.raises(ValueError):
        receiver.receive(ping(**changes), 1500)
    receiver.receive(ping(), 1500)


@pytest.mark.parametrize("raw", [b"{", b"x" * 4097, b"\xff", b"[]",
                                  b'{"kind":"ping","kind":"order"}', b'{"seq":NaN}'])
def test_bad_frames_and_duplicate_json_keys_fail_closed(raw):
    module = load("qmt-external-echo-protocol")
    with pytest.raises((ValueError, UnicodeDecodeError)):
        module.decode_ping(raw, "a" * 32, 1500)


def test_reordering_and_restart_do_not_invent_durable_deduplication():
    module = load("qmt-external-echo-protocol")
    with pytest.raises(ValueError):
        module.EchoReceiver("a" * 32).receive(ping(seq=2), 1500)
    module.EchoReceiver("a" * 32).receive(ping(), 1500)
    with pytest.raises(ValueError):
        module.EchoReceiver("c" * 32).receive(ping(), 1500)
    # Reusing a run ID would lose memory-only deduplication: it is not production persistence.
    assert json.loads(module.EchoReceiver("a" * 32).receive(ping(), 1500))["authorizes_trading"] is False
