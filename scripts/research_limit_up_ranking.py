#!/usr/bin/env python3
"""Run the fixed Issue #210 T+1 limit-up ranking research protocol."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

import polars as pl

from qsys.research.limit_up_ranking import (
    FEATURE_COLUMNS,
    ResearchConfig,
    baseline_predictions,
    calibration_table,
    config_dict,
    dataset_manifest,
    materialise_dataset,
    monthly_precision,
    promotion_verdict,
    ranking_metrics,
    top20_breakdown,
    walk_forward_predictions,
    write_json,
)


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _source_sha(value: str | None, execution_sha: str) -> str:
    if value is None:
        return execution_sha
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("--source-sha must be a full lowercase Git SHA")
    return value


def _empty_directory(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"output directory must be empty: {path}")
    path.mkdir(parents=True, exist_ok=True)


def _write_readme(path: Path, manifest: dict, verdict: dict) -> None:
    status = "PASS：可另开只读影子榜单 Issue" if verdict["promote_to_shadow"] else (
        "FAIL：没有通过严格样本外门禁，不进入系统榜单"
    )
    path.write_text(
        "# Issue #210 AI 涨停概率榜单研究\n\n"
        f"结论：**{status}**。\n\n"
        "本目录是离线研究证据，不是交易信号；没有修改策略参数、生产配置或订单服务。\n\n"
        "## 固定口径\n\n"
        "- 预测日 T 使用 T 日收盘后可见信息，标签是下一交易日 T+1。\n"
        "- 主指标是收盘封板 Precision@20；同时研究盘中触板。\n"
        "- 股票池排除 ST、北交所、退市整理、停牌和不足 60 根历史的股票。\n"
        "- TEST/OBS 仅使用冻结模型配置和严格向前训练。\n\n"
        "## 复现\n\n"
        f"- Git SHA：`{manifest['git_sha']}`\n"
        f"- 数据集 SHA-256：`{manifest['dataset']['sha256']}`\n"
        "- 运行配置见 `manifest.json`，晋级逐项判断见 `verdict.json`。\n"
        "- 完整日榜见 `predictions.parquet`，每日前 20 见 `top20.csv`。\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--bootstrap-samples", type=int, default=2_000)
    parser.add_argument(
        "--source-sha",
        help="full Git SHA of the uploaded research source (defaults to checkout HEAD)",
    )
    parser.add_argument(
        "--reuse-dataset", action="store_true",
        help="reuse dataset.parquet when it is the only existing output artifact",
    )
    args = parser.parse_args()

    config = ResearchConfig(
        threads=max(args.threads, 1), bootstrap_samples=max(args.bootstrap_samples, 100)
    )
    dataset_path = args.out / "dataset.parquet"
    if args.reuse_dataset:
        existing = set(args.out.iterdir()) if args.out.exists() else set()
        if existing != {dataset_path} or not dataset_path.is_file():
            raise ValueError(
                "--reuse-dataset requires dataset.parquet to be the only output artifact"
            )
        dataset_record = dataset_manifest(dataset_path)
    else:
        _empty_directory(args.out)
        dataset_record = materialise_dataset(args.warehouse, dataset_path, config)
    dataset = pl.read_parquet(dataset_path)

    model_predictions, importance, models = walk_forward_predictions(dataset, config)
    predictions = pl.concat(
        [baseline_predictions(dataset), model_predictions], how="vertical_relaxed"
    ).sort(["prediction_date", "model", "rank"])
    predictions.write_parquet(args.out / "predictions.parquet", compression="zstd")
    predictions.filter(pl.col("rank") <= 20).write_csv(args.out / "top20.csv")

    metrics = ranking_metrics(predictions)
    metrics.write_csv(args.out / "metrics.csv")
    monthly_precision(predictions).write_csv(args.out / "monthly_precision.csv")
    calibration_table(predictions).write_csv(args.out / "calibration.csv")
    top20_breakdown(predictions).write_csv(args.out / "top20_breakdown.csv")
    if importance.height:
        importance.write_csv(args.out / "feature_importance_by_month.csv")
        importance.group_by("feature").agg(
            pl.col("touch_gain").mean(), pl.col("seal_gain").mean()
        ).sort("touch_gain", descending=True).write_csv(
            args.out / "feature_importance.csv"
        )

    model_dir = args.out / "models"
    model_dir.mkdir()
    try:
        import joblib
    except ImportError as exc:  # pragma: no cover - CLI error path
        raise RuntimeError("ml-research dependency group is required") from exc
    for name, model in models.items():
        joblib.dump(model, model_dir / f"last_walk_forward_{name}.joblib")

    verdict = promotion_verdict(predictions, metrics, config)
    write_json(args.out / "verdict.json", verdict)
    execution_sha = _git_sha()
    manifest = {
        "schema_version": 1,
        "research": "issue-210-limit-up-ranking",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "git_sha": _source_sha(args.source_sha, execution_sha),
        "execution_checkout_sha": execution_sha,
        "warehouse": str(args.warehouse),
        "config": config_dict(config),
        "dataset": dataset_record,
        "feature_columns": list(FEATURE_COLUMNS),
        "models": {
            "momentum": "T-day return descending",
            "heuristic": "fixed 30/25/20/15/10 percentile blend",
            "logistic": "two-stage SGD logistic + Platt calibration",
            "lightgbm": "fixed two-stage LightGBM + Platt calibration",
        },
    }
    write_json(args.out / "manifest.json", manifest)
    _write_readme(args.out / "README.md", manifest, verdict)
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    print(f"research artifacts written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
