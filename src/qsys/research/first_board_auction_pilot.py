"""Small, fail-closed QMT auction pilot for Issue #230.

This is deliberately not the historical adjudication.  It uses only fully
covered recent Top20 sessions, freezes a chronological fit/evaluation split,
and never authorizes shadow or trading promotion.
"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import numpy as np
import polars as pl
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from qsys.research.first_board_intraday import MAX_POSITIONS, TOP_K


FINAL_AT = time(9, 25, 5)
PRE_FINAL_AT = time(9, 24, 30)
ENTRY_BAR_AT = time(9, 31)
GATE_FEATURES = (
    "p_close", "rank_fraction", "auction_gap", "auction_volume_ratio",
    "auction_amount_ratio", "price_revision_092430", "match_growth_092430",
)


def load_ticks(path: Path) -> pl.DataFrame:
    files = sorted(path.glob("*.parquet"))
    if not files:
        raise ValueError(f"no QMT auction parquet files: {path}")
    ticks = pl.concat([pl.read_parquet(item) for item in files], how="vertical_relaxed")
    required = {
        "symbol", "dt", "ts", "bid1", "bid_vol1", "last_price", "volume",
        "amount",
    }
    if missing := required - set(ticks.columns):
        raise ValueError(f"auction ticks miss columns: {sorted(missing)}")
    duplicates = ticks.group_by("symbol", "ts").len().filter(pl.col("len") != 1)
    if duplicates.height:
        raise ValueError("auction ticks contain duplicate symbol/timestamp rows")
    return ticks.sort("symbol", "ts")


def final_snapshots(ticks: pl.DataFrame) -> pl.DataFrame:
    return (
        ticks.filter(
            (pl.col("ts").dt.time() <= FINAL_AT)
            & (pl.col("last_price") > 0)
            & (pl.col("volume") > 0)
            & (pl.col("amount") > 0)
        )
        .sort("ts")
        .group_by("symbol", "dt", maintain_order=True)
        .last()
    )


def fully_covered_candidates(candidates: pl.DataFrame, ticks: pl.DataFrame) -> pl.DataFrame:
    """Keep sessions where every frozen Top20 member has a final snapshot."""
    final = final_snapshots(ticks).select("symbol", "dt").unique()
    covered = candidates.join(
        final, left_on=["symbol", "target_session"], right_on=["symbol", "dt"],
        how="inner",
    )
    expected = candidates.group_by("target_session").agg(
        pl.len().alias("expected"), pl.col("symbol").n_unique().alias("symbols"),
    )
    have = covered.group_by("target_session").len().rename({"len": "covered"})
    complete = (
        expected.join(have, on="target_session", how="left")
        .with_columns(pl.col("covered").fill_null(0))
        .filter(
            (pl.col("expected") == TOP_K)
            & (pl.col("symbols") == TOP_K)
            & (pl.col("covered") == TOP_K)
        )["target_session"]
    )
    return candidates.filter(pl.col("target_session").is_in(complete.to_list())).sort(
        "target_session", "rank"
    )


def build_features(query, candidates: pl.DataFrame, ticks: pl.DataFrame) -> pl.DataFrame:
    """Build features available after the 09:25 final auction snapshot."""
    if candidates.is_empty():
        raise ValueError("no fully covered Top20 sessions")
    final = final_snapshots(ticks).select(
        "symbol", pl.col("dt").alias("target_session"),
        pl.col("ts").alias("auction_ts"),
        pl.col("last_price").alias("auction_price"),
        pl.col("volume").alias("auction_volume"),
        pl.col("amount").alias("auction_amount"),
    )
    pre = (
        ticks.filter(
            (pl.col("ts").dt.time() <= PRE_FINAL_AT)
            & (pl.col("bid1") > 0) & (pl.col("bid_vol1") > 0)
        )
        .sort("ts")
        .group_by("symbol", "dt", maintain_order=True)
        .last()
        .select(
            "symbol", pl.col("dt").alias("target_session"),
            pl.col("ts").alias("pre_final_ts"),
            pl.col("bid1").alias("virtual_price_092430"),
            pl.col("bid_vol1").alias("matched_volume_092430"),
        )
    )
    symbols = candidates["symbol"].unique().to_list()
    daily = query.bar_daily(
        symbols, candidates["prediction_date"].min(), candidates["target_session"].max(),
        columns=("symbol", "dt", "close", "volume", "amount"),
    )
    previous = daily.select(
        "symbol", pl.col("dt").alias("prediction_date"),
        pl.col("close").alias("previous_close"),
        pl.col("volume").alias("previous_volume"),
        pl.col("amount").alias("previous_amount"),
    )
    limits = query.price_limit_daily(
        symbols, candidates["target_session"].min(), candidates["target_session"].max(),
    ).select("symbol", pl.col("dt").alias("target_session"), "limit_up")
    joined = (
        candidates.join(final, on=["symbol", "target_session"], how="left")
        .join(pre, on=["symbol", "target_session"], how="left")
        .join(previous, on=["symbol", "prediction_date"], how="left")
        .join(limits, on=["symbol", "target_session"], how="left")
    )
    required = (
        "auction_price", "auction_volume", "auction_amount", "virtual_price_092430",
        "matched_volume_092430", "previous_close", "previous_volume",
        "previous_amount", "limit_up",
    )
    valid = pl.all_horizontal([
        pl.col(name).is_not_null() & pl.col(name).is_finite() & (pl.col(name) > 0)
        for name in required
    ])
    return joined.with_columns(
        pl.when(valid).then(pl.lit("")).otherwise(
            pl.lit("missing or non-positive auction/daily input")
        ).alias("invalid_reason"),
        (pl.col("rank") / TOP_K).alias("rank_fraction"),
        (pl.col("auction_price") / pl.col("previous_close") - 1).alias("auction_gap"),
        (pl.col("auction_volume") / pl.col("previous_volume")).alias(
            "auction_volume_ratio"
        ),
        (pl.col("auction_amount") / pl.col("previous_amount")).alias(
            "auction_amount_ratio"
        ),
        (
            pl.col("auction_amount")
            / (pl.col("auction_volume") * 100.0)
            / pl.col("auction_price") - 1
        ).alias("auction_vwap_deviation"),
        (pl.col("auction_price") / pl.col("virtual_price_092430") - 1).alias(
            "price_revision_092430"
        ),
        (pl.col("auction_volume") / pl.col("matched_volume_092430")).alias(
            "match_growth_092430"
        ),
        pl.col("auction_price").alias("decision_price"),
    ).sort("target_session", "rank")


def fit_and_score(features: pl.DataFrame, *, fit_fraction: float = 2 / 3):
    eligible = features.filter(
        (pl.col("invalid_reason") == "")
        & pl.all_horizontal([pl.col(name).is_finite() for name in GATE_FEATURES])
    )
    days = sorted(eligible["target_session"].unique().to_list())
    if len(days) < 6:
        raise ValueError("auction pilot needs at least six fully covered sessions")
    cut = max(3, min(len(days) - 3, int(len(days) * fit_fraction)))
    fit_days, eval_days = days[:cut], days[cut:]
    fit = eligible.filter(pl.col("target_session").is_in(fit_days))
    if fit["actual_close"].n_unique() != 2:
        raise ValueError("auction pilot fit window needs both label classes")
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=0.5, class_weight="balanced", max_iter=2_000, random_state=230,
        ),
    )
    model.fit(fit.select(GATE_FEATURES).to_numpy(), fit["actual_close"].to_numpy())
    scores = model.predict_proba(eligible.select(GATE_FEATURES).to_numpy())[:, 1]
    scored = eligible.with_columns(
        pl.Series("gate_score", scores),
        pl.when(pl.col("target_session").is_in(fit_days))
        .then(pl.lit("PILOT_FIT")).otherwise(pl.lit("PILOT_EVAL")).alias("split"),
    )
    metadata = {
        "fit_first": str(fit_days[0]), "fit_last": str(fit_days[-1]),
        "eval_first": str(eval_days[0]), "eval_last": str(eval_days[-1]),
        "fit_sessions": len(fit_days), "eval_sessions": len(eval_days),
        "fit_rows": fit.height,
        "fit_positive": int(fit["actual_close"].sum()),
    }
    return model, scored, metadata


def freeze_eval_selections(scored: pl.DataFrame) -> pl.DataFrame:
    evaluated = scored.filter(pl.col("split") == "PILOT_EVAL")
    frames = []
    for policy, column, descending, limit in (
        ("rank_top1", "rank", False, 1),
        ("rank_top3", "rank", False, 3),
        ("auction_top1", "gate_score", True, 1),
        ("auction_top3", "gate_score", True, MAX_POSITIONS),
    ):
        frame = (
            evaluated.sort(
                ["target_session", column, "rank"],
                descending=[False, descending, False],
            )
            .with_columns(
                (pl.int_range(pl.len()).over("target_session") + 1).alias(
                    "selection_rank"
                ),
                pl.lit(policy).alias("policy"),
            )
            .filter(pl.col("selection_rank") <= limit)
        )
        frames.append(frame)
    return pl.concat(frames, how="vertical_relaxed").sort(
        "policy", "target_session", "selection_rank"
    )
