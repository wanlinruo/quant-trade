"""Issue #229 continuation-strategy artifact summaries and fixed verdict gates."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl


PRIMARY_EXIT = "next_open"
MIN_CLOSED_LOTS = 30
MIN_SHARPE = 0.5
MAX_DRAWDOWN = 0.15
MIN_POSITIVE_MONTH_RATIO = 0.60
MAX_TOP5_PROFIT_SHARE = 0.50


def bootstrap_daily_mean(
    returns: list[float], *, samples: int = 2_000, seed: int = 229,
) -> tuple[float, float, float]:
    """Trading-day bootstrap CI for the arithmetic mean daily return."""
    if not returns:
        raise ValueError("daily returns are empty")
    values = np.asarray(returns, dtype=float)
    rng = np.random.default_rng(seed)
    means = rng.choice(values, size=(samples, len(values)), replace=True).mean(axis=1)
    return (
        float(values.mean()), float(np.quantile(means, 0.025)),
        float(np.quantile(means, 0.975)),
    )


def summarize_run(
    run_dir: Path, *, split: str, exit_policy: str, bootstrap_samples: int = 2_000,
) -> dict[str, object]:
    metrics = {
        row["metric"]: float(row["value"])
        for row in pl.read_csv(run_dir / "metrics.csv").iter_rows(named=True)
    }
    status = pl.read_csv(run_dir / "run_status.csv").row(0, named=True)
    nav = pl.read_csv(run_dir / "daily_nav.csv", try_parse_dates=True)
    months = pl.read_csv(run_dir / "monthly_returns.csv")
    lots = pl.read_csv(run_dir / "closed_lots.csv", try_parse_dates=True)
    orders = pl.read_csv(run_dir / "orders.csv", try_parse_dates=True)
    fills = pl.read_csv(run_dir / "fills.csv", try_parse_dates=True)
    config = __import__("json").loads((run_dir / "config.json").read_text())

    _, ci_low, ci_high = bootstrap_daily_mean(
        nav["daily_return"].to_list(), samples=bootstrap_samples,
    )
    positive_month_ratio = (
        float((months["return"] > 0).mean()) if months.height else 0.0
    )
    profits = sorted(
        (float(value) for value in lots["realized_pnl_gross"].to_list() if value > 0),
        reverse=True,
    )
    top5_profit_share = sum(profits[:5]) / sum(profits) if profits else 1.0
    buy_orders = orders.filter(pl.col("side") == "buy").height
    buy_fills = fills.filter(pl.col("side") == "buy").height
    fill_stats = str(config.get("entry_fill_stats", ""))
    fallback = next(
        (int(item.split("=", 1)[1]) for item in fill_stats.split(",")
         if item.startswith("fallback=")), 0,
    )
    return {
        "split": split, "exit_policy": exit_policy,
        "start": status["start"], "end": status["end"],
        "total_return": metrics.get("total_return", 0.0),
        "sharpe": metrics.get("sharpe", 0.0),
        "max_drawdown": metrics.get("max_drawdown", 0.0),
        "closed_lots": int(status["closed_lots"]),
        "buy_orders": buy_orders, "buy_fills": buy_fills,
        "order_fill_rate": buy_fills / buy_orders if buy_orders else 0.0,
        "daily_mean_ci_low": ci_low, "daily_mean_ci_high": ci_high,
        "positive_month_ratio": positive_month_ratio,
        "top5_positive_profit_share": top5_profit_share,
        "continuous_fallbacks": fallback,
    }


def historical_verdict(rows: list[dict[str, object]]) -> dict[str, object]:
    primary = {
        str(row["split"]): row for row in rows
        if row["exit_policy"] == PRIMARY_EXIT
    }
    checks: dict[str, bool] = {}
    for split in ("TEST", "OBS"):
        row = primary.get(split)
        checks.update({
            f"{split.lower()}_present": row is not None,
            f"{split.lower()}_net_return_positive": bool(
                row and float(row["total_return"]) > 0
            ),
            f"{split.lower()}_daily_bootstrap_low_positive": bool(
                row and float(row["daily_mean_ci_low"]) > 0
            ),
            f"{split.lower()}_sharpe_at_least_{MIN_SHARPE}": bool(
                row and float(row["sharpe"]) >= MIN_SHARPE
            ),
            f"{split.lower()}_max_drawdown_at_most_{MAX_DRAWDOWN}": bool(
                row and float(row["max_drawdown"]) <= MAX_DRAWDOWN
            ),
            f"{split.lower()}_closed_lots_at_least_{MIN_CLOSED_LOTS}": bool(
                row and int(row["closed_lots"]) >= MIN_CLOSED_LOTS
            ),
            f"{split.lower()}_positive_month_ratio_at_least_{MIN_POSITIVE_MONTH_RATIO}": bool(
                row and float(row["positive_month_ratio"]) >= MIN_POSITIVE_MONTH_RATIO
            ),
            f"{split.lower()}_top5_profit_share_at_most_{MAX_TOP5_PROFIT_SHARE}": bool(
                row and float(row["top5_positive_profit_share"]) <= MAX_TOP5_PROFIT_SHARE
            ),
            f"{split.lower()}_no_continuous_fallback": bool(
                row and int(row["continuous_fallbacks"]) == 0
            ),
        })
    passed = all(checks.values())
    return {
        "schema_version": 1,
        "primary_exit": PRIMARY_EXIT,
        "checks": checks,
        "historical_support": passed,
        # TEST/OBS were inspected while forming the hypothesis.  A PASS only opens
        # the prospective observation gate; it never authorizes trading.
        "promote_to_trading": False,
        "prospective_shadow_required": passed,
        "prospective_min_sessions": 40,
        "prospective_min_fills": 12,
    }
