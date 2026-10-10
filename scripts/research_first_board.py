#!/usr/bin/env python3
"""Run the frozen Issue #230 overnight first-board ranking study."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

import polars as pl

from qsys.research import limit_up_ranking as ranking
from qsys.research.first_board import (
    FEATURE_COLUMNS,
    FirstBoardConfig,
    config_dict,
    dataset_manifest,
    materialise_dataset,
    ranking_verdict,
    rerank_legacy_predictions,
    segment_breakdown,
    walk_forward_predictions,
    selected_candidate,
)


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _source_sha(value: str | None, execution_sha: str) -> str:
    sha = value or execution_sha
    if sha != "unknown" and not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("--source-sha must be a full lowercase Git SHA")
    return sha


def _empty_directory(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"output directory must be empty: {path}")
    path.mkdir(parents=True, exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--legacy-ranking", type=Path, required=True,
                        help="#210 frozen predictions.parquet")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--bootstrap-samples", type=int, default=2_000)
    parser.add_argument("--source-sha")
    parser.add_argument("--reuse-dataset", action="store_true")
    args = parser.parse_args()

    config = FirstBoardConfig(
        threads=max(args.threads, 1),
        bootstrap_samples=max(args.bootstrap_samples, 100),
    )
    dataset_path = args.out / "dataset.parquet"
    if args.reuse_dataset:
        existing = set(args.out.iterdir()) if args.out.exists() else set()
        if existing != {dataset_path} or not dataset_path.is_file():
            raise ValueError(
                "--reuse-dataset requires dataset.parquet as the only artifact"
            )
        dataset_record = dataset_manifest(dataset_path)
    else:
        _empty_directory(args.out)
        dataset_record = materialise_dataset(args.warehouse, dataset_path, config)

    dataset = pl.read_parquet(dataset_path)
    legacy = pl.read_parquet(args.legacy_ranking)
    model_predictions, importance, models = walk_forward_predictions(dataset, config)
    predictions = pl.concat([
        ranking.baseline_predictions(dataset),
        rerank_legacy_predictions(dataset, legacy),
        model_predictions,
    ], how="vertical_relaxed").sort(["prediction_date", "model", "rank"])
    predictions.write_parquet(args.out / "predictions.parquet", compression="zstd")
    predictions.filter(pl.col("rank") <= config.overnight_top_k).write_csv(
        args.out / "top20.csv"
    )

    metrics = ranking.ranking_metrics(predictions)
    metrics.write_csv(args.out / "metrics.csv")
    ranking.monthly_precision(predictions).write_csv(
        args.out / "monthly_precision.csv"
    )
    ranking.calibration_table(predictions).write_csv(args.out / "calibration.csv")
    ranking.top20_breakdown(predictions).write_csv(args.out / "top20_breakdown.csv")
    segment_breakdown(predictions, dataset).write_csv(
        args.out / "first_board_segment_breakdown.csv"
    )
    if importance.height:
        importance.write_csv(args.out / "feature_importance_by_month.csv")
        importance.group_by("feature").agg(
            pl.col("touch_gain").mean(), pl.col("seal_gain").mean()
        ).sort("touch_gain", descending=True).write_csv(
            args.out / "feature_importance.csv"
        )

    model_dir = args.out / "models"
    model_dir.mkdir()
    import joblib
    for name, model in models.items():
        joblib.dump(model, model_dir / f"last_walk_forward_{name}.joblib")

    chosen_model = selected_candidate(metrics)
    verdict = ranking_verdict(predictions, metrics, config)
    ranking.write_json(args.out / "verdict.json", verdict)
    execution_sha = _git_sha()
    manifest = {
        "schema_version": 1,
        "research": "issue-230-first-board-ranking",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "git_sha": _source_sha(args.source_sha, execution_sha),
        "execution_checkout_sha": execution_sha,
        "warehouse": str(args.warehouse),
        "legacy_ranking": str(args.legacy_ranking),
        "legacy_ranking_sha256": ranking.file_sha256(args.legacy_ranking),
        "config": config_dict(config),
        "dataset": dataset_record,
        "feature_columns": list(FEATURE_COLUMNS),
        "labels": {
            "touch": "T+1 touched_limit_up",
            "close": "T+1 authoritative is_first_board",
        },
        "population": "eligible ordinary A shares with T-day is_limit_up=false",
        "models": {
            "legacy_210": "#210 LightGBM probabilities reranked in #230 universe",
            "momentum": "T-day return descending",
            "heuristic": "frozen #210 percentile blend",
            "logistic": "new first-board two-stage logistic",
            "lightgbm_price": "price/volume and market-regime features",
            "lightgbm_moneyflow": "base plus individual money flow",
            "lightgbm_industry": "base plus PIT SW industry diffusion",
            "lightgbm": "new first-board two-stage LightGBM",
        },
        "trading_evidence": {
            "status": "pending_issue_234",
            "promotable": False,
        },
        "selected_model": chosen_model,
        "selection_rule": "highest VALID first-board Precision@20; simplicity tie-break",
    }
    ranking.write_json(args.out / "manifest.json", manifest)
    (args.out / "README.md").write_text(
        "# Issue #230 AI 首板隔夜排名研究\n\n"
        f"隔夜排序结论：**{verdict['research_status']}**。\n\n"
        "本阶段只回答能否在 T 日收盘后发现 T+1 首板候选。历史竞价同口径与"
        "可交易收益证据由 #234 补齐；在此之前 `promote_to_shadow` 和"
        " `promote_to_trading` 恒为 false，不生成可导入策略。\n",
        encoding="utf-8",
    )
    (args.out / "RETENTION.md").write_text(
        "仅保留 manifest、verdict、排名、指标、模型及复现所需数据指纹；"
        "临时 checkout、缓存和失败运行目录在核验后逐项清理。\n",
        encoding="utf-8",
    )
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
