from datetime import date

import polars as pl
import pytest

from qsys.research.structural_gate_attribution import (
    enrich_traces,
    gate_diagnostics,
    single_relaxations,
    relaxation_summary,
    variant_summary,
    write_report,
)
from scripts.gate_study import net_return_pct


def test_low_main_exposes_market_stock_quadrants_and_variants():
    row = {
        "routed_tag": "LOW_MAIN",
        "index_ma20_slope_5d": 0.01,
        "prior_limitup_nextday_return_mean": 0.02,
        "stock_return_10d": 0.01,
        "distance_to_60d_high": -0.5,
    }
    result = gate_diagnostics(row)
    assert result["market_bad"] is True
    assert result["stock_bad"] is False
    assert result["variants"]["current"] is False
    assert result["variants"]["low_stock_only"] is True
    assert result["variants"]["low_both_bad_only"] is True


def test_high_chinext_does_not_hide_a_good_component():
    result = gate_diagnostics({
        "routed_tag": "HIGH_CHINEXT",
        "touched_limit_up_count": 120,
        "market_advance_ratio": 0.4,
    })
    assert result["activity_favorable"] is True
    assert result["breadth_favorable"] is False
    assert result["variants"]["current"] is False
    assert result["variants"]["hc_activity_only"] is True


def test_replay_rejects_any_difference_from_recorded_gate():
    frame = pl.DataFrame([{
        "date": date(2026, 1, 2), "symbol": "600001.SH",
        "routed_tag": "HIGH_MAIN", "market_median_return_1d": 0.02,
        "gate_allow": False,
    }])
    with pytest.raises(ValueError, match="gate replay differs"):
        enrich_traces(frame)


def test_replay_normalizes_csv_string_booleans_without_truthiness_bug():
    frame = pl.DataFrame([{
        "date": date(2026, 1, 2), "symbol": "600001.SH",
        "routed_tag": "HIGH_MAIN", "market_median_return_1d": 0.0,
        "gate_allow": "false", "gate_known": "true",
    }])
    row = enrich_traces(frame)[0]
    assert row["gate_allow"] is False
    assert row["gate_known"] is True


def test_single_relaxation_requires_exactly_one_failed_atomic_condition():
    row = {
        "date": date(2026, 1, 2), "symbol": "600001.SH", "routed_tag": None,
        "strict_auction_input": True,
        "low_history_ok": True, "low_auction_price_ok": True,
        "low_relative_position_ok": True, "low_amount_ok": True,
        "low_open_band_ok": False, "low_auction_volume_ok": True,
        "high_history_ok": True, "high_auction_price_ok": True,
        "high_avg_inc_ok": False, "high_amount_ok": False,
        "high_market_cap_ok": True, "high_circulating_cap_ok": True,
        "high_auction_volume_ok": True, "high_open_band_ok": False,
        "high_left_pressure_ok": True,
    }
    result = single_relaxations([row])
    assert result.height == 1
    assert result.row(0, named=True)["relaxed_condition"] == "low_open_band_ok"


def test_single_relaxation_uses_extended_volume_approximation():
    row = {
        "date": date(2026, 1, 2), "symbol": "600001.SH", "routed_tag": None,
        "strict_auction_input": False, "low_eligible": False,
        "low_history_ok": True, "low_auction_price_ok": True,
        "low_relative_position_ok": True, "low_amount_ok": True,
        "low_open_band_ok": False, "low_auction_volume_ok": None,
        "high_history_ok": True, "high_auction_price_ok": True,
        "high_avg_inc_ok": False, "high_amount_ok": False,
        "high_market_cap_ok": True, "high_circulating_cap_ok": True,
        "high_auction_volume_rule_ok": True, "high_open_band_ok": False,
        "high_left_pressure_ok": True,
    }
    result = single_relaxations([row])
    assert result.height == 1
    assert result.row(0, named=True)["relaxed_condition"] == "low_open_band_ok"
    assert result.row(0, named=True)["strict_auction_input"] is False


