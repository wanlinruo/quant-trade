from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from qsys.research.limit_up_ranking import FEATURE_COLUMNS
from qsys.research.limit_up_shadow import (
    DRIFT_BINS,
    DriftReference,
    PromotedModel,
    build_drift_reference,
    load_drift_reference,
    load_promoted_model,
    outcome_truth,
    prediction_psi,
    score_snapshot,
    scoring_frame_sha,
)


class _Model:
    def predict(self, frame):
        count = frame.height
        touch = [0.50 + index / 100 for index in range(count)]
        seal = [0.40] * count
        return touch, seal, [left * right for left, right in zip(touch, seal, strict=True)]


def _frame() -> pl.DataFrame:
    rows = []
    for index, symbol in enumerate(("600001.SH", "300001.SZ", "688001.SH")):
        row = {
            "prediction_date": date(2026, 10, 9),
            "target_session": date(2026, 10, 12),
            "symbol": symbol,
            "board": ("main", "gem", "star")[index],
            "market_state": "neutral",
        }
        row.update({feature: float(index) for feature in FEATURE_COLUMNS})
        rows.append(row)
    return pl.DataFrame(rows)


def _promoted(model=None) -> PromotedModel:
    return PromotedModel(
        model=model or _Model(), version="limit-up-lgbm-abcdef123456",
        source_sha="a" * 40, artifact_sha="b" * 64,
        research_data_sha="c" * 64,
    )


def test_score_snapshot_ranks_and_keeps_only_compact_contract():
    frame = _frame()

    snapshot = score_snapshot(
        frame, _promoted(), names={"688001.SH": "测试科创"},
        generated_at=datetime(2026, 10, 9, 17, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert snapshot.eligible_count == 3
    assert [entry.symbol for entry in snapshot.entries] == [
        "688001.SH", "300001.SZ", "600001.SH",
    ]
    assert snapshot.entries[0].name == "测试科创"
    assert snapshot.model_sha == "b" * 64
    assert len(snapshot.data_sha) == 64
    assert snapshot.entries[0].actual_touched is None


def test_drift_reference_is_compact_and_psi_detects_shift(tmp_path: Path):
    predictions = tmp_path / "predictions.parquet"
    values = [0.0015, 0.003, 0.007, 0.015, 0.03, 0.07] * 10
    pl.DataFrame({
        "model": ["lightgbm"] * len(values) + ["momentum"],
        "split": ["OBS"] * len(values) + ["OBS"],
        "p_close": values + [0.9],
    }).write_parquet(predictions)

    reference = build_drift_reference(predictions)
    path = tmp_path / "reference.json"
    path.write_text(__import__("json").dumps(reference.as_dict()), encoding="utf-8")
    loaded = load_drift_reference(path)

    assert loaded.rows == len(values)
    assert len(loaded.proportions) == len(DRIFT_BINS) - 1
    assert prediction_psi(values, loaded) == pytest.approx(0)
    assert prediction_psi([0.8] * len(values), loaded) > 0.25


def test_score_snapshot_publishes_only_scalar_drift():
    frame = _frame()
    close = [value * 0.4 for value in (0.50, 0.51, 0.52)]
    reference = DriftReference(
        source_sha="d" * 64, rows=3, bin_edges=DRIFT_BINS,
        proportions=tuple(
            count / 3 for count in [0, 0, 0, 0, 0, 0, 0, 3, 0, 0]
        ),
    )

    snapshot = score_snapshot(frame, _promoted(), drift_reference=reference)

    assert snapshot.drift_score == pytest.approx(prediction_psi(close, reference))


def test_scoring_fingerprint_is_order_independent_but_feature_sensitive():
    frame = _frame()
    shuffled = frame.reverse()
    changed = frame.with_columns(pl.col(FEATURE_COLUMNS[0]) + 1)

    assert scoring_frame_sha(frame) == scoring_frame_sha(shuffled)
    assert scoring_frame_sha(frame) != scoring_frame_sha(changed)


class _BadModel:
    def predict(self, frame):
        return [0.5] * frame.height, [0.5] * frame.height, [0.9] * frame.height


def test_score_snapshot_rejects_probability_identity_violation():
    with pytest.raises(ValueError, match="two-stage"):
        score_snapshot(_frame(), _promoted(_BadModel()))


def test_load_promoted_model_requires_passed_verdict(tmp_path: Path):
    import json

    (tmp_path / "manifest.json").write_text(json.dumps({
        "git_sha": "a" * 40,
        "dataset": {"sha256": "b" * 64},
        "feature_columns": list(FEATURE_COLUMNS),
    }), encoding="utf-8")
    (tmp_path / "verdict.json").write_text(
        '{"promote_to_shadow": false}', encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not permit"):
        load_promoted_model(tmp_path)


def test_load_promoted_model_preserves_full_artifact_provenance(tmp_path: Path):
    import joblib
    import json

    (tmp_path / "models").mkdir()
    joblib.dump(_Model(), tmp_path / "models/last_walk_forward_lightgbm.joblib")
    (tmp_path / "manifest.json").write_text(json.dumps({
        "git_sha": "a" * 40,
        "dataset": {"sha256": "b" * 64},
        "feature_columns": list(FEATURE_COLUMNS),
    }), encoding="utf-8")
    (tmp_path / "verdict.json").write_text(
        '{"promote_to_shadow": true}', encoding="utf-8",
    )

    promoted = load_promoted_model(tmp_path)

    assert promoted.version == "limit-up-lgbm-aaaaaaaa-bbbbbbbb"
    assert promoted.source_sha == "a" * 40
    assert promoted.research_data_sha == "b" * 64
    assert len(promoted.artifact_sha) == 64


def test_outcome_truth_requires_complete_rows_and_fingerprints_source(tmp_path: Path):
    day = date(2026, 10, 12)
    flags_path = tmp_path / "daily_flags/year=2026"
    bars_path = tmp_path / "tushare_daily/year=2026"
    flags_path.mkdir(parents=True)
    bars_path.mkdir(parents=True)
    pl.DataFrame({
        "dt": [day, day], "symbol": ["000001.SZ", "600000.SH"],
        "touched_limit_up": [True, False], "is_limit_up": [True, False],
        "limit_up": [11.0, 22.0],
    }).write_parquet(flags_path / "part.parquet")
    pl.DataFrame({
        "dt": [day, day], "symbol": ["000001.SZ", "600000.SH"],
        "open": [11.0, 20.0], "high": [11.0, 20.5], "low": [11.0, 19.8],
    }).write_parquet(bars_path / "part.parquet")

    outcomes, source_sha = outcome_truth(
        tmp_path, day, ("000001.SZ", "600000.SH"),
    )

    assert len(source_sha) == 64
    assert outcomes[0].one_price_limit is True
    assert outcomes[1].actual_touched is False

    with pytest.raises(ValueError, match="completely cover"):
        outcome_truth(tmp_path, day, ("000001.SZ", "600000.SH", "300001.SZ"))
