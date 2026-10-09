from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from qsys.research.limit_up_ranking import (
    FEATURE_COLUMNS,
    ResearchConfig,
    TwoStageModel,
    baseline_predictions,
    bootstrap_precision_difference,
    calibration_table,
    heuristic_score,
    materialise_dataset,
    monthly_precision,
    ranking_metrics,
    split_for,
    top20_breakdown,
)


def _sessions(start: date, count: int) -> list[date]:
    days: list[date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _write_partition(root: Path, name: str, frame: pl.DataFrame) -> None:
    path = root / name / "year=2023"
    path.mkdir(parents=True)
    frame.write_parquet(path / "part.parquet")


def _warehouse(root: Path) -> tuple[list[date], list[str]]:
    days = _sessions(date(2023, 1, 2), 72)
    symbols = [
        "600001.SH", "300001.SZ", "688001.SH", "920001.BJ", "600002.SH",
        "600003.SH", "600004.SH", "600005.SH",
    ]
    boards = ["main", "gem", "star", "bse", "main", "main", "main", "main"]
    bars: list[dict[str, object]] = []
    flags: list[dict[str, object]] = []
    statuses: list[dict[str, object]] = []
    for day_index, day in enumerate(days):
        for symbol_index, symbol in enumerate(symbols):
            # A recently listed stock must not enter the universe before it has
            # accumulated the required 60 sessions.
            if symbol == "600005.SH" and day_index < 52:
                continue
            close = 10.0 + day_index * 0.01 + symbol_index
            is_target_close = day_index == 61 and symbol == "600001.SH"
            is_target_touch = is_target_close or (
                day_index == 61 and symbol == "300001.SZ"
            )
            bars.append({
                "dt": day, "symbol": symbol, "open": close - 0.02,
                "high": close + 0.05, "low": close - 0.05, "close": close,
                "amount": 1_000_000.0 + day_index,
                "factor": 1.0, "turnover_ratio": 2.0 + symbol_index,
                "free_turnover_ratio": 2.5 + symbol_index, "volume_ratio": 1.2,
                "market_cap": 10_000_000_000.0,
                "circulating_market_cap": 8_000_000_000.0,
            })
            flags.append({
                "dt": day, "symbol": symbol, "limit_up": close if is_target_close else close * 1.1,
                "is_limit_up": is_target_close,
                "touched_limit_up": is_target_touch,
                "is_broken_board": is_target_touch and not is_target_close,
                "is_st": symbol == "600002.SH",
            })
            statuses.append({
                "dt": day, "symbol": symbol,
                "is_suspended": symbol == "600003.SH",
                "is_delisting": symbol == "600004.SH",
            })
    regime = pl.DataFrame({
        "dt": days,
        "limit_up_count": [10] * len(days), "limit_down_count": [2] * len(days),
        "limit_up_down_ratio": [5.0] * len(days),
        "touched_limit_up_count": [15] * len(days),
        "broken_board_count": [5] * len(days), "broken_board_rate": [1 / 3] * len(days),
        "first_board_count": [8] * len(days), "market_advance_ratio": [0.55] * len(days),
        "market_median_return_1d": [0.002] * len(days),
        "market_total_money": [1e12] * len(days),
        "prior_limitup_nextday_return_mean": [0.01] * len(days),
        "prior_limitup_nextday_return_median": [0.005] * len(days),
        "valid_stock_count": [5000] * len(days),
        "index_ma20_slope_5d": [0.01] * len(days),
    })
    instruments = pl.DataFrame({
        "symbol": symbols, "board": boards,
    })
    _write_partition(root, "tushare_daily", pl.DataFrame(bars))
    _write_partition(root, "daily_flags", pl.DataFrame(flags))
    _write_partition(root, "security_status_daily", pl.DataFrame(statuses))
    _write_partition(root, "market_regime", regime)
    path = root / "instruments"
    path.mkdir()
    instruments.write_parquet(path / "part.parquet")
    return days, symbols


def test_materialise_dataset_uses_next_session_and_filters_universe(tmp_path: Path) -> None:
    days, _symbols = _warehouse(tmp_path / "warehouse")
    output = tmp_path / "dataset.parquet"
    config = ResearchConfig(
        history_start=days[0], feature_start=days[59], feature_end=days[65],
        min_observations=60, threads=1,
    )

    manifest = materialise_dataset(tmp_path / "warehouse", output, config)
    result = pl.read_parquet(output)

    assert manifest["sha256"]
    assert set(result["symbol"]) == {"600001.SH", "300001.SZ", "688001.SH"}
    prediction = result.filter(
        (pl.col("prediction_date") == days[60]) & (pl.col("symbol") == "600001.SH")
    ).row(0, named=True)
    assert prediction["target_session"] == days[61]
    assert prediction["actual_touch"] is True
    assert prediction["actual_close"] is True
    assert prediction["market_state"] == "neutral"
    # The target label is not copied back into the prediction-day rolling feature.
    assert prediction["close_count_5d"] == 0


def test_materialise_dataset_connects_friday_to_monday(tmp_path: Path) -> None:
    days, _symbols = _warehouse(tmp_path / "warehouse")
    friday_index = next(
        index for index in range(59, len(days) - 1) if days[index].weekday() == 4
    )
    output = tmp_path / "dataset.parquet"
    config = ResearchConfig(
        history_start=days[0], feature_start=days[friday_index],
        feature_end=days[friday_index], min_observations=60, threads=1,
    )

    materialise_dataset(tmp_path / "warehouse", output, config)
    result = pl.read_parquet(output)

    assert result["prediction_date"].unique().to_list() == [days[friday_index]]
    assert days[friday_index + 1].weekday() == 0
    assert result["target_session"].unique().to_list() == [days[friday_index + 1]]


def test_materialise_dataset_enforces_exact_history_boundary(tmp_path: Path) -> None:
    days, _symbols = _warehouse(tmp_path / "warehouse")
    output = tmp_path / "dataset.parquet"
    config = ResearchConfig(
        history_start=days[0], feature_start=days[58], feature_end=days[60],
        min_observations=60, threads=1,
    )

    materialise_dataset(tmp_path / "warehouse", output, config)
    result = pl.read_parquet(output)

    assert days[58] not in result["prediction_date"].to_list()
    assert result["prediction_date"].min() == days[59]


def test_split_boundaries_are_frozen() -> None:
    assert split_for(date(2024, 12, 31)) == "TRAIN"
    assert split_for(date(2025, 1, 1)) == "VALID"
    assert split_for(date(2025, 7, 1)) == "TEST"
    assert split_for(date(2026, 1, 1)) == "OBS"
    assert split_for(date(2026, 10, 1)) is None


def _prediction_fixture() -> pl.DataFrame:
    days = [date(2025, 7, 1), date(2025, 7, 2)]
    rows = []
    for model, hit_rank in (("momentum", 2), ("heuristic", 2), ("logistic", 2), ("lightgbm", 1)):
        for day in days:
            for rank in range(1, 4):
                rows.append({
                    "prediction_date": day, "target_session": day + timedelta(days=1),
                    "symbol": f"60000{rank}.SH", "board": "main",
                    "market_state": "neutral", "split": "TEST",
                    "actual_touch": rank == hit_rank, "actual_close": rank == hit_rank,
                    "opened_at_limit": False, "one_price_limit": False,
                    "model": model, "rank": rank,
                    "p_touch": 0.9 if model in {"logistic", "lightgbm"} else None,
                    "p_seal_given_touch": 0.8 if model in {"logistic", "lightgbm"} else None,
                    "p_close": (0.8 if rank == hit_rank else 0.1)
                    if model in {"logistic", "lightgbm"} else None,
                })
    return pl.DataFrame(rows)


def test_ranking_metrics_and_reports() -> None:
    predictions = _prediction_fixture()
    metrics = ranking_metrics(predictions)
    headline = metrics.filter(
        (pl.col("model") == "lightgbm") & (pl.col("label") == "actual_close")
        & (pl.col("k") == 10)
    ).row(0, named=True)
    assert headline["precision"] == pytest.approx(1 / 3)
    assert headline["recall"] == 1.0
    assert monthly_precision(predictions).height == 4
    assert calibration_table(predictions).height > 0
    assert set(top20_breakdown(predictions)["dimension"]) == {"board", "market_state"}


def test_bootstrap_difference_is_paired_by_day() -> None:
    predictions = _prediction_fixture()
    observed, low, high = bootstrap_precision_difference(
        predictions, "lightgbm", "momentum", split="TEST", k=1,
        samples=200, seed=1,
    )
    assert observed == 1.0
    assert low == 1.0
    assert high == 1.0


def test_fixed_heuristic_weights() -> None:
    frame = pl.DataFrame({
        "ret_1d_rank": [1.0], "ret_5d_rank": [0.8], "turnover_rank": [0.6],
        "volume_ratio_rank": [0.4], "touch_count_20d_rank": [0.2],
    })
    assert heuristic_score(frame)[0] == pytest.approx(0.70)


def test_two_stage_model_keeps_close_probability_below_touch() -> None:
    rows = 400
    data = {name: [float((index + offset) % 17) for index in range(rows)]
            for offset, name in enumerate(FEATURE_COLUMNS)}
    data["actual_touch"] = [index % 3 == 0 for index in range(rows)]
    data["actual_close"] = [index % 6 == 0 for index in range(rows)]
    frame = pl.DataFrame(data)
    train, calibration = frame[:300], frame[300:]

    model = TwoStageModel("logistic", seed=210, threads=1).fit(train, calibration)
    p_touch, p_seal, p_close = model.predict(calibration)

    assert len(p_close) == 100
    assert all(0 <= value <= 1 for value in p_touch)
    assert all(0 <= value <= 1 for value in p_seal)
    assert all(close <= touch for close, touch in zip(p_close, p_touch, strict=True))


def test_two_stage_model_imputes_missing_features() -> None:
    rows = 400
    data = {
        name: [None if index % 37 == 0 else float((index + offset) % 17)
               for index in range(rows)]
        for offset, name in enumerate(FEATURE_COLUMNS)
    }
    data["actual_touch"] = [index % 3 == 0 for index in range(rows)]
    data["actual_close"] = [index % 6 == 0 for index in range(rows)]
    frame = pl.DataFrame(data)

    model = TwoStageModel("logistic", seed=210, threads=1).fit(
        frame[:300], frame[300:]
    )
    p_touch, p_seal, p_close = model.predict(frame[300:])

    assert all(math.isfinite(value) for value in (*p_touch, *p_seal, *p_close))
