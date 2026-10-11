#!/usr/bin/env python3
"""Write one immutable, research-only forward decision for Issue #230."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import polars as pl

from qsys.research.first_board_quality import (
    EXIT_POLICIES,
    MODEL_FEATURES,
    predict_bundle,
    select_daily,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"forward decision is immutable: {args.out}")

    manifest = json.loads((args.run / "manifest.json").read_text())
    frame = pl.read_parquet(args.features)
    missing = set(MODEL_FEATURES) - set(frame.columns)
    if missing:
        raise ValueError(f"forward features miss columns: {sorted(missing)}")
    days = frame["target_session"].unique().to_list()
    if len(days) != 1:
        raise ValueError("forward scoring requires exactly one target session")
    frozen_through = __import__("datetime").date.fromisoformat(
        manifest["frozen_through_session"]
    )
    if days[0] <= frozen_through:
        raise ValueError("forward session must be strictly after the frozen development data")
    if frame.height != 20 or frame["symbol"].n_unique() != 20:
        raise ValueError("forward scoring requires a complete frozen Top20")

    all_scores = []
    selections = []
    for policy in EXIT_POLICIES:
        model_path = args.run / "models" / f"{policy}.joblib"
        expected_sha = manifest["model_sha256"][policy]
        if _sha256(model_path) != expected_sha:
            raise ValueError(f"{policy} model hash does not match the manifest")
        bundle = joblib.load(model_path)
        scored = predict_bundle(frame, bundle)
        selected = select_daily(scored, bundle.thresholds)
        all_scores.append(scored)
        selections.append(selected)
    args.out.mkdir(parents=True)
    pl.concat(all_scores, how="vertical_relaxed").write_parquet(
        args.out / "scores.parquet", compression="zstd",
    )
    picks = pl.concat(selections, how="vertical_relaxed")
    picks.write_parquet(args.out / "decisions.parquet", compression="zstd")
    record = {
        "schema_version": 1, "target_session": str(days[0]),
        "research_only": True, "orders_created": 0,
        "source_run_sha256": _sha256(args.run / "manifest.json"),
        "features_sha256": _sha256(args.features),
        "selected": {
            policy: picks.filter(pl.col("exit_policy") == policy)["symbol"].to_list()
            for policy in EXIT_POLICIES
        },
    }
    (args.out / "record.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
