"""Parity gate between historical auction facts and QMT 09:25 snapshots."""

from __future__ import annotations

import math
from typing import Iterable

import polars as pl


MIN_MATCHED_ROWS = 1_000
MIN_SESSIONS = 20
MIN_PRICE_MATCH_RATE = 0.995
MIN_FLOW_MATCH_RATE = 0.99


def _best_scale(source: Iterable[float], reference: Iterable[float]) -> float:
    pairs = [(float(a), float(b)) for a, b in zip(source, reference, strict=True)
             if a is not None and b is not None and float(a) > 0 and float(b) > 0]
    if not pairs:
        raise ValueError("no positive overlap for unit inference")
    candidates = (0.01, 1.0, 100.0, 1_000.0, 10_000.0)
    return min(candidates, key=lambda scale: sorted(
        abs(a * scale / b - 1.0) for a, b in pairs
    )[len(pairs) // 2])


def evaluate_parity(history: pl.DataFrame, ticks: pl.DataFrame) -> dict[str, object]:
    """Infer units and apply a fail-closed overlap gate."""
    required_history = {"symbol", "dt", "open", "volume_raw", "amount_raw"}
    required_ticks = {"symbol", "dt", "ts", "last_price", "volume", "amount"}
    if missing := required_history - set(history.columns):
        raise ValueError(f"auction history misses columns: {sorted(missing)}")
    if missing := required_ticks - set(ticks.columns):
        raise ValueError(f"auction ticks miss columns: {sorted(missing)}")
    final = (
        ticks.filter((pl.col("last_price") > 0) & (pl.col("volume") > 0))
        .sort("ts").group_by("symbol", "dt", maintain_order=True).last()
        .select("symbol", "dt", "last_price", "volume", "amount")
    )
    joined = history.join(final, on=["symbol", "dt"], how="inner").drop_nulls(
        ["open", "volume_raw", "amount_raw", "last_price", "volume", "amount"]
    )
    if joined.is_empty():
        raise ValueError("historical auction and QMT ticks have no overlap")
    volume_scale = _best_scale(joined["volume_raw"], joined["volume"])
    amount_scale = _best_scale(joined["amount_raw"], joined["amount"])
    measured = joined.with_columns(
        (pl.col("open") - pl.col("last_price")).abs().alias("price_error"),
        ((pl.col("volume_raw") * volume_scale / pl.col("volume")) - 1).abs()
        .alias("volume_relative_error"),
        ((pl.col("amount_raw") * amount_scale / pl.col("amount")) - 1).abs()
        .alias("amount_relative_error"),
    )
    rows = measured.height
    sessions = measured["dt"].n_unique()
    price_rate = float((measured["price_error"] <= 0.005).mean())
    volume_rate = float((measured["volume_relative_error"] <= 0.01).mean())
    amount_rate = float((measured["amount_relative_error"] <= 0.01).mean())
    checks = {
        "matched_rows_at_least_1000": rows >= MIN_MATCHED_ROWS,
        "sessions_at_least_20": sessions >= MIN_SESSIONS,
        "price_match_rate_at_least_99_5pct": price_rate >= MIN_PRICE_MATCH_RATE,
        "volume_match_rate_at_least_99pct": volume_rate >= MIN_FLOW_MATCH_RATE,
        "amount_match_rate_at_least_99pct": amount_rate >= MIN_FLOW_MATCH_RATE,
        "scales_are_finite": math.isfinite(volume_scale) and math.isfinite(amount_scale),
    }
    return {
        "auction_parity_pass": all(checks.values()),
        "checks": checks,
        "matched_rows": rows,
        "sessions": sessions,
        "price_match_rate": price_rate,
        "volume_match_rate": volume_rate,
        "amount_match_rate": amount_rate,
        "volume_scale_to_qmt_lots": volume_scale,
        "amount_scale_to_qmt_cny": amount_scale,
    }
