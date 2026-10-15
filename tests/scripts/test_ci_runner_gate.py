import datetime as dt
import importlib.util
import sys
from pathlib import Path


GATE_PATH = Path(".github/ci-runner/runner_gate.py")
GATE_SPEC = importlib.util.spec_from_file_location("runner_gate", GATE_PATH)
assert GATE_SPEC is not None and GATE_SPEC.loader is not None
GATE = importlib.util.module_from_spec(GATE_SPEC)
sys.modules["runner_gate"] = GATE
GATE_SPEC.loader.exec_module(GATE)


def healthy(**overrides):
    values = {
        "enabled": True,
        "now": dt.datetime(2026, 10, 15, 12, 0),
        "available_bytes": 4 * 1024**3,
        "free_disk_bytes": 20 * 1024**3,
        "io_full_avg10": 0.5,
        "active_units": set(GATE.REQUIRED_UNITS),
        "failed_units": set(),
    }
    values.update(overrides)
    return GATE.audit_values(**values)


def test_gate_accepts_healthy_daytime_host():
    assert healthy()["ok"] is True


def test_gate_rejects_missing_operator_activation():
    result = healthy(enabled=False)
    assert result["ok"] is False
    assert "operator activation gate is absent" in result["errors"]


def test_gate_reserves_evening_production_window():
    result = healthy(now=dt.datetime(2026, 10, 15, 18, 25))
    assert "production high-water blackout is active" in result["errors"]
    assert healthy(now=dt.datetime(2026, 10, 15, 21, 0))["ok"] is True


def test_gate_rejects_resource_pressure_and_batch_collision():
    result = healthy(
        available_bytes=2 * 1024**3,
        free_disk_bytes=10 * 1024**3,
        io_full_avg10=5.1,
        active_units={*GATE.REQUIRED_UNITS, "qsys-tushare-sync.service"},
        failed_units={"broken.service"},
    )
    assert result["ok"] is False
    assert result["active_high_water_units"] == ["qsys-tushare-sync.service"]
    assert len(result["errors"]) == 5


def test_gate_requires_live_core_and_monitor():
    result = healthy(active_units=set())
    assert result["missing_required_units"] == sorted(GATE.REQUIRED_UNITS)
    assert "required production service is not active" in result["errors"]


def test_proc_parsers_are_strict():
    assert GATE.parse_mem_available("MemTotal: 99 kB\nMemAvailable: 42 kB\n") == 42 * 1024
    assert GATE.parse_pressure_avg10("some avg10=2.0 total=3\nfull avg10=1.25 total=2\n") == 1.25
