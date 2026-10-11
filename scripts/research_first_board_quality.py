#!/usr/bin/env python3
"""Build and freeze the Issue #230 outcome-aware 09:30 quality gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

import joblib
import polars as pl

from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.research.first_board_outcomes import build_counterfactual_outcomes
from qsys.research.first_board_quality import (
    EXIT_POLICIES,
    MODEL_FEATURES,
    PRIMARY_COST_STRESS,
    choose_thresholds,
    fit_frozen_bundle,
    quality_features,
    score_bundle,
    walk_forward_predictions,
)
from qsys.strategy.deps import build_deps


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
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


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _empty(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"output directory must be empty: {path}")
    path.mkdir(parents=True, exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--opening-study", type=Path, required=True)
    parser.add_argument("--ranking-run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-sha")
    args = parser.parse_args()
    _empty(args.out)

    scored = pl.read_parquet(args.opening_study / "scored_candidates.parquet")
    keys = scored.select("prediction_date", "target_session", "symbol")
    overnight = (
        pl.read_parquet(args.ranking_run / "predictions.parquet")
        .filter(pl.col("model") == "lightgbm_price")
        .join(keys, on=["prediction_date", "target_session", "symbol"], how="semi")
    )
    dataset = (
        pl.read_parquet(args.ranking_run / "dataset.parquet")
        .join(keys, on=["prediction_date", "target_session", "symbol"], how="semi")
    )
    features = quality_features(scored, overnight, dataset)
    _, _, _, all_sessions, _, _ = build_deps(args.warehouse)
    first = features["target_session"].min()
    session_slice = [day for day in all_sessions if day >= first]
    if not session_slice or session_slice[-1] <= features["target_session"].max():
        raise ValueError("warehouse needs at least one session after the final candidate day")
    query = WarehouseQuery(Warehouse(args.warehouse))
    labelled = build_counterfactual_outcomes(query, features, session_slice)
    labelled.write_parquet(args.out / "candidate_outcomes.parquet", compression="zstd")

    models = args.out / "models"
    models.mkdir()
    oof_frames = []
    decision_frames = []
    summaries = {}
    threshold_payload = {}
    for policy in EXIT_POLICIES:
        oof = walk_forward_predictions(labelled, policy)
        thresholds, summary = choose_thresholds(oof, policy)
        bundle = fit_frozen_bundle(labelled, policy, thresholds)
        joblib.dump(bundle, models / f"{policy}.joblib")
        frozen = score_bundle(labelled, bundle).with_columns(
            pl.lit("DEVELOPMENT_IN_SAMPLE").alias("decision_scope")
        )
        oof_frames.append(oof)
        decision_frames.append(frozen)
        summaries[policy] = summary
        threshold_payload[policy] = thresholds.as_dict()
    pl.concat(oof_frames, how="vertical_relaxed").write_parquet(
        args.out / "oof_predictions.parquet", compression="zstd",
    )
    pl.concat(decision_frames, how="vertical_relaxed").write_parquet(
        args.out / "development_decisions.parquet", compression="zstd",
    )
    _write_json(args.out / "thresholds.json", threshold_payload)
    _write_json(args.out / "development_summary.json", summaries)

    verdict = {
        "schema_version": 1,
        "research_status": "QUALITY_HYPOTHESIS_READY_FORWARD_PENDING",
        "historical_support": False,
        "promote_to_shadow": False,
        "promote_to_trading": False,
        "forward_gate": {
            "minimum_sessions": 120, "minimum_closed_fills_per_policy": 50,
            "primary_entry_cost_stress": PRIMARY_COST_STRESS,
            "total_return_positive": True,
            "multiplicity_adjusted_daily_ci_low_positive": True,
            "minimum_sharpe": 0.5, "maximum_drawdown": 0.15,
            "minimum_positive_month_ratio": 0.60,
            "maximum_top5_profit_share": 0.50,
            "missing_fallbacks": 0, "open_positions_at_end": 0,
        },
    }
    _write_json(args.out / "verdict.json", verdict)
    source_sha = _source_sha(args.source_sha)
    artifacts = (
        "candidate_outcomes.parquet", "oof_predictions.parquet",
        "development_decisions.parquet", "thresholds.json",
        "development_summary.json", "verdict.json",
    )
    manifest = {
        "schema_version": 1,
        "research": "issue-230-outcome-aware-first-board-quality-gate",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "git_sha": source_sha,
        "warehouse": str(args.warehouse),
        "opening_study": str(args.opening_study),
        "opening_manifest_sha256": _sha256(args.opening_study / "manifest.json"),
        "ranking_run": str(args.ranking_run),
        "ranking_manifest_sha256": _sha256(args.ranking_run / "manifest.json"),
        "features": list(MODEL_FEATURES),
        "population": "frozen lightgbm_price Top20; at most one pick per policy/day",
        "entry": {
            "decision": "09:30 completed auction match",
            "execution": "09:31 visible continuous minute",
            "price_buffer": 0.03,
            "cost_stress": PRIMARY_COST_STRESS,
            "sealed_at_decision": "reject within half-cent of limit-up",
            "missing": "fail closed",
        },
        "development_data_is_touched": True,
        "frozen_through_session": str(features["target_session"].max()),
        "forward_contract": {
            "starts_after_manifest_freeze": True,
            "weights_and_thresholds_frozen": True,
            "rule_change_resets_observation": True,
            "milestones_are_non_adjudicating": [30, 60],
            "final_sessions": 120, "final_fills_per_policy": 50,
            "position_weight": 0.10,
            "two_policy_bonferroni_lower_quantile": 0.0125,
            "settled_record": (
                "one row per policy/session, including abstentions; required columns "
                "target_session, exit_policy, entry_filled, closed, net_return_pct, "
                "missing_fallbacks, open_positions_at_end"
            ),
        },
        "model_sha256": {
            policy: _sha256(models / f"{policy}.joblib") for policy in EXIT_POLICIES
        },
        "artifacts": {name: _sha256(args.out / name) for name in artifacts},
    }
    _write_json(args.out / "manifest.json", manifest)
    (args.out / "README.md").write_text(
        "# Issue #230 首板入选质量门禁\n\n"
        "状态：`QUALITY_HYPOTHESIS_READY_FORWARD_PENDING`。开发数据已被研究触碰，"
        "因此结果只用于冻结假设，不能作为历史支持，也不授权影子或实盘。\n\n"
        "固定输入为 `lightgbm_price` 隔夜 Top20；09:30 完成竞价后，两套独立模型"
        "最多各选一只，也可空仓；09:31 连续竞价按 0.5% 入场压力和 3% 追价保护"
        "仿真。退出分别为次日开盘和 11:25/14:25 两阶段规则。开发选择依据是"
        "扩展窗口 OOF，`development_decisions.parquet` 仅为全样本拟合诊断。\n\n"
        "向前观察必须从 manifest 冻结日之后开始，模型、阈值、特征和规则均不得"
        "改动。30/60 日只汇报进度；两套退出均达到 120 个交易日且至少 50 笔"
        "闭合成交后，才可运行 `adjudicate_first_board_quality_forward.py`。通过也"
        "只形成向前研究支持，不自动产生订单或晋级交易。\n",
        encoding="utf-8",
    )
    (args.out / "RETENTION.md").write_text(
        "永久保留 manifest、verdict、阈值、模型、开发摘要、OOF 预测和候选结果。"
        "向前每日特征、决策、结算记录及每次不可变裁决也必须保留。临时 checkout、"
        "缓存和重复日志可在哈希复核后清理。\n",
        encoding="utf-8",
    )
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
