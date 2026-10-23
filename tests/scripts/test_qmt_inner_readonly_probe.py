"""QMT 内置诊断的离线边界测试；不导入或访问真实 QMT。"""

import ast
import builtins
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest


PROBE = Path(__file__).resolve().parents[2] / "docs/reference/qmt-inner-readonly-probe.py"


def load_probe():
    spec = importlib.util.spec_from_file_location("qmt_inner_probe", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configured_probe():
    probe = load_probe()
    probe.CLIENT_BUILD = "offline-fixture"
    probe.PROBE_DATE = datetime.now().strftime("%Y%m%d")
    probe.HISTORY_END = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    return probe


def records(capsys):
    return [ast.literal_eval(line) for line in capsys.readouterr().out.splitlines()]


def test_probe_is_python36_and_only_imports_stdlib_metadata():
    tree = ast.parse(PROBE.read_text(), feature_version=(3, 6))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module)
    assert imports == ["sys", "datetime"]
    # Restrict actual QMT operations, including any future additions.
    context_calls = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "ContextInfo"
    }
    assert context_calls == {"is_last_bar", "get_full_tick", "get_market_data_ex"}


def test_unconfigured_probe_never_queries(capsys):
    probe = load_probe()
    probe.init(object())
    probe.handlebar(object())
    assert records(capsys)[0]["status"] == "rejected"


@pytest.mark.parametrize("field,value", [
    ("PROBE_DATE", "19990101"), ("HISTORY_END", "20990101"),
    ("HISTORY_END", "20260230"), ("SYMBOLS", ()),
    ("SYMBOLS", ("600000.SH",) * 2), ("SYMBOLS", ("bad",)),
])
def test_invalid_metadata_stops_before_market_access(field, value, capsys):
    probe = configured_probe()
    setattr(probe, field, value)
    probe.init(object())
    probe.handlebar(object())
    assert records(capsys)[0]["status"] == "rejected"


def test_only_bounded_public_queries_without_io_or_trade(monkeypatch, capsys):
    probe = configured_probe()
    calls = []

    def forbidden(*args, **kwargs):
        pytest.fail("probe attempted file/account/trading operation")

    def history(fields, symbols, **kwargs):
        calls.append((fields, symbols, kwargs))
        return {symbols[0]: pd.DataFrame({field: [1, 2, 3] for field in fields})}

    context = SimpleNamespace(
        is_last_bar=lambda: True,
        get_full_tick=lambda symbols: {symbols[0]: {"time": 123, "volume": 7}},
        get_market_data_ex=history,
    )
    for name in ("passorder", "cancel", "get_trade_detail_data", "get_history_trade_detail_data"):
        setattr(probe, name, forbidden)
    probe.init(context)
    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        probe.handlebar(context)
        probe.handlebar(context)
    assert len(calls) == 2
    for fields, symbols, kwargs in calls:
        assert fields == list(probe.FIELDS)
        assert symbols == list(probe.SYMBOLS)
        assert kwargs["fill_data"] is False and kwargs["subscribe"] is False
        assert kwargs["dividend_type"] == "none"
        assert kwargs["end_time"] == probe.HISTORY_END + "150000"
    assert [(call[2]["period"], call[2]["count"]) for call in calls] == [("1d", 101), ("1m", 2)]
    output = records(capsys)
    samples = [record for record in output if record["check"] in ("1d", "1m")]
    assert all(len(record["raw"]["data"]) == 2 for record in samples)
    assert output[-1]["status"] == "incomplete"


def test_history_signature_error_is_not_retried_or_silently_downgraded(capsys):
    probe = configured_probe()
    calls = []

    def incompatible(*args, **kwargs):
        calls.append(kwargs)
        raise TypeError("private account detail must never be printed")

    context = SimpleNamespace(is_last_bar=lambda: True,
                              get_full_tick=lambda symbols: {},
                              get_market_data_ex=incompatible)
    probe.init(context)
    probe.handlebar(context)
    probe.handlebar(context)
    assert len(calls) == 2  # Each independent period once; no fallback or retry.
    output = records(capsys)
    assert len([row for row in output if row["status"] == "error"]) == 2
    assert "private account" not in repr(output)


@pytest.mark.parametrize("raw,status", [(None, "unexpected_type"), ({}, "missing"),
    ({"600000.SH": pd.DataFrame(columns=list("x"))}, "missing_columns"),
    ({"600000.SH": pd.DataFrame(columns=["open", "high", "low", "close", "volume", "amount"])}, "empty"),
])
def test_absent_history_is_never_reported_as_a_success(raw, status, capsys):
    probe = configured_probe()
    probe._history_sample(SimpleNamespace(get_market_data_ex=lambda *a, **k: raw), "1d", 101)
    assert records(capsys)[0]["status"] == status


def test_historical_callbacks_do_not_trigger_market_reads(capsys):
    probe = configured_probe()
    context = SimpleNamespace(is_last_bar=lambda: False)
    probe.init(context)
    capsys.readouterr()
    probe.handlebar(context)
    assert capsys.readouterr().out == ""
