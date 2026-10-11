from datetime import date, datetime, timedelta

import polars as pl
import pytest

from qsys.research.first_board_opening_minute import (
    GATE_FEATURES,
    fit_opening_gate,
    freeze_eval_selections,
    fully_covered_candidates,
    load_opening_bars,
)


def _candidates(days: int = 9) -> pl.DataFrame:
    rows = []
    for offset in range(days):
        day = date(2026, 1, 5) + timedelta(days=offset)
        for rank in range(1, 21):
            rows.append({
                "symbol": f"{rank:06d}.SZ", "target_session": day,
                "prediction_date": day - timedelta(days=1), "rank": rank,
                "p_close": 0.2 - rank / 1_000,
                "actual_close": rank == offset % 5 + 1,
                "actual_touch": rank <= 2, "split": "OBS",
                "model": "lightgbm_price",
            })
    return pl.DataFrame(rows)


def _bars(candidates: pl.DataFrame, *, drop_last: bool = False) -> pl.DataFrame:
    rows = []
    for row in candidates.iter_rows(named=True):
        if drop_last and row["target_session"] == candidates["target_session"].max() \
                and row["rank"] == 20:
            continue
        rows.append({
            "symbol": row["symbol"], "target_session": row["target_session"],
            "dt": datetime.combine(row["target_session"], datetime.min.time()).replace(
                hour=9, minute=30
            ),
            "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
            "volume": 100, "amount": 100_000.0,
        })
    return pl.DataFrame(rows)


def test_load_opening_bars_keeps_only_single_price_0930(tmp_path) -> None:
    frame = pl.DataFrame({
        "symbol": ["000001.SZ", "000001.SZ"],
        "dt": [datetime(2026, 1, 5, 9, 30), datetime(2026, 1, 5, 9, 31)],
        "open": [10.0, 10.1], "high": [10.0, 10.2],
        "low": [10.0, 10.0], "close": [10.0, 10.1],
        "volume": [100, 200], "amount": [100_000.0, 202_000.0],
    })
    frame.write_parquet(tmp_path / "minutes.parquet")
    got = load_opening_bars(tmp_path)
    assert got.height == 1
    assert got[0, "target_session"] == date(2026, 1, 5)


def test_load_opening_bars_rejects_non_auction_ohlc(tmp_path) -> None:
    frame = pl.DataFrame({
        "symbol": ["000001.SZ"], "dt": [datetime(2026, 1, 5, 9, 30)],
        "open": [10.0], "high": [10.1], "low": [10.0], "close": [10.0],
        "volume": [100], "amount": [100_000.0],
    })
    frame.write_parquet(tmp_path / "minutes.parquet")
    with pytest.raises(ValueError, match="single-price"):
        load_opening_bars(tmp_path)


def test_full_coverage_drops_incomplete_top20_session() -> None:
    candidates = _candidates()
    got = fully_covered_candidates(candidates, _bars(candidates, drop_last=True))
    assert got["target_session"].n_unique() == 8
    assert got.height == 160


def test_opening_gate_freezes_later_daily_top1_and_top3() -> None:
    rows = _candidates().with_columns(
        pl.lit("").alias("invalid_reason"),
        pl.col("rank").cast(pl.Float64).alias("decision_price"),
        pl.lit(30.0).alias("limit_up"),
    )
    for index, name in enumerate(GATE_FEATURES):
        rows = rows.with_columns(
            (pl.col("rank") / (index + 2) + pl.col("actual_close").cast(pl.Int8))
            .alias(name)
        )
    _model, scored, metadata = fit_opening_gate(rows)
    selections = freeze_eval_selections(scored)
    assert metadata["fit_sessions"] == 6
    assert metadata["eval_sessions"] == 3
    counts = dict(
        selections.group_by("policy").len().select("policy", "len").iter_rows()
    )
    assert counts == {
        "opening_top1": 3, "opening_top3": 9,
        "rank_top1": 3, "rank_top3": 9,
    }
