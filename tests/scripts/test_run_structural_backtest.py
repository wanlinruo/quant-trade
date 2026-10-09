import json

import pytest

from scripts.run_structural_backtest import (
    RESEARCH_THRESHOLD_KEYS, load_research_thresholds,
)


def test_load_research_thresholds_requires_the_exact_recalibration_schema(tmp_path):
    path = tmp_path / "recalibration.json"
    expected = {
        key: index / 10
        for index, key in enumerate(sorted(RESEARCH_THRESHOLD_KEYS))
    }
    path.write_text(
        json.dumps({"candidate_thresholds": expected}), encoding="utf-8"
    )
    assert load_research_thresholds(path) == expected


def test_load_research_thresholds_rejects_partial_or_unrelated_json(tmp_path):
    path = tmp_path / "recalibration.json"
    path.write_text(
        json.dumps({"candidate_thresholds": {"low_heat_low": 0.1}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="exactly"):
        load_research_thresholds(path)