def test_high_relaxation_is_not_counted_when_low_has_route_precedence():
    row = {
        "date": date(2026, 1, 2), "symbol": "600001.SH", "routed_tag": None,
        "strict_auction_input": True, "low_eligible": True,
        "low_history_ok": True, "low_auction_price_ok": True,
        "low_relative_position_ok": True, "low_amount_ok": True,
        "low_open_band_ok": True, "low_auction_volume_ok": False,
        "high_history_ok": True, "high_auction_price_ok": True,
        "high_avg_inc_ok": True, "high_amount_ok": True,
        "high_market_cap_ok": True, "high_circulating_cap_ok": True,
        "high_auction_volume_rule_ok": True, "high_open_band_ok": False,
        "high_left_pressure_ok": True,
    }
    result = single_relaxations([row])
    assert result.height == 1
    assert result.row(0, named=True)["strategy_tag"] == "LOW_MAIN"
    assert result.row(0, named=True)["relaxed_condition"] == "low_auction_volume_ok"


def test_variant_summary_uses_date_block_bootstrap_and_strength_labels():
    rows = [
        {"date": date(2026, 1, 2), "strategy_tag": "HIGH_MAIN",
         "market_median_return_1d": 0.02, "net_return_pct": 1.0},
        {"date": date(2026, 1, 5), "strategy_tag": "HIGH_MAIN",
         "market_median_return_1d": 0.03, "net_return_pct": 2.0},
    ]
    summary = variant_summary(rows)
    assert set(summary["variant"].to_list()) == {"current", "no_gate"}
    assert set(summary["evidence_strength"].to_list()) == {"insufficient"}


def test_relaxation_summary_keeps_strict_and_extended_samples_separate():
    rows = [
        {"date": date(2026, 1, 2), "strategy_tag": "LOW_MAIN",
         "relaxed_condition": "low_amount_ok", "strict_auction_input": True,
         "net_return_pct": 1.0},
        {"date": date(2026, 1, 5), "strategy_tag": "LOW_MAIN",
         "relaxed_condition": "low_amount_ok", "strict_auction_input": False,
         "net_return_pct": -1.0},
        {"date": date(2026, 1, 6), "strategy_tag": "LOW_MAIN",
         "relaxed_condition": "low_amount_ok", "strict_auction_input": True,
         "net_return_pct": 50.0, "outcome_quality": "no_intraday"},
    ]
    summary = relaxation_summary(rows).filter(pl.col("period") == "ALL")
    assert set(summary["sample_layer"].to_list()) == {"strict", "extended"}
    assert set(summary["n"].to_list()) == {1}


def test_counterfactual_net_return_includes_production_costs_and_slippage():
    assert net_return_pct(10.0, 11.0) < 10.0
    assert net_return_pct(10.0, 11.0) > 9.0


def test_report_writes_reproducible_artifact_set(tmp_path):
    trace_path = tmp_path / "candidate_funnel.csv"
    trace_path.write_text("date,symbol\n2026-01-02,600001.SH\n", encoding="utf-8")
    rows = [{
        "date": date(2026, 1, 2), "symbol": "600001.SH",
        "strict_auction_input": True, "terminal_stage": "allowed",
        "terminal_reason": "allowed", "routed_tag": "HIGH_MAIN",
    }]
    outcomes = [{
        **rows[0], "strategy_tag": "HIGH_MAIN",
        "market_median_return_1d": 0.02, "net_return_pct": 1.0,
    }]
    report = write_report(
        tmp_path / "out", trace_path=trace_path, rows=rows, outcomes=outcomes,
    )
    assert report["safety"]["production_parameters_modified"] is False
    assert {
        "coverage.csv", "funnel_summary.csv", "gate_cells.csv",
        "variant_metrics.csv", "single_relaxation_metrics.csv",
        "counterfactual_trades.parquet",
        "report.json", "report.md",
    } <= {path.name for path in (tmp_path / "out").iterdir()}
