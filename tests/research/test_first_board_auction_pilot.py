from datetime import date, datetime, timedelta

import polars as pl

from qsys.research.first_board_auction_pilot import (
    GATE_FEATURES,
    final_snapshots,
    fit_and_score,
    freeze_eval_selections,
    fully_covered_candidates,
)


def _candidates(days: int = 6) -> pl.DataFrame:
    rows = []
    for offset in range(days):
        day = date(2026, 9, 1) + timedelta(days=offset)
        for rank in range(1, 21):
            rows.append({
                "symbol": f"{rank:06d}.SZ", "target_session": day,
                "prediction_date": day - timedelta(days=1), "rank": rank,
                "p_close": 0.2 - rank / 1_000,
                "actual_close": rank == (offset % 4) + 1,
                "actual_touch": rank <= 2, "split": "OBS",
                "model": "lightgbm_price",
            })
    return pl.DataFrame(rows)


def _ticks(candidates: pl.DataFrame, *, drop_last: bool = False) -> pl.DataFrame:
    rows = []
    for row in candidates.iter_rows(named=True):
        if drop_last and row["target_session"] == candidates["target_session"].max() \
                and row["rank"] == 20:
            continue
        day = row["target_session"]
        rows.extend([
            {
                "symbol": row["symbol"], "dt": day,
                "ts": datetime.combine(day, datetime.min.time()).replace(
                    hour=9, minute=24, second=30
                ),
                "bid1": 10.0, "bid_vol1": 100, "last_price": 0.0,
                "volume": 0, "amount": 0.0,
            },
            {
                "symbol": row["symbol"], "dt": day,
                "ts": datetime.combine(day, datetime.min.time()).replace(
                    hour=9, minute=25, second=2
                ),
                "bid1": 10.0, "bid_vol1": 200, "last_price": 10.1,
                "volume": 200, "amount": 202_000.0,
            },
        ])
    return pl.DataFrame(rows)


def test_final_snapshots_take_the_completed_match() -> None:
    candidates = _candidates(1)
    got = final_snapshots(_ticks(candidates))
    assert got.height == 20
    assert got["ts"].dt.time().unique().to_list() == [datetime.min.time().replace(
        hour=9, minute=25, second=2
    )]
    assert got["last_price"].unique().to_list() == [10.1]


def test_full_coverage_drops_an_incomplete_top20_session() -> None:
    candidates = _candidates()
    got = fully_covered_candidates(candidates, _ticks(candidates, drop_last=True))
    assert got["target_session"].n_unique() == 5
    assert got.height == 100
    assert got["target_session"].max() < candidates["target_session"].max()


def test_fit_freezes_later_sessions_and_daily_topk() -> None:
    candidates = _candidates(9)
    rows = candidates.with_columns(
        pl.lit("").alias("invalid_reason"),
        pl.col("rank").cast(pl.Float64).alias("decision_price"),
        pl.lit(30.0).alias("limit_up"),
    )
    for index, name in enumerate(GATE_FEATURES):
        rows = rows.with_columns(
            (pl.col("rank") / (index + 2) + pl.col("actual_close").cast(pl.Int8))
            .alias(name)
        )
    _model, scored, metadata = fit_and_score(rows)
    selections = freeze_eval_selections(scored)
    assert metadata["fit_sessions"] == 6
    assert metadata["eval_sessions"] == 3
    assert scored.filter(pl.col("split") == "PILOT_EVAL").height == 60
    counts = selections.group_by("policy").len().sort("policy")
    assert dict(counts.iter_rows()) == {
        "auction_top1": 3, "auction_top3": 9,
        "rank_top1": 3, "rank_top3": 9,
    }
    assert selections.group_by("policy", "target_session").len()["len"].max() == 3
