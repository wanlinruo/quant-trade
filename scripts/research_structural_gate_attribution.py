#!/usr/bin/env python3
"""结构策略候选漏斗与 S6/S7 组件归因（只读）。"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

import polars as pl

from qsys.research.structural_gate_attribution import (
    enrich_traces,
    single_relaxations,
    write_report,
)


def _decision_frame(rows: list[dict]) -> pl.DataFrame:
    selected = []
    for row in rows:
        if not row.get("routed_tag") or row.get("gate_allow") is None:
            continue
        flat = {key: value for key, value in row.items() if key != "variants"}
        selected.append({
            **flat,
            "strategy_tag": row["routed_tag"],
            "s7_state": row.get("gate_state"),
            "reason": row.get("gate_reason"),
            "real_allow": row.get("gate_allow"),
        })
    if not selected:
        return pl.DataFrame()
    return pl.DataFrame(selected, infer_schema_length=None, strict=False)


def _relaxation_frame(rows: list[dict]) -> pl.DataFrame:
    frame = single_relaxations(rows)
    if frame.height == 0:
        return frame
    return frame.with_columns(
        pl.lit("RELAXED_PRE_GATE").alias("s7_state"),
        pl.col("relaxed_condition").alias("reason"),
        pl.lit(False).alias("real_allow"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--candidate-funnel", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--horizon", type=int, default=20)
    parser.add_argument("--market-data-facade", choices=("legacy", "canonical"))
    args = parser.parse_args()
    if args.market_data_facade:
        os.environ["QSYS_MARKET_DATA_FACADE"] = args.market_data_facade

    raw = pl.read_csv(args.candidate_funnel, try_parse_dates=True)
    rows = enrich_traces(raw)
    if rows and min(row["date"] for row in rows) < date(2023, 1, 3):
        raise ValueError("candidate funnel starts before the trustworthy 2023-01-03 boundary")

    # 沿用现有退出检查点重放，不另写一套持仓逻辑。
    from scripts.gate_study import run as counterfactual_run

    decisions = _decision_frame(rows)
    if decisions.height == 0:
        raise ValueError("candidate funnel contains no routed gate decisions")
    gate_result = counterfactual_run(args.warehouse, decisions, args.horizon)
    outcomes = gate_result.pop("trades")

    relaxation_frame = _relaxation_frame(rows)
    relaxation_outcomes: list[dict] = []
    if relaxation_frame.height:
        relaxed_result = counterfactual_run(
            args.warehouse, relaxation_frame, args.horizon
        )
        relaxation_outcomes = relaxed_result.pop("trades")

    args.out.mkdir(parents=True, exist_ok=True)
    raw.write_parquet(args.out / "candidate_funnel.parquet", compression="zstd")
    report = write_report(
        args.out,
        trace_path=args.candidate_funnel,
        rows=rows,
        outcomes=outcomes,
        relaxation_outcomes=relaxation_outcomes,
    )
    # 汇总统计只从按 strict/extended 分层的 CSV 读取；这里不保留 run() 的
    # 跨层 allowed/rejected 聚合，防止报告消费者误把两层样本混算。
    report["gate_counterfactual"] = {"sample": gate_result["sample"]}
    report["single_relaxation_counterfactual"] = (
        {"sample": relaxed_result["sample"]}
        if relaxation_frame.height else {"sample": {"decisions": 0}}
    )
    (args.out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
