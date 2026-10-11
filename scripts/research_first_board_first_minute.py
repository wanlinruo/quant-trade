#!/usr/bin/env python3
"""Run Issue #230's frozen 09:31 first-minute gate and trading study."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import date, datetime, time
from pathlib import Path

import joblib
import polars as pl

from qsys.backtest.entry_fill import EntryFill, EntryFillMode
from qsys.backtest.runner import execute_backtest, visible_entry_minute_loader
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.research.first_board_intraday import (
    ENTRY_BAR_AT,
    GATE_FEATURES,
    MODEL,
    build_feature_table,
    fit_frozen_gate,
    freeze_selections,
    load_candidates,
)
from qsys.research.limit_up_continuation import historical_verdict, summarize_run
from qsys.strategy.deps import build_deps


SPLITS = ("TEST", "OBS")
PRIMARY_POLICY = "gate_threshold"
PRIMARY_COST = 0.01
STRESS_COSTS = (0.005, 0.01, 0.015)
STRATEGY = Path("scripts/issue_230_first_minute_strategy.py")


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


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


def _empty_output(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"output directory must be empty: {path}")
    path.mkdir(parents=True, exist_ok=True)


def _windows(
    warehouse: Path, candidates: pl.DataFrame, selections: pl.DataFrame,
) -> tuple[dict, dict]:
    _, query, _, sessions, _, _ = build_deps(warehouse)
    bounds = query.intraday_bounds()
    if bounds is None:
        raise ValueError("canonical minute data is missing")
    minute_start, minute_end = (
        value.date() if hasattr(value, "date") else value for value in bounds
    )
    position = {day: i for i, day in enumerate(sessions)}
    windows: dict[str, tuple[date, date]] = {}
    coverage: dict[str, object] = {
        "minute_from": str(minute_start), "minute_through": str(minute_end),
    }
    for split in SPLITS:
        days = sorted(candidates.filter(
            pl.col("split") == split
        )["target_session"].unique().to_list())
        if not days:
            raise ValueError(f"{split} has no frozen first-minute selections")
        first, last = days[0], days[-1]
        if last not in position or position[last] + 1 >= len(sessions):
            raise ValueError(f"{split} lacks the next session required for exits")
        end = sessions[position[last] + 1]
        if not (minute_start <= first and end <= minute_end):
            raise ValueError(f"{split} exceeds canonical minute coverage")
        windows[split] = (first, end)
        coverage[split] = {
            "first_target": str(first), "last_target": str(last),
            "target_sessions": len(days),
            "selected_sessions": selections.filter(
                (pl.col("policy") == PRIMARY_POLICY)
                & (pl.col("split") == split)
            )["target_session"].n_unique(),
            "backtest_through": str(end),
        }
    return windows, coverage


def _matrix() -> list[tuple[str, str, float]]:
    rows = [
        (PRIMARY_POLICY, "next_open", cost) for cost in STRESS_COSTS
    ]
    rows += [(PRIMARY_POLICY, exit_policy, PRIMARY_COST)
             for exit_policy in ("11:25", "14:25")]
    rows += [(policy, "next_open", PRIMARY_COST)
             for policy in ("rank_top1", "rank_top3", "gate_top3")]
    return rows


def _fill_stat(run_dir: Path, name: str) -> int:
    config = json.loads((run_dir / "config.json").read_text())
    raw = str(config.get("entry_fill_stats", ""))
    values = {
        key: int(value) for item in raw.split(",") if "=" in item
        for key, value in [item.split("=", 1)]
    }
    return values.get(name, 0)


def _verdict(rows: list[dict[str, object]]) -> dict[str, object]:
    primary = [
        row for row in rows
        if row["policy"] == PRIMARY_POLICY
        and row["exit_policy"] == "next_open"
        and abs(float(row["entry_cost_stress"]) - PRIMARY_COST) < 1e-12
    ]
    base = historical_verdict(primary)
    checks = dict(base["checks"])
    for split in SPLITS:
        stressed = next((row for row in rows
                         if row["split"] == split
                         and row["policy"] == PRIMARY_POLICY
                         and row["exit_policy"] == "next_open"
                         and abs(float(row["entry_cost_stress"]) - 0.015) < 1e-12), None)
        checks[f"{split.lower()}_return_positive_at_1_5pct_stress"] = bool(
            stressed and float(stressed["total_return"]) > 0
        )
        checks[f"{split.lower()}_no_missing_entry_minute"] = all(
            int(row["missing_entry_minutes"]) == 0 for row in rows
            if row["split"] == split
        )
    passed = all(checks.values())
    return {
        **base,
        "schema_version": 1,
        "research_path": "09:31_first_minute_gate",
        "primary_entry_cost_stress": PRIMARY_COST,
        "checks": checks,
        "historical_support": passed,
        "promote_to_trading": False,
        "prospective_shadow_required": passed,
        "research_status": (
            "TRADING_PASS_PROSPECTIVE_SHADOW_REQUIRED" if passed
            else "TRADING_FAIL"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--ranking", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cash", type=float, default=100_000.0)
    parser.add_argument("--bootstrap-samples", type=int, default=2_000)
    parser.add_argument("--source-sha")
    args = parser.parse_args()

    _empty_output(args.out)
    query = WarehouseQuery(Warehouse(args.warehouse))
    candidates = load_candidates(args.ranking)
    features = build_feature_table(query, candidates)
    model, scored, gate = fit_frozen_gate(features)
    selections = freeze_selections(scored, float(gate["chosen_threshold"]))

    features.write_parquet(args.out / "first_minute_features.parquet")
    selections.write_parquet(args.out / "frozen_selections.parquet")
    pl.DataFrame(gate["threshold_candidates"]).write_csv(
        args.out / "valid_thresholds.csv"
    )
    _write_json(args.out / "gate_freeze.json", gate)
    joblib.dump(model, args.out / "gate_model.joblib")
    classifier = model.named_steps["logisticregression"]
    pl.DataFrame({
        "feature": GATE_FEATURES,
        "standardized_coefficient": classifier.coef_[0],
    }).write_csv(args.out / "gate_coefficients.csv")

    coverage_rows = (
        features.group_by("split", "invalid_reason")
        .agg(pl.len().alias("candidate_days"), pl.col("target_session").n_unique().alias("sessions"))
        .sort("split", "invalid_reason")
    )
    coverage_rows.write_csv(args.out / "minute_coverage.csv")
    selection_summary = selections.group_by("policy", "split").agg(
        pl.len().alias("selected"),
        pl.col("target_session").n_unique().alias("sessions"),
        pl.col("actual_close").sum().alias("first_boards"),
        pl.col("actual_close").mean().alias("precision"),
    ).sort("policy", "split")
    selection_summary.write_csv(args.out / "selection_summary.csv")

    windows, coverage = _windows(args.warehouse, candidates, selections)
    rows: list[dict[str, object]] = []
    for split in SPLITS:
        start, end = windows[split]
        for policy, exit_policy, cost in _matrix():
            name = (
                f"{split.lower()}-{policy}-{exit_policy.replace(':', '')}"
                f"-cost{int(round(cost * 10_000)):04d}bp"
            )
            run_dir = args.out / "runs" / name
            execute_backtest(
                STRATEGY, start=start, end=end, cash=args.cash,
                warehouse=args.warehouse, out_dir=run_dir,
                params={
                    "selection_path": str(args.out / "frozen_selections.parquet"),
                    "selection_policy": policy, "signal_split": split,
                    "position_weight": 0.10, "entry_price_buffer": 0.03,
                    "exit_policy": exit_policy,
                },
                entry_rule=EntryFill(
                    EntryFillMode.CONTINUOUS, minute_at=ENTRY_BAR_AT,
                    price_stress=cost, fallback_on_missing=False,
                ),
                entry_minutes=visible_entry_minute_loader(query),
            )
            row = summarize_run(
                run_dir, split=split, exit_policy=exit_policy,
                bootstrap_samples=max(args.bootstrap_samples, 100),
            )
            row.update({
                "policy": policy, "entry_cost_stress": cost,
                "missing_entry_minutes": _fill_stat(run_dir, "missing_minute"),
                "run_dir": str(run_dir),
            })
            rows.append(row)

    pl.DataFrame(rows).sort(
        "split", "policy", "exit_policy", "entry_cost_stress",
    ).write_csv(args.out / "trading_summary.csv")
    verdict = _verdict(rows)
    _write_json(args.out / "verdict.json", verdict)

    source_sha = _source_sha(args.source_sha)
    manifest = {
        "schema_version": 1,
        "research": "issue-230-first-board-09:31-first-minute-gate",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "git_sha": source_sha,
        "warehouse": str(args.warehouse),
        "ranking": str(args.ranking),
        "ranking_sha256": _sha256(args.ranking),
        "selected_overnight_model": MODEL,
        "strategy_adapter": str(STRATEGY),
        "strategy_adapter_sha256": _sha256(STRATEGY),
        "coverage": coverage,
        "gate_freeze": gate,
        "fixed_contract": {
            "overnight_top_k": 20, "max_positions": 3,
            "decision": "09:31 after the first continuous minute is visible",
            "entry_bar": "strictly subsequent bar visible at 09:32",
            "minute_identity": "visible_at, not provider ts",
            "entry_price_buffer": 0.03,
            "primary_entry_cost_stress": PRIMARY_COST,
            "stress_costs": list(STRESS_COSTS),
            "position_weight": 0.10,
            "primary_exit": "next_open",
            "sensitivity_exits": ["11:25", "14:25"],
            "missing_minute": "reject; no fallback",
        },
        "artifacts": {
            name: _sha256(args.out / name) for name in (
                "first_minute_features.parquet", "frozen_selections.parquet",
                "gate_model.joblib", "gate_freeze.json", "trading_summary.csv",
                "verdict.json",
            )
        },
    }
    _write_json(args.out / "manifest.json", manifest)
    (args.out / "README.md").write_text(
        "# Issue #230：09:31 首分钟过滤交易研究\n\n"
        f"历史裁决：**{verdict['research_status']}**。\n\n"
        "本路径使用冻结隔夜 Top20，在 09:31 只读取此前可见的首分钟，"
        "随后用 09:32 可见的下一根分钟线严格判定成交。它不等价于 09:25 竞价路径。"
        "即使历史门禁通过，也只允许另行开展前瞻只读影子观察，不授权交易，"
        "本次不生成 `strategies/` 文件。\n",
        encoding="utf-8",
    )
    (args.out / "RETENTION.md").write_text(
        "保留 manifest、verdict、冻结门控模型与选择、分钟特征、汇总和标准回测目录；"
        "临时 checkout、缓存与日志在 SHA 核对后清理。\n",
        encoding="utf-8",
    )
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
