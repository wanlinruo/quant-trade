import pytest

from scripts.benchmark_cutover import _aggregate


def _sample(ms=10.0, peak=20.0, *, rows=100, files=2, columns=8):
    return {
        "median_ms": ms,
        "peak_mb": peak,
        "rows": rows,
        "scanned_files": files,
        "scanned_columns": columns,
    }


def test_aggregate_uses_medians_across_independent_processes():
    got = _aggregate([
        _sample(12.0, 22.0),
        _sample(10.0, 20.0),
        _sample(11.0, 21.0),
    ])

    assert got == {
        "median_ms": 11.0,
        "peak_mb": 21.0,
        "rows": 100,
        "scanned_files": 2,
        "scanned_columns": 8,
    }


@pytest.mark.parametrize("field", ["rows", "scanned_files", "scanned_columns"])
def test_aggregate_rejects_shape_drift(field):
    changed = _sample()
    changed[field] += 1

    with pytest.raises(RuntimeError, match=field):
        _aggregate([_sample(), changed])


def test_aggregate_rejects_empty_samples():
    with pytest.raises(ValueError, match="不得为空"):
        _aggregate([])
