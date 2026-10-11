"""Expanded 09:30 QMT opening-auction pilot for Issue #230.

QMT's 09:30 one-minute bar is the completed opening-auction match.  Unlike
the short-lived tick history, the one-minute archive rolls for about a year.
This module deliberately uses only that single bar for the decision and keeps
the following 09:31 continuous minute for execution.
"""

from __future__ import annotations

from datetime import time
from pathlib import Path

import polars as pl

from qsys.research.first_board_auction_pilot import fit_and_score
from qsys.research.first_board_intraday import MAX_POSITIONS, TOP_K


AUCTION_BAR_AT = time(9, 30)
ENTRY_BAR_AT = time(9, 31)
GATE_FEATURES = (
    "p_close",
    "rank_fraction",
    "auction_gap",
    "auction_volume_ratio",
    "auction_amount_ratio",
)


def load_opening_bars(path: Path) -> pl.DataFrame:
    """Load only valid 09:30 auction bars from downloader output."""
    files = sorted(path.glob("*.parquet"))
    if not files:
        raise ValueError(f"no QMT minute parquet files: {path}")
    lazy = pl.scan_parquet(files)
    required = {"symbol", "dt", "open", "high", "low", "close", "volume", "amount"}
    if missing := required - set(lazy.collect_schema().names()):
        raise ValueError(f"QMT minute bars miss columns: {sorted(missing)}")
    bars = (
        lazy.filter(pl.col("dt").dt.time() == AUCTION_BAR_AT)
        .filter(
            (pl.col("open") > 0)
            & (pl.col("volume") > 0)
            & (pl.col("amount") > 0)
        )
        .with_columns(pl.col("dt").dt.date().alias("target_session"))
        .collect()
        .sort("symbol", "dt")
    )
    duplicates = bars.group_by("symbol", "target_session").len().filter(
        pl.col("len") != 1
    )
    if duplicates.height:
        raise ValueError("QMT opening bars contain duplicate symbol/session rows")
    unequal = bars.filter(
        (pl.col("open") != pl.col("high"))
        | (pl.col("open") != pl.col("low"))
        | (pl.col("open") != pl.col("close"))
    )
    if unequal.height:
        raise ValueError("09:30 QMT bars are not single-price auction matches")
    return bars


def fully_covered_candidates(
    candidates: pl.DataFrame, bars: pl.DataFrame,
) -> pl.DataFrame:
    """Keep only sessions where all frozen Top20 members have an auction bar."""
    keys = bars.select("symbol", "target_session").unique()
    covered = candidates.join(keys, on=["symbol", "target_session"], how="inner")
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


def build_features(query, candidates: pl.DataFrame, bars: pl.DataFrame) -> pl.DataFrame:
    """Build features observable when the completed 09:30 match is published."""
    if candidates.is_empty():
        raise ValueError("no fully covered Top20 sessions")
    opening = bars.select(
        "symbol", "target_session",
        pl.col("dt").alias("auction_ts"),
        pl.col("close").alias("auction_price"),
        pl.col("volume").alias("auction_volume"),
        pl.col("amount").alias("auction_amount"),
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
        candidates.join(opening, on=["symbol", "target_session"], how="left")
        .join(previous, on=["symbol", "prediction_date"], how="left")
        .join(limits, on=["symbol", "target_session"], how="left")
    )
    required = (
        "auction_price", "auction_volume", "auction_amount", "previous_close",
        "previous_volume", "previous_amount", "limit_up",
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
        pl.col("auction_price").alias("decision_price"),
    ).sort("target_session", "rank")


def fit_opening_gate(features: pl.DataFrame, *, fit_fraction: float = 2 / 3):
    """Use the pilot's fixed classifier with the reduced 09:30 feature contract."""
    return fit_and_score(
        features, fit_fraction=fit_fraction, gate_features=GATE_FEATURES,
    )


def freeze_eval_selections(scored: pl.DataFrame) -> pl.DataFrame:
    evaluated = scored.filter(pl.col("split") == "PILOT_EVAL")
    frames = []
    for policy, column, descending, limit in (
        ("rank_top1", "rank", False, 1),
        ("rank_top3", "rank", False, 3),
        ("opening_top1", "gate_score", True, 1),
        ("opening_top3", "gate_score", True, MAX_POSITIONS),
    ):
        frames.append(
            evaluated.sort(
                ["target_session", column, "rank"],
                descending=[False, descending, False],
            ).with_columns(
                (pl.int_range(pl.len()).over("target_session") + 1).alias(
                    "selection_rank"
                ),
                pl.lit(policy).alias("policy"),
            ).filter(pl.col("selection_rank") <= limit)
        )
    return pl.concat(frames, how="vertical_relaxed").sort(
        "policy", "target_session", "selection_rank"
    )
