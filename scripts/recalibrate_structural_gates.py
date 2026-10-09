#!/usr/bin/env python3
"""Recalibrate the four D-4 market thresholds without changing production.

The input decisions must come from one gated StructuralStrategy backtest covering
the fixed 2025 TRAIN and 2026 VALID/OBS windows.  Thresholds are fitted only on
TRAIN candidates.  A decision on trading day T is joined to market regime T-1,
which mirrors ``StructuralSelection.select`` and prevents same-day leakage.

This script writes research evidence only.  It never imports or edits
``params.py`` and never updates a runtime configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import polars as pl

from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


TRAIN = (date(2025, 1, 1), date(2025, 12, 31))
VALID = (date(2026, 1, 1), date(2026, 6, 30))
OBS = (date(2026, 7, 1), date(2026, 9, 30))

OLD_THRESHOLDS = {
    "low_trend_weak": -0.009053065956339234,
    "low_heat_low": 0.0069591652776029595,
    "high_main_breadth_high": 0.0042554739215828,
    "high_chinext_advance_not_low": 0.5104943476458254,
}

# These two gates are deliberately outside D-4 and remain frozen.
LOW_STOCK_REASON = "STOCK_OVERHEAT_RET10_HIGH_NEAR_60D_HIGH"
HIGH_CHINEXT_TOUCHED_NOT_LOW = 103.33333333333333


@dataclass(frozen=True)
class QuantileRule:
    key: str
    tag: str
    column: str
    quantile: float


RULES = (
    QuantileRule("low_trend_weak", "LOW_MAIN", "index_ma20_slope_5d", 1 / 3),
    QuantileRule(
        "low_heat_low", "LOW_MAIN", "prior_limitup_nextday_return_mean", 1 / 3
    ),
    QuantileRule(
        "high_main_breadth_high", "HIGH_MAIN", "market_median_return_1d", 2 / 3
    ),
    QuantileRule(
        "high_chinext_advance_not_low",
        "HIGH_CHINEXT",
        "market_advance_ratio",
        1 / 3,
    ),
)

FACTOR_COLUMNS = sorted({rule.column for rule in RULES} | {"touched_limit_up_count"})
REQUIRED_DECISION_COLUMNS = {
    "date", "symbol", "strategy_tag", "reason", "real_allow",
}


def _in_strategy_scope(symbol: str) -> bool:
    code, dot, exchange = str(symbol).partition(".")
    return bool(
        dot and exchange in {"SH", "SZ"}
        and not code.startswith(("4", "8", "68"))
    )


def partition_strategy_scope(frame: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Isolate candidates outside the intended main/GEM trading universe.

    This is deliberately explicit because legacy 4/8 prefix filters do not
    catch post-2023 ``920xxx.BJ`` codes.  Exclusions are written as evidence,
    not silently discarded.
    """
    if "symbol" not in frame.columns:
        raise ValueError("decisions missing column: symbol")
    marked = frame.with_columns(
        pl.col("symbol").map_elements(_in_strategy_scope, return_dtype=pl.Boolean)
        .alias("in_strategy_scope")
    )
    return (
        marked.filter(pl.col("in_strategy_scope")).drop("in_strategy_scope"),
        marked.filter(~pl.col("in_strategy_scope")).drop("in_strategy_scope"),
    )


def _split(day: date) -> str | None:
    for label, (start, end) in (("TRAIN", TRAIN), ("VALID", VALID), ("OBS", OBS)):
        if start <= day <= end:
            return label
    return None


def _normalise_decisions(frame: pl.DataFrame) -> pl.DataFrame:
    missing = REQUIRED_DECISION_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"decisions missing columns: {sorted(missing)}")
    dtype = frame.schema["date"]
    if dtype == pl.Utf8:
        frame = frame.with_columns(pl.col("date").str.to_date(strict=True))
    elif dtype == pl.Datetime:
        frame = frame.with_columns(pl.col("date").cast(pl.Date))
    elif dtype != pl.Date:
        raise ValueError(f"unsupported decisions date dtype: {dtype}")
    frame = frame.with_columns(
        pl.col("date").map_elements(_split, return_dtype=pl.Utf8).alias("split")
    )
    outside = frame.filter(pl.col("split").is_null())
    if outside.height:
        lo, hi = outside["date"].min(), outside["date"].max()
        raise ValueError(f"decisions outside fixed TRAIN/VALID/OBS windows: {lo}..{hi}")
    if frame.filter(pl.col("reason").str.contains("GATE_DISABLED")).height:
        raise ValueError("decisions must come from a gated backtest")
    return frame


