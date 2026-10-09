from datetime import date

import polars as pl
import pytest

from scripts import recalibrate_structural_gates as study


def _regime() -> pl.DataFrame:
    days = [
        date(2024, 12, 31), date(2025, 1, 2), date(2025, 6, 30),
        date(2025, 12, 31), date(2026, 1, 2), date(2026, 6, 30),
        date(2026, 7, 1), date(2026, 9, 30),
    ]
    return pl.DataFrame({
        "dt": days,
        "index_ma20_slope_5d": [-.03, -.02, -.01, .00, .01, .02, .03, .04],
        "prior_limitup_nextday_return_mean": [.00, .01, .02, .03, .04, .05, .06, .07],
        "market_median_return_1d": [-.02, -.01, .00, .01, .02, .03, .04, .05],
        "market_advance_ratio": [.30, .40, .50, .60, .70, .80, .90, .95],
        "touched_limit_up_count": [120.0] * len(days),
    })


def _decisions() -> pl.DataFrame:
    return pl.DataFrame({
        "date": [
            date(2025, 1, 2), date(2025, 7, 1), date(2025, 12, 31),
            date(2025, 12, 31), date(2026, 1, 2), date(2026, 7, 1),
        ],
        "symbol": [
            "600001.SH", "600002.SH", "600003.SH", "300001.SZ", "600004.SH",
            "600005.SH",
        ],
        "strategy_tag": [
            "LOW_MAIN", "LOW_MAIN", "HIGH_MAIN", "HIGH_CHINEXT", "HIGH_CHINEXT",
            "LOW_CHINEXT",
        ],
        "reason": [
            "LOW_MAIN_CLEAN", "MARKET_BAD_NOT_WEAK_HIGH_HEAT", "BREADTH_STRONG",
            "ACTIVITY_AND_BREADTH_OK", "ACTIVITY_AND_BREADTH_OK",
            "SAMPLE_INSUFFICIENT_KEEP_BASELINE",
        ],
        # These values only exercise temporal joining and fitting.  Replay is
        # tested separately with thresholds tailored to each row.
        "real_allow": [True, False, True, True, True, True],
    })


def test_candidate_day_uses_the_immediately_preceding_regime_session():
    got = study.build_observations(_decisions(), _regime())
    assert got["regime_dt"].to_list() == [
        date(2024, 12, 31), date(2025, 6, 30), date(2025, 6, 30),
        date(2025, 6, 30), date(2025, 12, 31), date(2026, 6, 30),
    ]
    assert got["split"].to_list() == [
        "TRAIN", "TRAIN", "TRAIN", "TRAIN", "VALID", "OBS",
    ]


def test_decisions_outside_the_frozen_windows_fail_closed():
    bad = _decisions().with_columns(
        pl.when(pl.col("symbol") == "600001.SH")
        .then(date(2024, 12, 30)).otherwise(pl.col("date")).alias("date")
    )
    with pytest.raises(ValueError, match="outside fixed"):
        study.build_observations(bad, _regime())


def test_fit_uses_train_candidates_and_declared_quantiles_only():
    got = study.build_observations(_decisions(), _regime())
    thresholds, evidence = study.fit_thresholds(got)
    # Two LOW_MAIN TRAIN candidates saw the 2024-12-31 and 2025-06-30 rows.
    assert thresholds["low_trend_weak"] == pytest.approx(-0.03 + (0.02 / 3))
    assert thresholds["low_heat_low"] == pytest.approx(0.00 + (0.02 / 3))
    # VALID has a much larger advance ratio, but must not affect the fit.
    assert thresholds["high_chinext_advance_not_low"] == pytest.approx(0.50)
    assert {row["key"] for row in evidence} == set(study.OLD_THRESHOLDS)


def test_gate_replay_retains_the_non_d4_stock_and_touched_rules():
    base = {
        "index_ma20_slope_5d": -1.0,
        "prior_limitup_nextday_return_mean": -1.0,
        "market_median_return_1d": 1.0,
        "market_advance_ratio": 1.0,
        "touched_limit_up_count": 120.0,
        "reason": "LOW_MAIN_CLEAN",
    }
    thresholds = {key: 0.0 for key in study.OLD_THRESHOLDS}
    assert study.gate_allow({**base, "strategy_tag": "LOW_MAIN"}, thresholds)
    assert not study.gate_allow({
        **base, "strategy_tag": "LOW_MAIN", "reason": study.LOW_STOCK_REASON,
    }, thresholds)
    assert study.gate_allow({**base, "strategy_tag": "HIGH_CHINEXT"}, thresholds)
    assert not study.gate_allow({
        **base, "strategy_tag": "HIGH_CHINEXT", "touched_limit_up_count": 100.0,
    }, thresholds)


def test_research_scope_records_920_bse_without_excluding_000920_sz():
    frame = pl.DataFrame({
        "symbol": ["920026.BJ", "430001.BJ", "688001.SH", "000920.SZ", "300920.SZ"],
    })
    eligible, excluded = study.partition_strategy_scope(frame)
    assert eligible["symbol"].to_list() == ["000920.SZ", "300920.SZ"]
    assert excluded["symbol"].to_list() == ["920026.BJ", "430001.BJ", "688001.SH"]
