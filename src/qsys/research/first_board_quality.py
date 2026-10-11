"""Outcome-aware quality gate for Issue #230 first-board candidates.

The overnight Top-20 is an input contract.  This module decides whether one
candidate is worth attempting at 09:31; it never authorises live trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any, Mapping, Sequence

import numpy as np
import polars as pl


MODEL_FEATURES = (
    "p_touch", "p_seal_given_touch", "p_close", "rank_fraction",
    "auction_gap", "distance_to_limit", "auction_volume_ratio",
    "auction_gap_rank", "distance_to_limit_rank", "auction_volume_rank",
    "free_turnover_ratio", "range_pct", "ret_1d", "ret_5d",
    "distance_high_20d", "limit_up_down_ratio", "limit_up_count",
    "broken_board_rate", "market_advance_ratio",
    "board_main", "board_gem", "board_star",
)
EXIT_POLICIES = ("next_open", "two_stage_limit_hold")
PRIMARY_COST_STRESS = 0.005
MAX_DRAWDOWN = 0.15
MIN_DEVELOPMENT_FILLS = 30
SEALED_TOLERANCE = 0.005
POSITION_WEIGHT = 0.10
FORWARD_MIN_SESSIONS = 120
FORWARD_MIN_FILLS = 50
MIN_SHARPE = 0.5
MIN_POSITIVE_MONTH_RATIO = 0.60
MAX_TOP5_PROFIT_SHARE = 0.50


@dataclass(frozen=True, slots=True)
class GateThresholds:
    min_fill_probability: float
    min_expected_utility: float
    min_downside_quantile: float
    max_block_probability: float = 1.0

    def as_dict(self) -> dict[str, float]:
        return {
            "min_fill_probability": self.min_fill_probability,
            "min_expected_utility": self.min_expected_utility,
            "min_downside_quantile": self.min_downside_quantile,
            "max_block_probability": self.max_block_probability,
        }


@dataclass(frozen=True, slots=True)
class QualityModelBundle:
    exit_policy: str
    features: tuple[str, ...]
    fill_model: Any
    return_model: Any
    downside_model: Any
    block_model: Any | None
    thresholds: GateThresholds


class ConstantProbabilityModel:
    """Pickle-safe fallback for a training slice containing one class."""

    def __init__(self, probability: float) -> None:
        self.probability = float(probability)

    def predict_proba(self, values) -> np.ndarray:
        count = len(values)
        p = np.full(count, self.probability, dtype=float)
        return np.column_stack([1.0 - p, p])


def quality_features(
    scored: pl.DataFrame, overnight: pl.DataFrame, dataset: pl.DataFrame,
) -> pl.DataFrame:
    """Build the fixed, decision-time-only feature contract."""
    identities = ["prediction_date", "target_session", "symbol"]
    probabilities = overnight.select(
        *identities, "p_touch", "p_seal_given_touch", "p_close",
    )
    context_columns = (
        "free_turnover_ratio", "range_pct", "ret_1d", "ret_5d",
        "distance_high_20d", "limit_up_down_ratio", "limit_up_count",
        "broken_board_rate", "market_advance_ratio",
        "board_main", "board_gem", "board_star",
    )
    context = dataset.select(*identities, *context_columns)
    frame = (
        scored.drop("p_close", strict=False)
        .join(probabilities, on=identities, how="inner", validate="1:1")
        .join(context, on=identities, how="inner", validate="1:1")
        .with_columns(
            (pl.col("rank") / 20.0).alias("rank_fraction"),
            (pl.col("limit_up") / pl.col("decision_price") - 1.0).alias(
                "distance_to_limit"
            ),
        )
        .with_columns(
            pl.col("auction_gap").rank("average", descending=True)
            .over("target_session").truediv(20).alias("auction_gap_rank"),
            pl.col("distance_to_limit").rank("average", descending=False)
            .over("target_session").truediv(20).alias("distance_to_limit_rank"),
            pl.col("auction_volume_ratio").rank("average", descending=True)
            .over("target_session").truediv(20).alias("auction_volume_rank"),
        )
    )
    missing = set(MODEL_FEATURES) - set(frame.columns)
    if missing:
        raise ValueError(f"quality feature frame misses columns: {sorted(missing)}")
    invalid = frame.filter(
        pl.any_horizontal([
            pl.col(name).is_null() | ~pl.col(name).is_finite()
            for name in MODEL_FEATURES
        ])
    )
    if invalid.height:
        raise ValueError(f"quality feature frame has {invalid.height} invalid rows")
    return frame.sort("target_session", "rank")


def sealed_at_decision(decision_price: float, limit_up: float) -> bool:
    return decision_price >= limit_up - SEALED_TOLERANCE


def net_return_pct(
    entry_price: float, exit_price: float, qty: int, fee_model,
) -> float:
    """Return on cash committed, including both-side fees."""
    from qsys.core.trading import Side, to_cents

    buy_gross = to_cents(entry_price * qty)
    sell_gross = to_cents(exit_price * qty)
    committed = buy_gross + fee_model.fee_for(Side.BUY, buy_gross)
    proceeds = sell_gross - fee_model.fee_for(Side.SELL, sell_gross)
    return 100.0 * (proceeds / committed - 1.0)


def _matrix(frame: pl.DataFrame) -> np.ndarray:
    return frame.select(MODEL_FEATURES).cast(pl.Float32).to_numpy()


def _classifier(seed: int):
    from lightgbm import LGBMClassifier

    return LGBMClassifier(
        objective="binary", n_estimators=180, learning_rate=0.03,
        num_leaves=15, min_child_samples=100, subsample=0.8, subsample_freq=1,
        colsample_bytree=0.8, reg_lambda=2.0, class_weight="balanced",
        random_state=seed, n_jobs=2, verbosity=-1,
    )


def _regressor(seed: int, *, quantile: bool = False):
    from lightgbm import LGBMRegressor

    params: dict[str, object] = {
        "objective": "quantile" if quantile else "huber",
        "n_estimators": 180, "learning_rate": 0.03, "num_leaves": 15,
        "min_child_samples": 100, "subsample": 0.8, "subsample_freq": 1,
        "colsample_bytree": 0.8, "reg_lambda": 2.0,
        "random_state": seed, "n_jobs": 2, "verbosity": -1,
    }
    if quantile:
        params["alpha"] = 0.10
    return LGBMRegressor(**params)


def _fit_probability(frame: pl.DataFrame, label: str, seed: int):
    values = frame[label].cast(pl.Int8).to_numpy()
    if len(np.unique(values)) < 2:
        return ConstantProbabilityModel(float(values.mean()))
    model = _classifier(seed)
    model.fit(_matrix(frame), values)
    return model


def _return_column(exit_policy: str) -> str:
    if exit_policy not in EXIT_POLICIES:
        raise ValueError(f"unknown exit policy: {exit_policy}")
    return f"{exit_policy}_return_pct"


def fit_models(frame: pl.DataFrame, exit_policy: str, *, seed: int = 230):
    return_col = _return_column(exit_policy)
    fill = _fit_probability(frame, "entry_filled", seed)
    closed = frame.filter(
        pl.col("entry_filled") & pl.col(return_col).is_not_null()
    )
    if closed.height < 30:
        raise ValueError(f"{exit_policy} needs at least 30 closed outcomes")
    mean = _regressor(seed)
    downside = _regressor(seed + 1, quantile=True)
    mean.fit(_matrix(closed), closed[return_col].to_numpy() / 100.0)
    downside.fit(_matrix(closed), closed[return_col].to_numpy() / 100.0)
    block = None
    if exit_policy == "two_stage_limit_hold":
        block = _fit_probability(closed, "two_stage_sell_blocked", seed + 2)
    return fill, mean, downside, block


def predict_models(frame: pl.DataFrame, models, exit_policy: str) -> pl.DataFrame:
    fill, mean, downside, block = models
    values = _matrix(frame)
    p_fill = fill.predict_proba(values)[:, 1]
    expected = mean.predict(values)
    q10 = downside.predict(values)
    p_block = block.predict_proba(values)[:, 1] if block is not None else np.zeros(len(frame))
    return frame.with_columns(
        pl.Series("pred_fill", p_fill),
        pl.Series("pred_return", expected),
        pl.Series("pred_q10", q10),
        pl.Series("pred_block", p_block),
        pl.Series("expected_utility", p_fill * expected),
        pl.lit(exit_policy).alias("exit_policy"),
    )


def walk_forward_predictions(
    frame: pl.DataFrame, exit_policy: str, *, min_train_sessions: int = 100,
    folds: int = 4, seed: int = 230,
) -> pl.DataFrame:
    days = sorted(frame["target_session"].unique().to_list())
    if len(days) <= min_train_sessions + folds:
        raise ValueError("quality walk-forward coverage is too short")
    remaining = days[min_train_sessions:]
    chunks = [part.tolist() for part in np.array_split(remaining, folds) if len(part)]
    outputs = []
    for index, validation_days in enumerate(chunks):
        first = validation_days[0]
        train = frame.filter(pl.col("target_session") < first)
        exit_col = f"{exit_policy}_exit_date"
        # A position opened before the fold boundary may close inside the
        # validation period.  Its entry-time facts remain eligible for the fill
        # head, but its return must not leak into the return/risk heads.
        train = train.with_columns(
            pl.when(pl.col(exit_col) < first)
            .then(pl.col(_return_column(exit_policy)))
            .otherwise(None)
            .alias(_return_column(exit_policy))
        )
        validation = frame.filter(pl.col("target_session").is_in(validation_days))
        models = fit_models(train, exit_policy, seed=seed + index)
        outputs.append(predict_models(validation, models, exit_policy))
    return pl.concat(outputs, how="vertical_relaxed").sort("target_session", "rank")


def select_daily(scored: pl.DataFrame, thresholds: GateThresholds) -> pl.DataFrame:
    eligible = scored.filter(
        (pl.col("pred_fill") >= thresholds.min_fill_probability)
        & (pl.col("expected_utility") >= thresholds.min_expected_utility)
        & (pl.col("pred_q10") >= thresholds.min_downside_quantile)
        & (pl.col("pred_block") <= thresholds.max_block_probability)
    )
    return (
        eligible.sort(
            ["target_session", "expected_utility", "pred_q10", "rank"],
            descending=[False, True, True, False],
        )
        .with_columns(
            (pl.int_range(pl.len()).over("target_session") + 1).alias("quality_rank")
        )
        .filter(pl.col("quality_rank") == 1)
    )


def _max_drawdown(returns: Sequence[float]) -> float:
    value = peak = 1.0
    worst = 0.0
    for ret in returns:
        value *= 1.0 + POSITION_WEIGHT * ret
        peak = max(peak, value)
        worst = max(worst, 1.0 - value / peak)
    return worst


def choose_thresholds(scored: pl.DataFrame, exit_policy: str) -> tuple[GateThresholds, dict]:
    """Choose once on development OOF predictions from a bounded grid."""
    return_col = _return_column(exit_policy)
    fill_grid = (0.50, 0.60, 0.70, 0.80)
    utility_grid = (0.0, 0.0025, 0.005, 0.01)
    downside_grid = (-0.10, -0.08, -0.05, -0.03)
    block_grid = (0.05, 0.10, 0.20) if exit_policy == "two_stage_limit_hold" else (1.0,)
    candidates = []
    for values in product(fill_grid, utility_grid, downside_grid, block_grid):
        thresholds = GateThresholds(*values)
        selected = select_daily(scored, thresholds)
        realised = selected.with_columns(
            pl.when(pl.col("entry_filled") & pl.col(return_col).is_not_null())
            .then(pl.col(return_col) / 100.0).otherwise(0.0).alias("realised")
        ).sort("target_session")
        fills = selected.filter(
            pl.col("entry_filled") & pl.col(return_col).is_not_null()
        ).height
        drawdown = _max_drawdown(realised["realised"].to_list())
        mean = float(realised["realised"].mean()) if realised.height else -1.0
        candidates.append((fills >= MIN_DEVELOPMENT_FILLS and drawdown <= MAX_DRAWDOWN,
                           mean, fills, -drawdown, thresholds, selected.height))
    viable = [row for row in candidates if row[0]]
    if not viable:
        raise ValueError(f"no viable {exit_policy} quality threshold")
    chosen = max(viable, key=lambda row: (row[1], row[2], row[3]))
    selected = select_daily(scored, chosen[4])
    realised = selected.with_columns(
        pl.when(pl.col("entry_filled") & pl.col(return_col).is_not_null())
        .then(pl.col(return_col) / 100.0).otherwise(0.0).alias("trade_return")
    )
    daily = (
        scored.select("target_session").unique().sort("target_session")
        .join(realised.select("target_session", "trade_return"),
              on="target_session", how="left")
        .with_columns(
            (pl.col("trade_return").fill_null(0.0) * POSITION_WEIGHT)
            .alias("account_return")
        )
    )
    account_returns = daily["account_return"].to_list()
    _, ci_low, ci_high = bootstrap_mean_interval(account_returns)
    nav = np.cumprod(1.0 + np.asarray(account_returns, dtype=float))
    mean_account = float(np.mean(account_returns))
    std_account = float(np.std(account_returns, ddof=1))
    return chosen[4], {
        "development_sessions": daily.height,
        "development_selected": chosen[5], "development_fills": chosen[2],
        "development_mean_selected_return": chosen[1],
        "development_mean_account_return_all_sessions": mean_account,
        "development_total_account_return": float(nav[-1] - 1.0),
        "development_adjusted_daily_ci_low": ci_low,
        "development_adjusted_daily_ci_high": ci_high,
        "development_sharpe": (
            mean_account / std_account * np.sqrt(252.0)
            if std_account > 1e-12 else 0.0
        ),
        "development_max_drawdown": -chosen[3],
    }


def bootstrap_mean_interval(
    returns: Sequence[float], *, samples: int = 10_000, seed: int = 230,
    lower_quantile: float = 0.0125,
) -> tuple[float, float, float]:
    """Day bootstrap with a Bonferroni-adjusted lower bound for two policies."""
    if not returns:
        raise ValueError("daily returns are empty")
    values = np.asarray(returns, dtype=float)
    rng = np.random.default_rng(seed)
    means = rng.choice(values, size=(samples, len(values)), replace=True).mean(axis=1)
    return (
        float(values.mean()), float(np.quantile(means, lower_quantile)),
        float(np.quantile(means, 1.0 - lower_quantile)),
    )


def _policy_forward_summary(
    settled: pl.DataFrame, policy: str, *, bootstrap_samples: int,
) -> dict[str, object]:
    rows = settled.filter(pl.col("exit_policy") == policy).sort("target_session")
    sessions = rows["target_session"].n_unique()
    fills = rows.filter(pl.col("entry_filled") & pl.col("closed")).height
    missing = int(rows["missing_fallbacks"].sum()) if rows.height else 0
    open_positions = int(rows["open_positions_at_end"].max()) if rows.height else 0
    by_day = (
        rows.group_by("target_session").agg(
            pl.when(pl.col("entry_filled") & pl.col("closed"))
            .then(pl.col("net_return_pct") / 100.0 * POSITION_WEIGHT)
            .otherwise(0.0).sum().alias("daily_return")
        ).sort("target_session")
    )
    returns = by_day["daily_return"].to_list()
    if returns:
        mean, low, high = bootstrap_mean_interval(
            returns, samples=bootstrap_samples, seed=230,
        )
        values = np.cumprod(1.0 + np.asarray(returns, dtype=float))
        peaks = np.maximum.accumulate(values)
        total_return = float(values[-1] - 1.0)
        max_drawdown = float(np.max(1.0 - values / peaks))
        std = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
        sharpe = mean / std * np.sqrt(252.0) if std > 1e-12 else 0.0
        monthly = (
            by_day.with_columns(
                pl.col("target_session").dt.strftime("%Y-%m").alias("month")
            ).group_by("month").agg(
                ((pl.col("daily_return") + 1.0).product() - 1.0).alias("return")
            )
        )
        positive_month_ratio = float((monthly["return"] > 0).mean())
    else:
        mean = low = high = total_return = max_drawdown = sharpe = 0.0
        positive_month_ratio = 0.0
    profits = sorted(
        (float(value) for value in rows.filter(
            pl.col("entry_filled") & pl.col("closed")
        )["net_return_pct"].to_list() if value > 0), reverse=True,
    )
    top5_share = sum(profits[:5]) / sum(profits) if profits else 1.0
    enough = sessions >= FORWARD_MIN_SESSIONS and fills >= FORWARD_MIN_FILLS
    checks = {
        "minimum_sessions": sessions >= FORWARD_MIN_SESSIONS,
        "minimum_closed_fills": fills >= FORWARD_MIN_FILLS,
        "total_return_positive": total_return > 0,
        "adjusted_daily_ci_low_positive": low > 0,
        "minimum_sharpe": sharpe >= MIN_SHARPE,
        "maximum_drawdown": max_drawdown <= MAX_DRAWDOWN,
        "minimum_positive_month_ratio": positive_month_ratio >= MIN_POSITIVE_MONTH_RATIO,
        "maximum_top5_profit_share": top5_share <= MAX_TOP5_PROFIT_SHARE,
        "no_missing_fallbacks": missing == 0,
        "no_open_positions": open_positions == 0,
    }
    return {
        "exit_policy": policy, "sessions": sessions, "closed_fills": fills,
        "mean_daily_account_return": mean,
        "adjusted_daily_ci_low": low, "adjusted_daily_ci_high": high,
        "total_return": total_return, "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "positive_month_ratio": positive_month_ratio,
        "top5_positive_profit_share": top5_share,
        "missing_fallbacks": missing, "open_positions_at_end": open_positions,
        "enough_observations": enough, "checks": checks,
        "passed": enough and all(checks.values()),
    }


def forward_verdict(
    settled: pl.DataFrame, *, bootstrap_samples: int = 10_000,
) -> dict[str, object]:
    """Apply the frozen prospective gates without authorising any orders."""
    required = {
        "target_session", "exit_policy", "entry_filled", "closed",
        "net_return_pct", "missing_fallbacks", "open_positions_at_end",
    }
    if missing := required - set(settled.columns):
        raise ValueError(f"settled forward records miss columns: {sorted(missing)}")
    unknown = set(settled["exit_policy"].unique().to_list()) - set(EXIT_POLICIES)
    if unknown:
        raise ValueError(f"unknown forward exit policies: {sorted(unknown)}")
    summaries = {
        policy: _policy_forward_summary(
            settled, policy, bootstrap_samples=max(bootstrap_samples, 100),
        ) for policy in EXIT_POLICIES
    }
    enough = all(bool(row["enough_observations"]) for row in summaries.values())
    passing = [row for row in summaries.values() if row["passed"]]
    selected = max(
        passing,
        key=lambda row: (row["adjusted_daily_ci_low"], -row["max_drawdown"]),
        default=None,
    )
    return {
        "schema_version": 1,
        "research_status": (
            "FORWARD_ACCUMULATING" if not enough else
            "FORWARD_PASS" if selected else "FORWARD_FAIL"
        ),
        "selected_exit_policy": selected["exit_policy"] if selected else None,
        "policies": summaries,
        "forward_support": bool(selected),
        "promote_to_shadow": False,
        "promote_to_trading": False,
    }


def fit_frozen_bundle(
    frame: pl.DataFrame, exit_policy: str, thresholds: GateThresholds,
) -> QualityModelBundle:
    models = fit_models(frame, exit_policy)
    return QualityModelBundle(
        exit_policy=exit_policy, features=MODEL_FEATURES,
        fill_model=models[0], return_model=models[1], downside_model=models[2],
        block_model=models[3], thresholds=thresholds,
    )


def score_bundle(frame: pl.DataFrame, bundle: QualityModelBundle) -> pl.DataFrame:
    scored = predict_bundle(frame, bundle)
    return select_daily(scored, bundle.thresholds)


def predict_bundle(frame: pl.DataFrame, bundle: QualityModelBundle) -> pl.DataFrame:
    if bundle.features != MODEL_FEATURES:
        raise ValueError("quality model feature contract mismatch")
    return predict_models(
        frame,
        (bundle.fill_model, bundle.return_model, bundle.downside_model,
         bundle.block_model),
        bundle.exit_policy,
    )
