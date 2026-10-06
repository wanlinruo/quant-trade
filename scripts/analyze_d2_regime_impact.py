#!/usr/bin/env python3
"""只读量化北交所上市边界修复对市场因子与冻结 Gate 阈值的影响。"""

from __future__ import annotations

import argparse
import json
import math
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from qsys.data.pipeline.regime import (
    _aggregate,
    _in_breadth_universe,
)
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.strategy.structural.params import (
    S6_HC_ADVANCE_NOT_LOW,
    S6_HIGH_MAIN_BREADTH_HIGH,
    S6_LOW_HEAT_LOW_THRESHOLD,
)


LOOKBACK_DAYS = 30
METRICS = (
    "valid_stock_count",
    "market_advance_ratio",
    "market_median_return_1d",
    "prior_limitup_nextday_return_mean",
    "prior_limitup_nextday_return_median",
)
THRESHOLDS = {
    "market_advance_ratio": S6_HC_ADVANCE_NOT_LOW,
    "market_median_return_1d": S6_HIGH_MAIN_BREADTH_HIGH,
    "prior_limitup_nextday_return_mean": S6_LOW_HEAT_LOW_THRESHOLD,
}


def _after_official_listing(
    frame: pl.DataFrame, security_master: pl.DataFrame,
) -> pl.DataFrame:
    """与待发布生产实现同口径，脚本因此可在发布前只读量化。"""
    if frame.height == 0 or security_master.height == 0:
        return frame
    listing = security_master.select("symbol", "list_date").unique("symbol")
    result = frame.join(listing, on="symbol", how="left").filter(
        pl.col("list_date").is_null() | (pl.col("dt") >= pl.col("list_date"))
    )
    if "prev_close" in result.columns:
        result = result.with_columns(
            pl.when(pl.col("dt") == pl.col("list_date"))
            .then(None)
            .otherwise(pl.col("prev_close"))
            .alias("prev_close")
        )
    return result.drop("list_date")


def _different(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is not right
    return not math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-12)


def _metric_summary(rows: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    changed = [row for row in rows if _different(row[f"{metric}_old"], row[f"{metric}_new"])]
    comparable = [
        row for row in changed
        if row[f"{metric}_old"] is not None and row[f"{metric}_new"] is not None
    ]
    for row in comparable:
        row["_abs_change"] = abs(
            float(row[f"{metric}_new"]) - float(row[f"{metric}_old"])
        )
    samples = sorted(comparable, key=lambda row: row["_abs_change"], reverse=True)[:10]
    return {
        "changed_dates": len(changed),
        "first_changed": min((row["dt"] for row in changed), default=None),
        "last_changed": max((row["dt"] for row in changed), default=None),
        "max_absolute_change": max(
            (row["_abs_change"] for row in comparable), default=0.0
        ),
        "samples": [
            {
                "dt": row["dt"], "old": row[f"{metric}_old"],
                "new": row[f"{metric}_new"],
                "absolute_change": row["_abs_change"],
            }
            for row in samples
        ],
    }


def analyze(warehouse: Path, start: date, end: date) -> dict[str, Any]:
    query = WarehouseQuery(Warehouse(warehouse))
    security = query.security_master()
    yearly: list[pl.DataFrame] = []
    removed_rows = 0
    removed_symbols: set[str] = set()

    for year in range(start.year, end.year + 1):
        lo = max(start, date(year, 1, 1))
        hi = min(end, date(year, 12, 31))
        back = lo - timedelta(days=LOOKBACK_DAYS)
        bars = _in_breadth_universe(query.daily_bars(None, back, hi))
        flags = _in_breadth_universe(query.daily_flags(back, hi))
        if bars.height == 0 or flags.height == 0:
            continue
        factors = query.adj_factors(None, back, hi)

        new_bars = _after_official_listing(bars, security)
        removed = bars.join(new_bars.select("symbol", "dt"), on=["symbol", "dt"], how="anti")
        removed_rows += removed.height
        removed_symbols.update(removed["symbol"].to_list())

        old = _aggregate(bars, flags, factors).filter(
            pl.col("dt").is_between(lo, hi)
        )
        new = _aggregate(
            new_bars,
            _after_official_listing(flags, security),
            _after_official_listing(factors, security),
        ).filter(pl.col("dt").is_between(lo, hi))
        joined = old.select("dt", *METRICS).join(
            new.select("dt", *METRICS), on="dt", how="full", coalesce=True,
            suffix="_new",
        )
        joined = joined.rename({metric: f"{metric}_old" for metric in METRICS})
        yearly.append(joined)

    comparison = pl.concat(yearly, how="vertical").sort("dt") if yearly else pl.DataFrame()
    rows = comparison.to_dicts()
    metric_results = {metric: _metric_summary(rows, metric) for metric in METRICS}
    crossings = {}
    for metric, threshold in THRESHOLDS.items():
        crossed = [
            row for row in rows
            if row[f"{metric}_old"] is not None
            and row[f"{metric}_new"] is not None
            and (row[f"{metric}_old"] > threshold)
            != (row[f"{metric}_new"] > threshold)
        ]
        crossings[metric] = {
            "threshold": threshold,
            "crossing_dates": len(crossed),
            "dates": [row["dt"] for row in crossed],
        }

    return {
        "analysis": "D-2 official-listing-boundary market regime impact",
        "generated_at": datetime.now().astimezone().isoformat(),
        "read_only": True,
        "warehouse": str(warehouse),
        "window": {"start": start, "end": end},
        "compared_dates": comparison.height,
        "first_compared": comparison["dt"].min() if comparison.height else None,
        "last_compared": comparison["dt"].max() if comparison.height else None,
        "prelisting_rows_removed": removed_rows,
        "prelisting_symbols_removed": len(removed_symbols),
        "metrics": metric_results,
        "frozen_gate_threshold_crossings": crossings,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    payload = json.dumps(
        analyze(args.warehouse, args.start, args.end),
        ensure_ascii=False, indent=2, default=str,
    ) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
        temporary.write_text(payload)
        os.replace(temporary, args.output)
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