def build_observations(decisions: pl.DataFrame, regime: pl.DataFrame) -> pl.DataFrame:
    """Join each candidate on T to the immediately preceding regime session."""
    decisions = _normalise_decisions(decisions)
    needed = {"dt", *FACTOR_COLUMNS}
    missing = needed - set(regime.columns)
    if missing:
        raise ValueError(f"market_regime missing columns: {sorted(missing)}")
    regime = regime.select("dt", *FACTOR_COLUMNS).sort("dt")
    if regime["dt"].n_unique() != regime.height:
        raise ValueError("market_regime has duplicate dates")
    sessions = regime["dt"].to_list()
    previous: list[date] = []
    for day in decisions["date"].to_list():
        index = bisect_left(sessions, day)
        if index == 0:
            raise ValueError(f"no prior market_regime session for decision {day}")
        previous.append(sessions[index - 1])
    joined = (
        decisions.with_columns(pl.Series("regime_dt", previous, dtype=pl.Date))
        .join(regime, left_on="regime_dt", right_on="dt", how="left")
        .sort("date", "symbol")
    )
    if joined.filter(pl.any_horizontal(pl.col(FACTOR_COLUMNS).is_null())).height:
        bad = joined.filter(pl.any_horizontal(pl.col(FACTOR_COLUMNS).is_null()))
        raise ValueError(
            f"candidate regimes contain null factors: {bad.height} rows, "
            f"first={bad.row(0, named=True)['date']}"
        )
    if joined.filter(pl.col("regime_dt") >= pl.col("date")).height:
        raise ValueError("future-data guard failed: regime date must precede decision date")
    return joined


def fit_thresholds(observations: pl.DataFrame) -> tuple[dict[str, float], list[dict]]:
    """Fit candidate-weighted linear quantiles on TRAIN only."""
    thresholds: dict[str, float] = {}
    evidence: list[dict] = []
    train = observations.filter(pl.col("split") == "TRAIN")
    for rule in RULES:
        values = train.filter(pl.col("strategy_tag") == rule.tag)[rule.column].drop_nulls()
        if not len(values):
            raise ValueError(f"no TRAIN candidates for {rule.tag}/{rule.column}")
        value = values.quantile(rule.quantile, interpolation="linear")
        if value is None:
            raise ValueError(f"cannot fit {rule.key}")
        thresholds[rule.key] = float(value)
        evidence.append({
            "key": rule.key,
            "tag": rule.tag,
            "factor": rule.column,
            "quantile": rule.quantile,
            "interpolation": "linear",
            "train_candidates": len(values),
            "old": OLD_THRESHOLDS[rule.key],
            "candidate": float(value),
            "delta": float(value) - OLD_THRESHOLDS[rule.key],
        })
    return thresholds, evidence


def gate_allow(row: dict, thresholds: dict[str, float]) -> bool:
    tag = row["strategy_tag"]
    if tag == "LOW_MAIN":
        market_bad = (
            row["index_ma20_slope_5d"] > thresholds["low_trend_weak"]
            and row["prior_limitup_nextday_return_mean"] > thresholds["low_heat_low"]
        )
        stock_bad = LOW_STOCK_REASON in str(row["reason"])
        return not (market_bad or stock_bad)
    if tag == "HIGH_MAIN":
        return row["market_median_return_1d"] > thresholds["high_main_breadth_high"]
    if tag == "HIGH_CHINEXT":
        return (
            row["touched_limit_up_count"] > HIGH_CHINEXT_TOUCHED_NOT_LOW
            and row["market_advance_ratio"]
            > thresholds["high_chinext_advance_not_low"]
        )
    return True


