"""Offline guards for #546 diagnostic; never connects to QMT."""
import ast
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

PROBE = Path(__file__).resolve().parents[2] / "docs/reference/qmt-runtime-container-probe.py"


def load():
    spec = importlib.util.spec_from_file_location("runtime_probe", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.CLIENT_BUILD = "offline-fixture"
    module.PROBE_DATE = datetime.now().strftime("%Y%m%d")
    module.HISTORY_END = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    return module


def records(capsys):
    return [ast.literal_eval(line) for line in capsys.readouterr().out.splitlines()]


def test_python36_syntax_and_native_call_allowlist():
    tree = ast.parse(PROBE.read_text(), feature_version=(3, 6))
    calls = {node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and isinstance(node.func.value, ast.Name) and node.func.value.id == "ContextInfo"}
    assert calls == {"is_last_bar", "get_market_data_ex"}


@pytest.mark.parametrize("field,value", [
    ("CLIENT_BUILD", ""), ("PROBE_DATE", "19990101"),
    ("HISTORY_END", "20990101"), ("HISTORY_END", "20260230"),
    ("SYMBOLS", ("600000.SH",) * 3), ("SYMBOLS", ("bad",)),
])
def test_invalid_configuration_has_no_native_access(field, value, capsys):
    probe = load()
    setattr(probe, field, value)
    probe.init(object())
    probe.handlebar(object())
    assert records(capsys)[0]["status"] == "rejected"


@pytest.mark.parametrize("raises", [False, True])
def test_bounded_query_never_retries_across_callbacks_or_init(raises, capsys):
    probe = load()
    calls = []

    def history(fields, symbols, **kwargs):
        calls.append(kwargs)
        assert kwargs["fill_data"] is False and kwargs["subscribe"] is False
        assert kwargs["count"] == 2 and kwargs["dividend_type"] == "none"
        assert kwargs["end_time"] == probe.HISTORY_END + "150000"
        if raises:
            raise TypeError("private detail must not reach output")
        return {symbol: pd.DataFrame({field: [1, None] for field in fields},
                                    index=["20261022", "20261023"]) for symbol in symbols}

    context = SimpleNamespace(is_last_bar=lambda: False, get_market_data_ex=history)
    probe.init(context)
    probe.handlebar(context)
    assert calls == []
    context.is_last_bar = lambda: True
    probe.handlebar(context)
    probe.handlebar(context)
    probe.init(context)
    probe.handlebar(context)
    assert len(calls) == 1
    output = records(capsys)
    assert "private detail" not in repr(output)
    scope = [row for row in output if row["check"] == "scope"]
    assert len(scope) == 1 and scope[0]["history_attempts"] == 1
    assert scope[0]["numeric_equivalence_verified"] is False
    if not raises:
        frames = [row for row in output if row["check"] == "daily_frame"]
        assert len(frames) == 2 and all(row["null_count"] == 4 for row in frames)
        assert all(row["status"] == "sample_only" for row in frames)
