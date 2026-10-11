#!/usr/bin/env python3
"""Build one complete 09:30 forward feature frame for frozen Issue #230 models."""

from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl

from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.research.first_board_opening_minute import build_features, load_opening_bars
from qsys.research.first_board_quality import MODEL_FEATURES, quality_features


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument(
        "--top20", type=Path, required=True,
        help="one-day frozen overnight Top20 with probabilities and context features",
    )
    parser.add_argument("--minutes", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"forward features are immutable: {args.out}")

    top20 = pl.read_parquet(args.top20)
    if top20.height != 20 or top20["symbol"].n_unique() != 20 \
            or top20["target_session"].n_unique() != 1:
        raise ValueError("--top20 must contain exactly 20 unique symbols for one session")
    required = {
        "prediction_date", "target_session", "symbol", "rank",
        "p_touch", "p_seal_given_touch", "p_close",
    } | (set(MODEL_FEATURES) - {
        "rank_fraction", "auction_gap", "distance_to_limit",
        "auction_volume_ratio", "auction_gap_rank",
        "distance_to_limit_rank", "auction_volume_rank",
    })
    if missing := required - set(top20.columns):
        raise ValueError(f"overnight Top20 misses columns: {sorted(missing)}")
    bars = load_opening_bars(args.minutes)
    day = top20["target_session"].item(0)
    bars = bars.filter(pl.col("target_session") == day)
    covered = top20.join(
        bars.select("symbol", "target_session"),
        on=["symbol", "target_session"], how="semi",
    )
    if covered.height != 20:
        raise ValueError("09:30 bars do not completely cover the frozen Top20")
    query = WarehouseQuery(Warehouse(args.warehouse))
    opening = build_features(query, top20, bars)
    if opening.filter(pl.col("invalid_reason") != "").height:
        raise ValueError("opening inputs contain missing or invalid values")
    frame = quality_features(opening, top20, top20)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(args.out, compression="zstd")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
