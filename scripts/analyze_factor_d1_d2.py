#!/usr/bin/env python3
"""用 QMT 单次复权因子独立验真 D-1 / D-2 候选。

输入均为只读：Task 9 的 ``factor_verification.parquet`` 与 Windows QMT
``get_divid_factors`` 原始归档。输出只描述来源支持关系，不回写累计因子。
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl


REL_THRESHOLD = 0.01
MATCH_TOLERANCE = 0.001


def _relative(left: str, right: str) -> pl.Expr:
    return (
        (pl.col(left) - pl.col(right)).abs()
        / pl.max_horizontal(pl.col(right).abs(), pl.lit(1e-12))
    )


def _summary(frame: pl.DataFrame) -> dict[str, Any]:
    counts = frame.group_by("attribution").len().sort("attribution")
    shapes = frame.group_by("shape").len().sort("shape")
    cross = frame.group_by("shape", "attribution").len().sort(
        "shape", "attribution"
    )
    return {
        "rows": frame.height,
        "symbols": frame["symbol"].n_unique() if frame.height else 0,
        "before_listing": frame.filter(pl.col("days_from_listing") < 0).height,
        "on_listing_date": frame.filter(pl.col("days_from_listing") == 0).height,
        "after_listing": frame.filter(pl.col("days_from_listing") > 0).height,
        "listing_date_missing": frame.filter(pl.col("list_date").is_null()).height,
        "max_original_relative_difference": (
            float(frame["rel_diff"].max()) if frame.height else 0.0
        ),
        "attribution": {
            row["attribution"]: row["len"] for row in counts.iter_rows(named=True)
        },
        "shape": {
            row["shape"]: row["len"] for row in shapes.iter_rows(named=True)
        },
        "shape_by_attribution": [
            {"shape": row["shape"], "attribution": row["attribution"],
             "rows": row["len"]}
            for row in cross.iter_rows(named=True)
        ],
        "samples": frame.sort("rel_diff", descending=True).select(
            "symbol", "dt", "shape", "direct", "computed", "qmt_factor",
            "direct_qmt_rel", "computed_qmt_rel", "attribution", "rel_diff",
        ).head(20).to_dicts(),
    }


def analyze(verification_path: Path, dividend_paths: list[Path],
            security_master_path: Path | None = None,
            details_path: Path | None = None,
            feedback_path: Path | None = None) -> dict[str, Any]:
    verification = pl.read_parquet(verification_path).filter(
        pl.col("rel_diff") > REL_THRESHOLD
    )
    raw = pl.concat(
        [pl.read_parquet(path) for path in dividend_paths], how="vertical_relaxed"
    ).select(
        "symbol", pl.col("ex_date").alias("dt"),
        pl.col("factor").alias("qmt_factor"),
    )
    qmt = raw.group_by("symbol", "dt").agg(
        pl.col("qmt_factor").filter(pl.col("qmt_factor") > 0).first(),
        pl.col("qmt_factor").filter(pl.col("qmt_factor") > 0).n_unique()
            .alias("qmt_factor_versions"),
    )
    qmt_symbols = raw.select("symbol").unique().with_columns(
        pl.lit(True).alias("qmt_symbol_present")
    )
    joined = verification.join(qmt, on=["symbol", "dt"], how="left").join(
        qmt_symbols, on="symbol", how="left"
    )
    if security_master_path is not None:
        security = pl.read_parquet(
            security_master_path, columns=["symbol", "list_date"]
        )
        joined = joined.join(security, on="symbol", how="left")
    else:
        joined = joined.with_columns(pl.lit(None).cast(pl.Date).alias("list_date"))
    joined = joined.with_columns(
        _relative("direct", "qmt_factor").alias("direct_qmt_rel"),
        _relative("computed", "qmt_factor").alias("computed_qmt_rel"),
        (pl.col("dt") - pl.col("list_date")).dt.total_days()
        .alias("days_from_listing"),
        pl.when((pl.col("direct") - 1).abs() <= REL_THRESHOLD)
        .then(pl.lit("price_gap_factor_static"))
        .when((pl.col("computed") - 1).abs() <= REL_THRESHOLD)
        .then(pl.lit("factor_jump_without_price_gap"))
        .otherwise(pl.lit("jump_magnitude_mismatch"))
        .alias("shape"),
    ).with_columns(
        pl.when(pl.col("qmt_factor").is_null()
                & pl.col("qmt_symbol_present").fill_null(False))
        .then(pl.lit("qmt_no_event_on_date"))
        .when(pl.col("qmt_factor").is_null()).then(pl.lit("qmt_symbol_missing"))
        .when(pl.col("qmt_factor_versions") > 1).then(pl.lit("qmt_event_conflict"))
        .when((pl.col("direct_qmt_rel") <= MATCH_TOLERANCE)
              & (pl.col("computed_qmt_rel") > MATCH_TOLERANCE))
        .then(pl.lit("qmt_supports_tushare_factor"))
        .when((pl.col("computed_qmt_rel") <= MATCH_TOLERANCE)
              & (pl.col("direct_qmt_rel") > MATCH_TOLERANCE))
        .then(pl.lit("qmt_supports_price_gap"))
        .when((pl.col("computed_qmt_rel") <= MATCH_TOLERANCE)
              & (pl.col("direct_qmt_rel") <= MATCH_TOLERANCE))
        .then(pl.lit("qmt_matches_both"))
        .otherwise(pl.lit("qmt_matches_neither"))
        .alias("attribution"),
    ).with_columns(
        pl.when(pl.col("symbol").str.ends_with(".BJ")
                & pl.col("symbol").str.starts_with("92"))
        .then(pl.lit("d2_bj_92"))
        .when(pl.col("symbol").str.ends_with(".BJ"))
        .then(pl.lit("d2_bj_legacy"))
        .when(pl.col("dt").dt.year().is_between(2005, 2006))
        .then(pl.lit("share_reform_2005_2006"))
        .otherwise(pl.lit("d1_non_reform_a_share"))
        .alias("scope"),
    )
    if details_path is not None:
        details_path.parent.mkdir(parents=True, exist_ok=True)
        joined.sort("scope", "symbol", "dt").write_parquet(details_path)
    if feedback_path is not None:
        feedback_path.parent.mkdir(parents=True, exist_ok=True)
        joined.filter(
            (pl.col("scope") == "d1_non_reform_a_share")
            & (pl.col("attribution") == "qmt_supports_price_gap")
        ).select(
            "symbol", "dt", "shape", "direct", "computed", "qmt_factor",
            "direct_qmt_rel", "computed_qmt_rel",
        ).sort("symbol", "dt").write_csv(feedback_path)

    scopes = {}
    for scope in (
        "d1_non_reform_a_share", "d2_bj_92", "d2_bj_legacy",
        "share_reform_2005_2006",
    ):
        scopes[scope] = _summary(joined.filter(pl.col("scope") == scope))
    return {
        "analysis": "D-1 / D-2 independent QMT factor attribution",
        "generated_at": datetime.now().astimezone().isoformat(),
        "read_only": True,
        "thresholds": {
            "candidate_relative_difference": REL_THRESHOLD,
            "qmt_match_relative_tolerance": MATCH_TOLERANCE,
        },
        "inputs": {
            "verification": str(verification_path),
            "qmt_dividends": [str(path) for path in dividend_paths],
            "qmt_rows": raw.height,
            "qmt_symbols": raw["symbol"].n_unique(),
            "qmt_min_date": raw["dt"].min(),
            "qmt_max_date": raw["dt"].max(),
            "security_master": (
                str(security_master_path) if security_master_path else None
            ),
        },
        "all_candidates": _summary(joined),
        "scopes": scopes,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument("--qmt-dividends", type=Path, nargs="+", required=True)
    parser.add_argument("--security-master", type=Path)
    parser.add_argument("--details-output", type=Path)
    parser.add_argument("--feedback-output", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = analyze(
        args.verification, args.qmt_dividends, args.security_master,
        args.details_output, args.feedback_output,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
        temporary.write_text(payload)
        os.replace(temporary, args.output)
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
