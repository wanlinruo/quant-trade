from datetime import date, datetime, time

import polars as pl
import pytest

from qsys.backtest.fees import AShareFeeModel
from qsys.research.first_board_outcomes import build_counterfactual_outcomes
from qsys.research.first_board_quality import (
    MODEL_FEATURES,
    GateThresholds,
    forward_verdict,
    choose_thresholds,
    net_return_pct,
    quality_features,
    sealed_at_decision,
    select_daily,
)


DAY = date(2026, 6, 9)


def test_quality_features_join_frozen_probability_and_context():
    ids = {"prediction_date": [date(2026, 6, 8)], "target_session": [DAY],
           "symbol": ["600001.SH"]}
    scored = pl.DataFrame({
        **ids, "rank": [2], "p_close": [0.1], "auction_gap": [0.03],
        "auction_volume_ratio": [0.02], "limit_up": [11.0],
        "decision_price": [10.5],
    })
    overnight = pl.DataFrame({
        **ids, "p_touch": [0.4], "p_seal_given_touch": [0.5], "p_close": [0.2],
    })
    context = {
        "free_turnover_ratio": 2.0, "range_pct": 0.05, "ret_1d": 0.01,
        "ret_5d": 0.04, "distance_high_20d": -0.03,
        "limit_up_down_ratio": 3.0, "limit_up_count": 50.0,
        "broken_board_rate": 0.2, "market_advance_ratio": 0.55,
        "board_main": 1.0, "board_gem": 0.0, "board_star": 0.0,
    }
    dataset = pl.DataFrame([{**{key: value[0] for key, value in ids.items()}, **context}])

    got = quality_features(scored, overnight, dataset)

    assert got["p_close"].item() == 0.2
    assert got["rank_fraction"].item() == 0.1
    assert set(MODEL_FEATURES) <= set(got.columns)


def test_sealed_and_fee_inclusive_return_contract():
    assert sealed_at_decision(10.995, 11.0)
    assert not sealed_at_decision(10.99, 11.0)
    value = net_return_pct(10.0, 11.0, 1000, AShareFeeModel())
    assert 9.7 < value < 10.0


def test_daily_selection_can_abstain_and_keeps_only_one():
    frame = pl.DataFrame({
        "target_session": [DAY, DAY], "rank": [1, 2],
        "pred_fill": [0.8, 0.9], "expected_utility": [0.004, 0.02],
        "pred_q10": [-0.04, -0.02], "pred_block": [0.02, 0.03],
    })
    thresholds = GateThresholds(0.7, 0.005, -0.05, 0.05)
    selected = select_daily(frame, thresholds)
    assert selected["rank"].to_list() == [2]
    assert select_daily(frame, GateThresholds(0.95, 0, -1)).is_empty()


def test_threshold_search_requires_fills_and_prefers_positive_oof_returns():
    rows = []
    for offset in range(40):
        day = date(2026, 1, 1) + __import__("datetime").timedelta(days=offset)
        rows.append({
            "target_session": day, "rank": 1, "pred_fill": 0.9,
            "expected_utility": 0.02, "pred_q10": -0.02, "pred_block": 0.01,
            "entry_filled": True, "next_open_return_pct": 1.0,
        })
    thresholds, summary = choose_thresholds(pl.DataFrame(rows), "next_open")
    assert summary["development_fills"] == 40
    assert summary["development_mean_selected_return"] > 0
    assert summary["development_mean_account_return_all_sessions"] == pytest.approx(
        summary["development_mean_selected_return"] * 0.1
    )
    assert summary["development_adjusted_daily_ci_low"] > 0
    assert thresholds.min_fill_probability <= 0.9


class _Query:
    def bar_daily(self, symbols, start, end, columns):
        days = [DAY, date(2026, 6, 10), date(2026, 6, 11)]
        return pl.DataFrame({
            "symbol": ["600001.SH"] * 3, "dt": days,
            "open": [10.0, 10.5, 10.4], "low": [9.9, 10.1, 10.2],
            "close": [10.0, 10.4, 10.3], "pre_close": [9.8, 10.0, 10.4],
        })

    def price_limit_daily(self, symbols, start, end):
        days = [DAY, date(2026, 6, 10), date(2026, 6, 11)]
        return pl.DataFrame({
            "symbol": ["600001.SH"] * 3, "dt": days,
            "limit_up": [11.0, 11.0, 11.44], "limit_down": [9.0, 9.0, 9.36],
        })

    def corporate_actions(self, symbols):
        return pl.DataFrame(schema={
            "symbol": pl.Utf8, "ex_date": pl.Date,
            "cash_per_share": pl.Float64, "stock_per_share": pl.Float64,
        })

    def bar_minute_1m(self, symbols, start, end, columns):
        if start.date() == DAY:
            return pl.DataFrame({
                "symbol": ["600001.SH"],
                "ts": [datetime.combine(DAY, time(9, 31))],
                "open": [10.0], "low": [9.95], "close": [10.0],
                "visible_at": [datetime.combine(DAY, time(9, 31))],
            })
        day = start.date()
        return pl.DataFrame({
            "symbol": ["600001.SH", "600001.SH"],
            "ts": [datetime.combine(day, time(11, 25)),
                   datetime.combine(day, time(14, 25))],
            "open": [10.2, 10.1], "low": [10.2, 10.1], "close": [10.2, 10.1],
            "visible_at": [datetime.combine(day, time(11, 25)),
                           datetime.combine(day, time(14, 25))],
        })


def test_counterfactual_builder_labels_both_exit_policies():
    candidates = pl.DataFrame({
        "prediction_date": [date(2026, 6, 8)], "target_session": [DAY],
        "symbol": ["600001.SH"], "rank": [1],
        "decision_price": [10.0], "limit_up": [11.0],
    })
    got = build_counterfactual_outcomes(
        _Query(), candidates, [DAY, date(2026, 6, 10), date(2026, 6, 11)],
    ).row(0, named=True)
    assert got["entry_filled"] is True
    assert got["next_open_exit_date"] == date(2026, 6, 10)
    assert got["two_stage_limit_hold_exit_date"] == date(2026, 6, 10)
    assert got["next_open_return_pct"] > got["two_stage_limit_hold_return_pct"] > 0
    assert got["next_open_exit_price"] > got["two_stage_limit_hold_exit_price"] > 0
    assert got["two_stage_holding_days"] == 1


def test_forward_verdict_accumulates_then_passes_without_authorising_trading():
    days = [date(2026, 1, 1) + __import__("datetime").timedelta(days=i)
            for i in range(120)]
    records = pl.DataFrame([
        {
            "target_session": day, "exit_policy": policy,
            "entry_filled": i < 60, "closed": i < 60,
            "net_return_pct": (1.0 if i < 60 else None),
            "missing_fallbacks": 0, "open_positions_at_end": 0,
        }
        for policy in ("next_open", "two_stage_limit_hold")
        for i, day in enumerate(days)
    ])
    verdict = forward_verdict(records, bootstrap_samples=100)
    assert verdict["research_status"] == "FORWARD_PASS"
    assert verdict["forward_support"] is True
    assert verdict["promote_to_trading"] is False

    accumulating = forward_verdict(
        records.filter(pl.col("target_session") < days[30]), bootstrap_samples=100,
    )
    assert accumulating["research_status"] == "FORWARD_ACCUMULATING"
