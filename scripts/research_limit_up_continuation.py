#!/usr/bin/env python3
"""Run the frozen Issue #229 executable continuation-strategy study."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import date, datetime
from pathlib import Path

import polars as pl

from qsys.backtest.runner import execute_backtest
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.research.limit_up_continuation import (
    MAX_DRAWDOWN,
    MAX_TOP5_PROFIT_SHARE,
    MIN_CLOSED_LOTS,
    MIN_POSITIVE_MONTH_RATIO,
    MIN_SHARPE,
    historical_verdict,
    summarize_run,
)
from qsys.strategy.deps import build_deps


EXITS = ("next_open", "11:25", "14:25")
SPLITS = ("TEST", "OBS")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _source_sha(value: str | None) -> str:
    sha = value or _git_sha()
    if sha != "unknown" and not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("--source-sha must be a full lowercase Git SHA")
    return sha


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _empty_output(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"output directory must be empty: {path}")
    path.mkdir(parents=True, exist_ok=True)


def _signal_windows(
    ranking: Path, warehouse: Path, *, runoff_sessions: int,
) -> tuple[dict[str, tuple[date, date]], dict[str, object]]:
    schema = pl.scan_parquet(ranking).collect_schema().names()
    required = {"target_session", "split", "model", "rank"}
    if missing := required - set(schema):
        raise ValueError(f"ranking artifact misses columns: {sorted(missing)}")
    targets = (
        pl.scan_parquet(ranking)
        .filter((pl.col("model") == "lightgbm") & (pl.col("rank") <= 3)
                & pl.col("split").is_in(SPLITS))
        .select("split", "target_session").unique().collect()
    )
    _, query, calendar, sessions, _, _ = build_deps(warehouse)
    bounds = query.intraday_bounds()
    if bounds is None:
        raise ValueError("warehouse has no minute data")
    minute_start, minute_end = (value.date() if hasattr(value, "date") else value
                                for value in bounds)
    usable_sessions = [day for day in sessions if minute_start <= day <= minute_end]
    if len(usable_sessions) <= runoff_sessions:
        raise ValueError("minute coverage is too short for the exit runoff")
    latest_target = usable_sessions[-runoff_sessions - 1]
    position = {day: i for i, day in enumerate(sessions)}
    windows: dict[str, tuple[date, date]] = {}
    coverage: dict[str, object] = {
        "minute_from": str(minute_start), "minute_through": str(minute_end),
        "runoff_sessions": runoff_sessions,
    }
    for split in SPLITS:
        days = sorted(targets.filter(
            (pl.col("split") == split)
            & pl.col("target_session").is_between(minute_start, latest_target)
        )["target_session"].to_list())
        if not days:
            raise ValueError(f"{split} has no signals inside minute coverage")
        end = sessions[position[days[-1]] + runoff_sessions]
        windows[split] = (days[0], end)
        coverage[split] = {
            "first_target": str(days[0]), "last_target": str(days[-1]),
            "target_sessions": len(days), "backtest_through": str(end),
        }
    return windows, coverage


def _post_trade_breakdown(ranking: Path, runs: list[dict[str, object]]) -> pl.DataFrame:
    source = (
        pl.scan_parquet(ranking)
        .filter((pl.col("model") == "lightgbm") & (pl.col("rank") <= 3))
        .select("target_session", "symbol", "board", "market_state")
        .unique().collect()
    )
    frames: list[pl.DataFrame] = []
    for run in runs:
        lots = pl.read_csv(Path(str(run["run_dir"])) / "closed_lots.csv",
                           try_parse_dates=True)
        if lots.is_empty():
            continue
        enriched = lots.with_columns(
            pl.col("entry_time").dt.date().alias("target_session")
        ).join(source, on=["target_session", "symbol"], how="left")
        for dimension in ("board", "market_state"):
            frames.append(enriched.group_by(dimension).agg(
                pl.len().alias("closed_lots"),
                pl.col("return_pct").mean().alias("mean_return_pct"),
                (pl.col("return_pct") > 0).mean().alias("win_rate"),
                pl.col("realized_pnl_gross").sum().alias("gross_pnl"),
            ).with_columns(
                pl.lit(run["split"]).alias("split"),
                pl.lit(run["exit_policy"]).alias("exit_policy"),
                pl.lit(dimension).alias("dimension"),
                pl.col(dimension).alias("value"),
            ).select("split", "exit_policy", "dimension", "value", "closed_lots",
                     "mean_return_pct", "win_rate", "gross_pnl"))
    return pl.concat(frames, how="vertical_relaxed") if frames else pl.DataFrame()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--ranking", type=Path, required=True)
    parser.add_argument("--strategy", type=Path,
                        default=Path("strategies/ai_limit_up_continuation.py"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cash", type=float, default=100_000.0)
    parser.add_argument("--bootstrap-samples", type=int, default=2_000)
    parser.add_argument("--runoff-sessions", type=int, default=10)
    parser.add_argument("--source-sha")
    args = parser.parse_args()

    _empty_output(args.out)
    windows, coverage = _signal_windows(
        args.ranking, args.warehouse, runoff_sessions=max(args.runoff_sessions, 1),
    )
    rows: list[dict[str, object]] = []
    run_records: list[dict[str, object]] = []
    for split in SPLITS:
        start, end = windows[split]
        for exit_policy in EXITS:
            name = f"{split.lower()}-{exit_policy.replace(':', '')}"
            run_dir = args.out / "runs" / name
            execute_backtest(
                args.strategy, start=start, end=end, cash=args.cash,
                warehouse=args.warehouse, out_dir=run_dir, entry_fill="continuous",
                params={
                    "ranking_path": str(args.ranking), "signal_split": split,
                    "top_k": 3, "gap_min": 0.0, "gap_max": 0.02,
                    "position_weight": 0.10, "max_positions": 3,
                    "entry_price_buffer": 0.005, "exit_policy": exit_policy,
                },
            )
            row = summarize_run(
                run_dir, split=split, exit_policy=exit_policy,
                bootstrap_samples=max(args.bootstrap_samples, 100),
            )
            rows.append(row)
            run_records.append({**row, "run_dir": str(run_dir)})

    pl.DataFrame(rows).sort(["split", "exit_policy"]).write_csv(args.out / "summary.csv")
    breakdown = _post_trade_breakdown(args.ranking, run_records)
    if breakdown.height:
        breakdown.write_csv(args.out / "board_regime_breakdown.csv")
    verdict = historical_verdict(rows)
    _write_json(args.out / "verdict.json", verdict)
    manifest = {
        "schema_version": 1,
        "research": "issue-229-ai-limit-up-continuation",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "git_sha": _source_sha(args.source_sha),
        "warehouse": str(args.warehouse),
        "ranking": str(args.ranking),
        "ranking_sha256": _sha256(args.ranking),
        "strategy": str(args.strategy),
        "strategy_sha256": _sha256(args.strategy),
        "coverage": coverage,
        "fixed_parameters": {
            "model": "lightgbm", "top_k": 3, "auction_gap": "[0%,2%)",
            "entry": "09:31 continuous", "entry_price_buffer": 0.005,
            "position_weight": 0.10, "max_positions": 3,
            "exit_policies": list(EXITS), "primary_exit": "next_open",
        },
        "historical_gates": {
            "min_sharpe": MIN_SHARPE, "max_drawdown": MAX_DRAWDOWN,
            "min_closed_lots": MIN_CLOSED_LOTS,
            "min_positive_month_ratio": MIN_POSITIVE_MONTH_RATIO,
            "max_top5_positive_profit_share": MAX_TOP5_PROFIT_SHARE,
            "daily_bootstrap_ci_low": ">0", "continuous_fallbacks": 0,
        },
    }
    _write_json(args.out / "manifest.json", manifest)
    (args.out / "README.md").write_text(
        "# Issue #229 AI 连板强势延续策略研究\n\n"
        f"历史证据结论：**{'PASS' if verdict['historical_support'] else 'FAIL'}**。\n\n"
        "TEST/OBS 曾参与假设探索，因此 PASS 也只允许进入冻结规则后的只读前瞻观察，"
        "不授权模拟或实盘。主裁决固定为次日开盘退出；11:25/14:25 仅作敏感性分析。\n\n"
        "每个 `runs/` 子目录是标准 qsys 回测产物；汇总见 `summary.csv`，"
        "板块/市场状态拆分见 `board_regime_breakdown.csv`，逐项门禁见 `verdict.json`。\n",
        encoding="utf-8",
    )
    (args.out / "RETENTION.md").write_text(
        "保留 manifest、verdict、summary、breakdown 与六个标准回测目录。"
        "运行产生的上传副本、临时 checkout、日志和缓存应在 SHA 核对后清理。\n",
        encoding="utf-8",
    )
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
