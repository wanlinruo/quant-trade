#!/usr/bin/env python3
"""Run the isolated, directional Issue #230 QMT auction pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

import joblib
import polars as pl

from qsys.backtest.entry_fill import EntryFill, EntryFillMode
from qsys.backtest.runner import execute_backtest, visible_entry_minute_loader
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.research.first_board_auction_pilot import (
    ENTRY_BAR_AT,
    GATE_FEATURES,
    build_features,
    final_snapshots,
    fit_and_score,
    freeze_eval_selections,
    fully_covered_candidates,
    load_ticks,
)
from qsys.research.first_board_intraday import MODEL, TOP_K, load_candidates
from qsys.research.limit_up_continuation import summarize_run
from qsys.strategy.deps import build_deps


STRATEGY = Path("scripts/issue_230_auction_pilot_strategy.py")
PRIMARY_POLICY = "auction_top1"
PRIMARY_COST = 0.005


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(path.glob("*.parquet")):
        digest.update(item.name.encode())
        digest.update(bytes.fromhex(_sha256(item)))
    return digest.hexdigest()


def _source_sha(value: str | None) -> str:
    if value is None:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            check=False,
        )
        value = result.stdout.strip() if result.returncode == 0 else "unknown"
    if value != "unknown" and not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("--source-sha must be a full lowercase Git SHA")
    return value


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _empty_output(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"output directory must be empty: {path}")
    path.mkdir(parents=True, exist_ok=True)


def _matrix() -> list[tuple[str, str, float]]:
    return [
        ("auction_top1", "next_open", 0.0),
        ("auction_top1", "next_open", 0.005),
        ("auction_top1", "next_open", 0.01),
        ("auction_top1", "11:25", 0.005),
        ("auction_top1", "14:25", 0.005),
        ("auction_top3", "next_open", 0.005),
        ("rank_top1", "next_open", 0.005),
        ("rank_top3", "next_open", 0.005),
    ]


def _fill_stat(run_dir: Path, name: str) -> int:
    config = json.loads((run_dir / "config.json").read_text())
    values = {
        key: int(value) for item in str(config.get("entry_fill_stats", "")).split(",")
        if "=" in item for key, value in [item.split("=", 1)]
    }
    return values.get(name, 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--ranking", type=Path, required=True)
    parser.add_argument("--ticks", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cash", type=float, default=100_000.0)
    parser.add_argument("--bootstrap-samples", type=int, default=500)
    parser.add_argument("--source-sha")
    args = parser.parse_args()
    _empty_output(args.out)

    query = WarehouseQuery(Warehouse(args.warehouse))
    ticks = load_ticks(args.ticks)
    all_candidates = load_candidates(args.ranking).filter(
        (pl.col("split") == "OBS")
        & (pl.col("target_session") >= ticks["dt"].min())
        & (pl.col("target_session") <= ticks["dt"].max())
    )
    final_keys = final_snapshots(ticks).select(
        "symbol", pl.col("dt").alias("target_session")
    ).unique()
    coverage = (
        all_candidates.group_by("target_session").len().rename({"len": "expected"})
        .join(
            all_candidates.join(
                final_keys, on=["symbol", "target_session"], how="inner",
            ).group_by("target_session").len().rename({"len": "covered"}),
            on="target_session", how="left",
        )
        .with_columns(
            pl.col("covered").fill_null(0),
            (pl.col("covered").fill_null(0) == pl.col("expected")).alias("complete"),
        )
        .sort("target_session")
    )
    coverage.write_csv(args.out / "coverage.csv")
    candidates = fully_covered_candidates(all_candidates, ticks)
    features = build_features(query, candidates, ticks)
    model, scored, freeze = fit_and_score(features)
    selections = freeze_eval_selections(scored)
    features.write_parquet(args.out / "auction_features.parquet")
    scored.write_parquet(args.out / "scored_candidates.parquet")
    selections.write_parquet(args.out / "frozen_selections.parquet")
    joblib.dump(model, args.out / "gate_model.joblib")
    classifier = model.named_steps["logisticregression"]
    pl.DataFrame({
        "feature": GATE_FEATURES,
        "standardized_coefficient": classifier.coef_[0],
    }).write_csv(args.out / "gate_coefficients.csv")
    _write_json(args.out / "freeze.json", freeze)
    selections.group_by("policy").agg(
        pl.len().alias("selected"),
        pl.col("target_session").n_unique().alias("sessions"),
        pl.col("actual_close").sum().alias("first_boards"),
        pl.col("actual_close").mean().alias("precision"),
    ).sort("policy").write_csv(args.out / "selection_summary.csv")

    eval_days = sorted(
        scored.filter(pl.col("split") == "PILOT_EVAL")["target_session"].unique().to_list()
    )
    _, _, _, sessions, _, _ = build_deps(args.warehouse)
    positions = {day: index for index, day in enumerate(sessions)}
    end = sessions[positions[eval_days[-1]] + 1]
    rows: list[dict[str, object]] = []
    for policy, exit_policy, cost in _matrix():
        name = (
            f"{policy}-{exit_policy.replace(':', '')}"
            f"-cost{int(round(cost * 10_000)):04d}bp"
        )
        run_dir = args.out / "runs" / name
        execute_backtest(
            STRATEGY, start=eval_days[0], end=end, cash=args.cash,
            warehouse=args.warehouse, out_dir=run_dir,
            params={
                "selection_path": str(args.out / "frozen_selections.parquet"),
                "selection_policy": policy, "position_weight": 0.10,
                "entry_price_buffer": 0.03, "exit_policy": exit_policy,
            },
            entry_rule=EntryFill(
                EntryFillMode.CONTINUOUS, minute_at=ENTRY_BAR_AT,
                price_stress=cost, fallback_on_missing=False,
            ),
            entry_minutes=visible_entry_minute_loader(query),
        )
        row = summarize_run(
            run_dir, split="PILOT_EVAL", exit_policy=exit_policy,
            bootstrap_samples=max(args.bootstrap_samples, 100),
        )
        row.update({
            "policy": policy, "entry_cost_stress": cost,
            "missing_entry_minutes": _fill_stat(run_dir, "missing_minute"),
            "run_dir": str(run_dir),
        })
        rows.append(row)
    summary = pl.DataFrame(rows).sort("policy", "exit_policy", "entry_cost_stress")
    summary.write_csv(args.out / "trading_summary.csv")
    primary = summary.filter(
        (pl.col("policy") == PRIMARY_POLICY)
        & (pl.col("exit_policy") == "next_open")
        & ((pl.col("entry_cost_stress") - PRIMARY_COST).abs() < 1e-12)
    ).row(0, named=True)
    verdict = {
        "schema_version": 1,
        "research_status": "PILOT_DIRECTIONAL_POSITIVE" if (
            primary["total_return"] > 0 and primary["closed_lots"] >= 3
        ) else "PILOT_DIRECTIONAL_NEGATIVE",
        "primary_policy": PRIMARY_POLICY,
        "primary_entry_cost_stress": PRIMARY_COST,
        "primary_total_return": primary["total_return"],
        "primary_closed_lots": primary["closed_lots"],
        "historical_support": False,
        "promote_to_shadow": False,
        "promote_to_trading": False,
        "limitations": [
            "only recent rolling QMT history",
            f"only {len(eval_days)} held-out sessions",
            "pilot result cannot replace frozen TEST/OBS historical adjudication",
        ],
    }
    _write_json(args.out / "verdict.json", verdict)
    source_sha = _source_sha(args.source_sha)
    manifest = {
        "schema_version": 1,
        "research": "issue-230-qmt-auction-pilot",
        "git_sha": source_sha,
        "warehouse": str(args.warehouse),
        "ranking": str(args.ranking),
        "ranking_sha256": _sha256(args.ranking),
        "ticks": str(args.ticks),
        "ticks_sha256": _directory_sha256(args.ticks),
        "coverage": {
            "complete_sessions": candidates["target_session"].n_unique(),
            "complete_rows": candidates.height,
            **freeze,
        },
        "fixed_contract": {
            "model": MODEL, "overnight_top_k": TOP_K,
            "decision": "09:25 final QMT auction snapshot",
            "entry": "first continuous minute visible at 09:31",
            "max_positions": 3, "position_weight": 0.10,
            "primary_policy": PRIMARY_POLICY,
            "primary_entry_cost_stress": PRIMARY_COST,
        },
        "artifacts": {
            name: _sha256(args.out / name) for name in (
                "coverage.csv", "auction_features.parquet", "scored_candidates.parquet",
                "frozen_selections.parquet", "gate_model.joblib", "freeze.json",
                "selection_summary.csv", "trading_summary.csv", "verdict.json",
            )
        },
    }
    _write_json(args.out / "manifest.json", manifest)
    (args.out / "README.md").write_text(
        "# Issue #230 QMT 竞价小样本试验\n\n"
        f"结果：**{verdict['research_status']}**。本产物只有方向性，"
        "不构成历史支持，不授权影子或交易，也不替代购买后的冻结 TEST/OBS 裁决。\n"
    )
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