def compare_gates(
    observations: pl.DataFrame, candidate: dict[str, float]
) -> tuple[pl.DataFrame, pl.DataFrame]:
    rows: list[dict] = []
    mismatches: list[dict] = []
    for row in observations.iter_rows(named=True):
        old_allow = gate_allow(row, OLD_THRESHOLDS)
        new_allow = gate_allow(row, candidate)
        if old_allow != bool(row["real_allow"]):
            mismatches.append(row)
        rows.append({**row, "old_allow_recomputed": old_allow,
                     "candidate_allow": new_allow,
                     "transition": f"{int(old_allow)}->{int(new_allow)}"})
    if mismatches:
        first = mismatches[0]
        raise ValueError(
            "old-threshold replay does not match input decisions: "
            f"{len(mismatches)} rows, first={first['date']}/{first['symbol']}"
        )
    detail = pl.DataFrame(rows)
    summary = (
        detail.group_by("split", "strategy_tag")
        .agg(
            pl.len().alias("candidates"),
            pl.col("old_allow_recomputed").sum().alias("old_allowed"),
            pl.col("candidate_allow").sum().alias("candidate_allowed"),
            (pl.col("old_allow_recomputed") != pl.col("candidate_allow"))
            .sum().alias("changed"),
        )
        .with_columns(
            (pl.col("old_allowed") / pl.col("candidates")).alias("old_allow_rate"),
            (pl.col("candidate_allowed") / pl.col("candidates"))
            .alias("candidate_allow_rate"),
        )
        .sort("split", "strategy_tag")
    )
    return detail, summary


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _markdown(report: dict, thresholds: pl.DataFrame, summary: pl.DataFrame) -> str:
    lines = [
        "# S6/S7 D-4 recalibration evidence",
        "",
        "This is research output only; production parameters were not modified.",
        "",
        "## Frozen splits",
        "",
        "- TRAIN: 2025-01-01..2025-12-31 (fit only)",
        "- VALID: 2026-01-01..2026-06-30 (selection only)",
        "- OBS: 2026-07-01..2026-09-30 (observation only)",
        "- Candidate day T uses market regime T-1.",
        "",
        "## Threshold candidates",
        "",
        thresholds.write_csv().strip(),
        "",
        "## Gate frequency",
        "",
        summary.write_csv().strip(),
        "",
        f"Generated at: {report['generated_at']}",
    ]
    return "\n".join(lines) + "\n"


def run(warehouse: Path, decisions_path: Path, out: Path) -> dict:
    raw_decisions = pl.read_csv(decisions_path, try_parse_dates=True)
    decisions, excluded = partition_strategy_scope(raw_decisions)
    regime = WarehouseQuery(Warehouse(warehouse)).market_regime(
        date(2024, 12, 1), OBS[1]
    )
    observations = build_observations(decisions, regime)
    candidate, threshold_rows = fit_thresholds(observations)
    detail, summary = compare_gates(observations, candidate)

    out.mkdir(parents=True, exist_ok=True)
    thresholds = pl.DataFrame(threshold_rows)
    thresholds.write_csv(out / "thresholds.csv")
    summary.write_csv(out / "gate_frequency.csv")
    detail.write_csv(out / "observations.csv")
    excluded.write_csv(out / "excluded_out_of_scope.csv")

    counts = {
        row["split"]: row["count"]
        for row in observations.group_by("split").agg(pl.len().alias("count"))
        .iter_rows(named=True)
    }
    report = {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "safety": {
            "research_only": True,
            "params_modified": False,
            "runtime_config_modified": False,
        },
        "splits": {
            "TRAIN": [str(TRAIN[0]), str(TRAIN[1])],
            "VALID": [str(VALID[0]), str(VALID[1])],
            "OBS": [str(OBS[0]), str(OBS[1])],
        },
        "fit_scope": "TRAIN candidates only; candidate-weighted linear quantiles",
        "temporal_join": "decision T -> immediately preceding market_regime session T-1",
        "decision_sha256": _sha256(decisions_path),
        "decision_rows": raw_decisions.height,
        "eligible_decision_rows": decisions.height,
        "excluded_out_of_scope_rows": excluded.height,
        "excluded_out_of_scope_symbols": sorted(set(excluded["symbol"].to_list())),
        "rows_by_split": counts,
        "old_thresholds": OLD_THRESHOLDS,
        "candidate_thresholds": candidate,
    }
    (out / "recalibration.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / "README.md").write_text(
        _markdown(report, thresholds, summary), encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.warehouse, args.decisions, args.out)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
