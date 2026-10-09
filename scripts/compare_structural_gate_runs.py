#!/usr/bin/env python3
"""Compare structural backtest artifacts across the fixed Issue #120 splits.

This is deliberately an artifact-only report: it never opens the warehouse and
cannot mutate strategy parameters.  Returns use the NAV immediately before a
split as their base, while fills and closed lots are attributed by fill and
entry timestamp respectively.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl


@dataclass(frozen=True)
class Split:
    name: str
    start: date
    end: date


SPLITS = (
    Split("TRAIN", date(2025, 1, 1), date(2025, 12, 31)),
    Split("VALID", date(2026, 1, 1), date(2026, 6, 30)),
    Split("OBS", date(2026, 7, 1), date(2026, 9, 30)),
    Split("ALL", date(2025, 1, 1), date(2026, 9, 30)),
)
TRADING_DAYS = 243


def _drawdown(values: list[float]) -> tuple[float, int]:
    peak = values[0]
    worst = 0.0
    underwater = longest = 0
    for value in values:
        if value >= peak:
            peak = value
            underwater = 0
        else:
            underwater += 1
            longest = max(longest, underwater)
            worst = max(worst, 1.0 - value / peak)
    return worst, longest


def _nav_metrics(nav: pl.DataFrame, split: Split) -> dict[str, float | int]:
    before = nav.filter(pl.col("date") < split.start)
    within = nav.filter(pl.col("date").is_between(split.start, split.end))
    if within.is_empty():
        raise ValueError(f"no NAV rows in {split.name}")

    values = within["total_value"].cast(pl.Float64).to_list()
    base = float(before[-1, "total_value"]) if before.height else float(values[0])
    path = [base, *values] if not before.is_empty() else values
    returns = [path[i] / path[i - 1] - 1.0 for i in range(1, len(path))]
    total_return = path[-1] / path[0] - 1.0
    periods = len(returns)
    annualized = (1.0 + total_return) ** (TRADING_DAYS / periods) - 1.0 if periods else 0.0
    mean = sum(returns) / periods if periods else 0.0
    variance = (
        sum((item - mean) ** 2 for item in returns) / (periods - 1)
        if periods > 1
        else 0.0
    )
    volatility = math.sqrt(variance) * math.sqrt(TRADING_DAYS)
    max_drawdown, max_drawdown_days = _drawdown(path)
    return {
        "sessions": within.height,
        "start_value": path[0],
        "end_value": path[-1],
        "total_return": total_return,
        "annualized_return": annualized,
        "volatility": volatility,
        "sharpe": mean * TRADING_DAYS / volatility if volatility > 1e-9 else 0.0,
        "max_drawdown": max_drawdown,
        "max_drawdown_days": max_drawdown_days,
    }


def _read_optional(path: Path) -> pl.DataFrame:
    return pl.read_csv(path, try_parse_dates=True) if path.is_file() else pl.DataFrame()


def compare_run(label: str, run_dir: Path) -> list[dict[str, object]]:
    nav = pl.read_csv(run_dir / "daily_nav.csv", try_parse_dates=True).sort("date")
    fills = _read_optional(run_dir / "fills.csv")
    lots = _read_optional(run_dir / "closed_lots.csv")
    decisions = _read_optional(run_dir / "gate_decisions.csv")
    rows: list[dict[str, object]] = []
    for split in SPLITS:
        row: dict[str, object] = {"run": label, "split": split.name}
        row.update(_nav_metrics(nav, split))

        split_fills = fills
        if fills.height:
            split_fills = fills.filter(
                pl.col("filled_at").dt.date().is_between(split.start, split.end)
            )
        row.update({
            "fills": split_fills.height,
            "buys": (
                split_fills.filter(pl.col("side") == "buy").height if split_fills.height else 0
            ),
            "sells": (
                split_fills.filter(pl.col("side") == "sell").height if split_fills.height else 0
            ),
            "fees": (
                float(split_fills["fee"].cast(pl.Float64).sum()) if split_fills.height else 0.0
            ),
        })

        split_lots = lots
        if lots.height:
            split_lots = lots.filter(
                pl.col("entry_time").dt.date().is_between(split.start, split.end)
            )
        row["closed_lots_by_entry"] = split_lots.height
        row["win_rate_by_entry"] = (
            split_lots.filter(pl.col("return_pct") > 0).height / split_lots.height
            if split_lots.height
            else 0.0
        )

        split_decisions = decisions
        if decisions.height:
            date_col = "date" if "date" in decisions.columns else "decision_date"
            split_decisions = decisions.filter(
                pl.col(date_col).cast(pl.Date).is_between(split.start, split.end)
            )
        row["gate_decisions"] = split_decisions.height
        allow_col = next(
            (name for name in ("real_allow", "allowed") if name in split_decisions.columns),
            None,
        )
        row["gate_allowed"] = (
            split_decisions.filter(pl.col(allow_col)).height
            if split_decisions.height and allow_col
            else 0
        )
        rows.append(row)
    return rows


def _run_spec(value: str) -> tuple[str, Path]:
    label, separator, path = value.partition("=")
    if not separator or not label or not path:
        raise argparse.ArgumentTypeError("run must be LABEL=RUN_DIR")
    return label, Path(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", type=_run_spec, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    rows: list[dict[str, object]] = []
    for label, run_dir in args.run:
        rows.extend(compare_run(label, run_dir))
    result = pl.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result.write_csv(args.out)
    print(result)
    print(f"comparison written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
