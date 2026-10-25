"""#533 同输入差分：生成的无交易核心与现有生产计算逐字段比较。"""

import ast
import hashlib
import importlib.util
import itertools
import json
import math
from dataclasses import asdict
from pathlib import Path

import pytest

from qsys.strategy.structural import allocation, exits, gate


ROOT = Path(__file__).resolve().parents[2]


def load(name):
    path = ROOT / "docs/reference" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def core():
    return load("qmt-inner-decision-core")


def boundary_values(value):
    return (None, math.nextafter(value, -math.inf), value,
            math.nextafter(value, math.inf), float("nan"), "invalid")


def test_generation_is_reproducible_and_binds_exact_sources(core):
    generator = load("build-qmt-inner-core")
    source = (ROOT / "docs/reference/qmt-inner-decision-core.py").read_text()
    assert source == generator.build_core()
    for name, digest in core.SOURCE_SHA256.items():
        if name == generator.HISTORY_INPUT:
            path = ROOT / "docs/reference" / name
        elif name in generator.REFERENCE_SOURCES:
            path = ROOT / name
        else:
            path = ROOT / "src/qsys/strategy/structural" / name
        assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert set(generator.REFERENCE_SOURCES) <= set(core.SOURCE_SHA256)
    ast.parse(source, feature_version=(3, 6))
    tree = ast.parse(source)
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert imports == ["collections"]
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
    calls = [node.func.id for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    assert not set(calls) & {"open", "exec", "eval", "__import__", "passorder", "cancel"}
    assert not hasattr(core, "init") and not hasattr(core, "handlebar")
    assert not hasattr(core, "gate_allow_policy")  # Research switches are not bundled.


def test_records_remain_immutable(core):
    value = core.Classification("UNKNOWN_ALLOW", False, "fixture")
    with pytest.raises(AttributeError):
        value.state = "FAVORABLE"
    assert core.ExitDecision(False)._asdict() == asdict(exits.ExitDecision(False))


def test_archived_production_manifest_is_self_consistent():
    # Historical evidence is not a gate on unrelated future source changes.
    baseline = json.loads((ROOT / "docs/reference/qmt-inner-production-baseline.json").read_text())
    encoded = json.dumps(baseline["module_sha256"], sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == baseline["module_manifest_sha256"]
    assert len(baseline["module_sha256"]) == 13
    assert set(baseline["explicit_fees"]) == {
        "commission_rate", "min_commission", "stamp_duty_rate", "transfer_fee_rate",
    }


@pytest.mark.parametrize("left,right", list(itertools.product((None, False, True), repeat=2)))
def test_three_value_logic(core, left, right):
    assert core.three_or(left, right) is gate.three_or(left, right)
    assert core.three_and(left, right) is gate.three_and(left, right)


def assert_classification(core, tag, env, stock, thresholds=None):
    actual = core.classify(tag, env, stock, thresholds=thresholds)
    expected = gate.classify(tag, env, stock, thresholds=thresholds)
    assert actual._asdict() == asdict(expected)
    assert core.gate_allow(tag, actual) is gate.gate_allow(tag, expected)


def test_low_gate_all_unknown_and_threshold_boundaries(core):
    values = [boundary_values(value) for value in (
        gate.S6_LOW_TREND_WEAK_THRESHOLD, gate.S6_LOW_HEAT_LOW_THRESHOLD,
        gate.S6_LOW_STOCK_RET10_HIGH, gate.S6_LOW_DISTANCE60_HIGH,
    )]
    for slope, heat, ret10, distance in itertools.product(*values):
        assert_classification(core, "LOW_MAIN", {
            "index_ma20_slope_5d": slope,
            "prior_limitup_nextday_return_mean": heat,
        }, {"stock_return_10d": ret10, "distance_to_60d_high": distance})


def test_other_gate_branches_and_threshold_overrides(core):
    for value in boundary_values(gate.S6_HIGH_MAIN_BREADTH_HIGH):
        assert_classification(core, "HIGH_MAIN", {"market_median_return_1d": value}, {})
    for touched, advance in itertools.product(
        boundary_values(gate.S6_HC_TOUCHED_NOT_LOW), boundary_values(gate.S6_HC_ADVANCE_NOT_LOW),
    ):
        assert_classification(core, "HIGH_CHINEXT", {
            "touched_limit_up_count": touched, "market_advance_ratio": advance,
        }, {})
    assert_classification(core, "LOW_CHINEXT", {}, {})
    assert_classification(core, "HIGH_MAIN", {"market_median_return_1d": 0.02}, {},
                          {"high_main_breadth_high": 0.03})
    assert_classification(core, "HIGH_CHINEXT", {
        "touched_limit_up_count": 200, "market_advance_ratio": 0.7,
    }, {}, {"high_chinext_advance_not_low": 0.8})
    assert_classification(core, "LOW_MAIN", {
        "index_ma20_slope_5d": 0.01, "prior_limitup_nextday_return_mean": 0.01,
    }, {}, {"low_trend_weak": 0.02, "low_heat_low": 0.02})


def test_sizing_and_protection_match_including_live_buffers(core):
    candidates = [("600000.SH", "LOW_MAIN"), ("300001.SZ", "HIGH_CHINEXT")]
    cases = itertools.product(
        (-100, 0, 500, 1000, 1010, 10000, 100000),
        ((0.002, None), (0.03, 0.005), (0.03, 0)),
        (None, 9.0, 11.0), (None, 0, 10000),
    )
    for cash, (price_buffer, sizing_buffer), limit, maximum in cases:
        kwargs = dict(cash=cash, opens={"600000.SH": 10.0, "300001.SZ": 20.0},
                      limit_ups={"600000.SH": limit}, max_budget=maximum,
                      price_buffer=price_buffer, sizing_buffer=sizing_buffer)
        assert [row._asdict() for row in core.allocate(candidates, **kwargs)] == [
            asdict(row) for row in allocation.allocate(candidates, **kwargs)
        ]
    for opens in ({}, {"600000.SH": 0}, {"600000.SH": None}):
        assert [row._asdict() for row in core.allocate(candidates, 10000, opens)] == [
            asdict(row) for row in allocation.allocate(candidates, 10000, opens)
        ]
    assert core.allocate([], 10000, {}) == []
    for cash in (-1, 0, 1000, 10000, 50000):
        assert core.fee_reserve(cash) == allocation.fee_reserve(cash)
    # Preserve existing unknown-tag semantics in this parity specimen.
    unknown = [("600000.SH", "UNKNOWN")]
    assert core.allocate(unknown, 10000, {"600000.SH": 10})[0]._asdict() == asdict(
        allocation.allocate(unknown, 10000, {"600000.SH": 10})[0])
    with pytest.raises(ValueError):
        core.protect_price(10, None, -0.01)


def test_exits_match_missing_values_limit_prices_and_decision_order(core):
    for price, limit, entry, ma5 in itertools.product(
        (8.0, 10.0, 11.0, 12.0), (None, 10.0, 11.0, 12.0),
        (None, 0, 10.0), (None, 9.0, 11.0, 13.0),
    ):
        assert core.legacy_exit(price, limit, entry, ma5)._asdict() == asdict(
            exits.legacy_exit(price, limit, entry, ma5))
        assert core.low_1125(price, limit, entry)._asdict() == asdict(
            exits.low_1125(price, limit, entry))
        assert core.low_1425(price, limit, entry)._asdict() == asdict(
            exits.low_1425(price, limit, entry))
    for closes in ([], [10.0], [9.0, 10.0, 11.0], [8, 9, 10, 11, 12]):
        assert core.dynamic_ma5(closes, 11) == exits.dynamic_ma5(closes, 11)
    assert core.legacy_exit(11, 12, 10, 13).reason == "TAKE_PROFIT"
