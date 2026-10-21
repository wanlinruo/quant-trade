"""Leak-free T+1 limit-up ranking research.

The module has three deliberately separate layers:

* materialise a point-in-time feature/label table from the warehouse;
* fit fixed, reproducible baselines and two-stage probability models;
* evaluate daily rankings without ever feeding TEST/OBS outcomes into choices.

Nothing here writes strategy parameters or runtime configuration.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence

import duckdb
import polars as pl


TRAIN = (date(2023, 4, 1), date(2024, 12, 31))
VALID = (date(2025, 1, 1), date(2025, 6, 30))
TEST = (date(2025, 7, 1), date(2025, 12, 31))
OBS = (date(2026, 1, 1), date(2026, 9, 30))
SPLITS = (("TRAIN", TRAIN), ("VALID", VALID), ("TEST", TEST), ("OBS", OBS))

LABEL_COLUMNS = ("actual_touch", "actual_close")
IDENTITY_COLUMNS = (
    "prediction_date", "target_session", "symbol", "board", "market_state", "split",
    "actual_touch", "actual_close", "opened_at_limit", "one_price_limit",
)
PREDICTION_COLUMNS = IDENTITY_COLUMNS + (
    "model", "p_touch", "p_seal_given_touch", "p_close", "rank",
)

STOCK_FEATURES = (
    "ret_1d", "ret_3d", "ret_5d", "ret_10d", "ret_20d", "ret_60d",
    "volatility_5d", "volatility_20d", "volatility_60d",
    "intraday_return", "range_pct", "distance_high_20d",
    "distance_high_60d", "position_60d", "amount_ratio_5d",
    "amount_ratio_20d", "turnover_ratio", "free_turnover_ratio",
    "volume_ratio", "log_amount", "log_market_cap", "log_circulating_market_cap",
    "close_count_5d", "close_count_10d", "close_count_20d", "close_count_60d",
    "touch_count_5d", "touch_count_10d", "touch_count_20d", "touch_count_60d",
    "broken_count_5d", "broken_count_10d", "broken_count_20d", "broken_count_60d",
    "ret_1d_rank", "ret_5d_rank", "turnover_rank", "volume_ratio_rank",
    "touch_count_20d_rank", "board_main", "board_gem", "board_star",
)
REGIME_FEATURES = (
    "limit_up_count", "limit_down_count", "limit_up_down_ratio",
    "touched_limit_up_count", "broken_board_count", "broken_board_rate",
    "first_board_count", "market_advance_ratio", "market_median_return_1d",
    "market_total_money", "prior_limitup_nextday_return_mean",
    "prior_limitup_nextday_return_median", "valid_stock_count",
    "index_ma20_slope_5d",
)
FEATURE_COLUMNS = STOCK_FEATURES + REGIME_FEATURES
KS = (10, 20, 50, 100)


@dataclass(frozen=True)
class ResearchConfig:
    feature_start: date = date(2023, 4, 1)
    feature_end: date = date(2026, 9, 30)
    history_start: date = date(2023, 1, 1)
    min_observations: int = 60
    calibration_months: int = 6
    random_seed: int = 210
    bootstrap_samples: int = 2_000
    threads: int = 2


def split_for(day: date) -> str | None:
    for name, (start, end) in SPLITS:
        if start <= day <= end:
            return name
    return None


def _parquet(root: Path, dataset: str, *, partitioned: bool = True) -> str:
    pattern = "year=*/part.parquet" if partitioned else "part.parquet"
    path = root / dataset / pattern
    if not list((root / dataset).glob(pattern)):
        raise FileNotFoundError(f"required research dataset is missing: {path}")
    return str(path)


def _read(path: str) -> str:
    escaped = path.replace("'", "''")
    return f"read_parquet('{escaped}', union_by_name=true)"


def dataset_sql(
    warehouse: Path, config: ResearchConfig, *,
    target_session_override: date | None = None,
) -> str:
    """Return the exact DuckDB query used to build the point-in-time dataset."""
    bars = _read(_parquet(warehouse, "tushare_daily"))
    flags = _read(_parquet(warehouse, "daily_flags"))
    regime = _read(_parquet(warehouse, "market_regime"))
    status = _read(_parquet(warehouse, "security_status_daily"))
    instruments = _read(_parquet(warehouse, "instruments", partitioned=False))
    start = config.history_start.isoformat()
    end = (config.feature_end + timedelta(days=10)).isoformat()
    feature_start = config.feature_start.isoformat()
    feature_end = config.feature_end.isoformat()
    target_expression = "lead(dt) OVER (ORDER BY dt)"
    if target_session_override is not None:
        if target_session_override <= config.feature_end:
            raise ValueError("target session override must be after feature end")
        target_expression = (
            f"CASE WHEN dt = DATE '{feature_end}' "
            f"THEN DATE '{target_session_override.isoformat()}' "
            "ELSE lead(dt) OVER (ORDER BY dt) END"
        )

    def sums(column: str, prefix: str) -> str:
        return ",\n".join(
            f"sum({column}) over w{window} AS {prefix}_count_{window}d"
            for window in (5, 10, 20, 60)
        )

    return f"""
WITH sessions AS (
    SELECT dt,
           {target_expression} AS target_session
    FROM {regime}
    WHERE dt BETWEEN DATE '{start}' AND DATE '{end}'
),
raw AS (
    SELECT
        b.dt,
        s.target_session,
        b.symbol,
        i.board,
        b.open * coalesce(b.factor, 1.0) AS adj_open,
        b.high * coalesce(b.factor, 1.0) AS adj_high,
        b.low * coalesce(b.factor, 1.0) AS adj_low,
        b.close * coalesce(b.factor, 1.0) AS adj_close,
        b.amount,
        b.turnover_ratio,
        b.free_turnover_ratio,
        b.volume_ratio,
        b.market_cap,
        b.circulating_market_cap,
        coalesce(f.is_limit_up, false)::INTEGER AS is_close_limit,
        coalesce(f.touched_limit_up, false)::INTEGER AS is_touch_limit,
        coalesce(f.is_broken_board, false)::INTEGER AS is_broken,
        coalesce(f.is_st, true) AS is_st,
        st.symbol IS NOT NULL AS has_status,
        coalesce(st.is_suspended, false) AS is_suspended,
        coalesce(st.is_delisting, false) AS is_delisting,
        row_number() OVER (PARTITION BY b.symbol ORDER BY b.dt) AS observations,
        r.limit_up_count, r.limit_down_count, r.limit_up_down_ratio,
        r.touched_limit_up_count, r.broken_board_count, r.broken_board_rate,
        r.first_board_count, r.market_advance_ratio, r.market_median_return_1d,
        r.market_total_money, r.prior_limitup_nextday_return_mean,
        r.prior_limitup_nextday_return_median, r.valid_stock_count,
        r.index_ma20_slope_5d
    FROM {bars} b
    JOIN sessions s ON s.dt = b.dt
    JOIN {flags} f ON f.dt = b.dt AND f.symbol = b.symbol
    JOIN {instruments} i ON i.symbol = b.symbol
    LEFT JOIN {status} st ON st.dt = b.dt AND st.symbol = b.symbol
    JOIN {regime} r ON r.dt = b.dt
    WHERE b.dt BETWEEN DATE '{start}' AND DATE '{end}'
),
lagged AS (
    SELECT *,
        lag(adj_close, 1) OVER symbol_order AS close_lag_1,
        lag(adj_close, 3) OVER symbol_order AS close_lag_3,
        lag(adj_close, 5) OVER symbol_order AS close_lag_5,
        lag(adj_close, 10) OVER symbol_order AS close_lag_10,
        lag(adj_close, 20) OVER symbol_order AS close_lag_20,
        lag(adj_close, 60) OVER symbol_order AS close_lag_60,
        avg(amount) OVER w5 AS amount_avg_5,
        avg(amount) OVER w20 AS amount_avg_20,
        max(adj_high) OVER w20 AS high_max_20,
        max(adj_high) OVER w60 AS high_max_60,
        min(adj_low) OVER w60 AS low_min_60,
        {sums('is_close_limit', 'close')},
        {sums('is_touch_limit', 'touch')},
        {sums('is_broken', 'broken')}
    FROM raw
    WINDOW
        symbol_order AS (PARTITION BY symbol ORDER BY dt),
        w5 AS (PARTITION BY symbol ORDER BY dt ROWS BETWEEN 4 PRECEDING AND CURRENT ROW),
        w10 AS (PARTITION BY symbol ORDER BY dt ROWS BETWEEN 9 PRECEDING AND CURRENT ROW),
        w20 AS (PARTITION BY symbol ORDER BY dt ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
        w60 AS (PARTITION BY symbol ORDER BY dt ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
),
returns AS (
    SELECT *,
        adj_close / nullif(close_lag_1, 0) - 1 AS ret_1d,
        adj_close / nullif(close_lag_3, 0) - 1 AS ret_3d,
        adj_close / nullif(close_lag_5, 0) - 1 AS ret_5d,
        adj_close / nullif(close_lag_10, 0) - 1 AS ret_10d,
        adj_close / nullif(close_lag_20, 0) - 1 AS ret_20d,
        adj_close / nullif(close_lag_60, 0) - 1 AS ret_60d,
        adj_close / nullif(adj_open, 0) - 1 AS intraday_return,
        (adj_high - adj_low) / nullif(close_lag_1, 0) AS range_pct,
        adj_close / nullif(high_max_20, 0) - 1 AS distance_high_20d,
        adj_close / nullif(high_max_60, 0) - 1 AS distance_high_60d,
        (adj_close - low_min_60) / nullif(high_max_60 - low_min_60, 0) AS position_60d,
        amount / nullif(amount_avg_5, 0) AS amount_ratio_5d,
        amount / nullif(amount_avg_20, 0) AS amount_ratio_20d,
        ln(greatest(amount, 1.0)) AS log_amount,
        ln(greatest(market_cap, 1.0)) AS log_market_cap,
        ln(greatest(circulating_market_cap, 1.0)) AS log_circulating_market_cap
    FROM lagged
),
volatility AS (
    SELECT *,
        stddev_samp(ret_1d) OVER w5 AS volatility_5d,
        stddev_samp(ret_1d) OVER w20 AS volatility_20d,
        stddev_samp(ret_1d) OVER w60 AS volatility_60d,
        percent_rank() OVER (PARTITION BY dt ORDER BY ret_1d) AS ret_1d_rank,
        percent_rank() OVER (PARTITION BY dt ORDER BY ret_5d) AS ret_5d_rank,
        percent_rank() OVER (PARTITION BY dt ORDER BY turnover_ratio) AS turnover_rank,
        percent_rank() OVER (PARTITION BY dt ORDER BY volume_ratio) AS volume_ratio_rank,
        percent_rank() OVER (PARTITION BY dt ORDER BY touch_count_20d) AS touch_count_20d_rank,
        (board = 'main')::INTEGER AS board_main,
        (board = 'gem')::INTEGER AS board_gem,
        (board = 'star')::INTEGER AS board_star
    FROM returns
    WINDOW
        w5 AS (PARTITION BY symbol ORDER BY dt ROWS BETWEEN 4 PRECEDING AND CURRENT ROW),
        w20 AS (PARTITION BY symbol ORDER BY dt ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
        w60 AS (PARTITION BY symbol ORDER BY dt ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
),
labelled AS (
    SELECT v.*,
        coalesce(nf.touched_limit_up, false) AS actual_touch,
        coalesce(nf.is_limit_up, false) AS actual_close,
        coalesce(nb.open >= nf.limit_up - 0.000001, false) AS opened_at_limit,
        coalesce(nb.low >= nf.limit_up - 0.000001 AND nb.high <= nf.limit_up + 0.000001,
                 false) AS one_price_limit
    FROM volatility v
    LEFT JOIN {flags} nf
      ON nf.dt = v.target_session AND nf.symbol = v.symbol
    LEFT JOIN {bars} nb
      ON nb.dt = v.target_session AND nb.symbol = v.symbol
)
SELECT
    dt AS prediction_date,
    target_session,
    symbol,
    board,
    CASE
      WHEN dt BETWEEN DATE '{TRAIN[0]}' AND DATE '{TRAIN[1]}' THEN 'TRAIN'
      WHEN dt BETWEEN DATE '{VALID[0]}' AND DATE '{VALID[1]}' THEN 'VALID'
      WHEN dt BETWEEN DATE '{TEST[0]}' AND DATE '{TEST[1]}' THEN 'TEST'
      WHEN dt BETWEEN DATE '{OBS[0]}' AND DATE '{OBS[1]}' THEN 'OBS'
    END AS split,
    CASE
      WHEN market_advance_ratio < 0.4 THEN 'weak'
      WHEN market_advance_ratio > 0.6 THEN 'strong'
      ELSE 'neutral'
    END AS market_state,
    actual_touch,
    actual_close,
    opened_at_limit,
    one_price_limit,
    {', '.join(FEATURE_COLUMNS)}
FROM labelled
WHERE dt BETWEEN DATE '{feature_start}' AND DATE '{feature_end}'
  AND target_session IS NOT NULL
  AND observations >= {config.min_observations}
  AND board IN ('main', 'gem', 'star')
  AND (symbol LIKE '%.SH' OR symbol LIKE '%.SZ')
  AND has_status AND NOT is_st AND NOT is_suspended AND NOT is_delisting
"""


def scoring_frame(
    warehouse: Path, prediction_date: date, target_session: date, *,
    threads: int = 2, min_observations: int = 60,
) -> pl.DataFrame:
    """Build one current feature slice without requiring future outcome rows.

    The explicit target comes from the authoritative trading calendar.  It only
    replaces the final session link; all feature values still come from T-day
    warehouse data through the frozen research SQL.
    """
    config = ResearchConfig(
        history_start=prediction_date - timedelta(days=400),
        feature_start=prediction_date,
        feature_end=prediction_date,
        min_observations=min_observations,
        threads=max(threads, 1),
    )
    from qsys.data.query_budget import connect_warehouse_query

    con = connect_warehouse_query()
    try:
        con.execute(f"PRAGMA threads={config.threads}")
        frame = con.execute(dataset_sql(
            warehouse, config, target_session_override=target_session,
        )).pl()
    finally:
        con.close()
    if frame.is_empty():
        raise ValueError(f"no eligible scoring rows for {prediction_date}")
    if frame["prediction_date"].unique().to_list() != [prediction_date]:
        raise ValueError("scoring frame contains an unexpected prediction date")
    if frame["target_session"].unique().to_list() != [target_session]:
        raise ValueError("scoring frame target does not match trading calendar")
    return frame


def materialise_dataset(
    warehouse: Path, output: Path, config: ResearchConfig = ResearchConfig()
) -> dict[str, Any]:
    """Write the research table atomically enough for an isolated run directory."""
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    con = duckdb.connect()
    con.execute(f"PRAGMA threads={max(config.threads, 1)}")
    sql = dataset_sql(warehouse, config)
    escaped = str(temporary).replace("'", "''")
    con.execute(
        f"COPY ({sql}) TO '{escaped}' "
        "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)"
    )
    con.close()
    temporary.replace(output)
    return dataset_manifest(output)


def dataset_manifest(output: Path) -> dict[str, Any]:
    """Validate and fingerprint an existing materialised research dataset."""
    frame = pl.scan_parquet(output)
    summary = (
        frame.group_by("split")
        .agg(
            pl.len().alias("rows"),
            pl.col("prediction_date").n_unique().alias("sessions"),
            pl.col("actual_touch").sum().alias("touches"),
            pl.col("actual_close").sum().alias("closes"),
        )
        .collect()
        .sort("split")
    )
    invalid = frame.filter(pl.col("actual_close") & ~pl.col("actual_touch")).select(
        pl.len()
    ).collect().item()
    if invalid:
        raise ValueError(f"label invariant violated: {invalid} closes did not touch limit")
    return {
        "path": str(output),
        "sha256": file_sha256(output),
        "rows": summary.to_dicts(),
        "features": list(FEATURE_COLUMNS),
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def heuristic_score(frame: pl.DataFrame) -> pl.Series:
    return frame.select(
        (
            pl.col("ret_1d_rank") * 0.30
            + pl.col("ret_5d_rank") * 0.25
            + pl.col("turnover_rank") * 0.20
            + pl.col("volume_ratio_rank") * 0.15
            + pl.col("touch_count_20d_rank") * 0.10
        ).alias("score")
    )["score"]


def _month_floor(day: date) -> date:
    return day.replace(day=1)


def _shift_month(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def evaluation_months(frame: pl.DataFrame) -> list[date]:
    return sorted({
        _month_floor(day)
        for day, split in frame.select("prediction_date", "split").iter_rows()
        if split in {"TEST", "OBS"}
    })


def _matrix(
    frame: pl.DataFrame, feature_columns: Sequence[str] = FEATURE_COLUMNS,
) -> Any:
    import numpy as np

    return frame.select(feature_columns).cast(pl.Float32).to_numpy().astype(
        np.float32, copy=False
    )


def _labels(frame: pl.DataFrame, column: str) -> Any:
    import numpy as np

    return np.asarray(frame[column].to_list(), dtype=np.int8)


def _logit(values: Any) -> Any:
    import numpy as np

    clipped = np.clip(values, 1e-6, 1 - 1e-6)
    return np.log(clipped / (1 - clipped)).reshape(-1, 1)


class _ProbabilityCalibrator:
    def __init__(self) -> None:
        self._model: Any | None = None

    def fit(self, probability: Any, labels: Any) -> "_ProbabilityCalibrator":
        import numpy as np

        if len(labels) and len(np.unique(labels)) > 1:
            from sklearn.linear_model import LogisticRegression

            self._model = LogisticRegression(C=1.0, solver="lbfgs", random_state=210)
            self._model.fit(_logit(probability), labels)
        return self

    def predict(self, probability: Any) -> Any:
        import numpy as np

        if self._model is None:
            return np.clip(probability, 0.0, 1.0)
        return self._model.predict_proba(_logit(probability))[:, 1]


class TwoStageModel:
    """P(touch) × P(close | touch), with optional Platt calibration."""

    def __init__(
        self, kind: str, *, seed: int, threads: int,
        feature_columns: Sequence[str] = FEATURE_COLUMNS,
    ) -> None:
        self.kind = kind
        self.seed = seed
        self.threads = threads
        self.feature_columns = tuple(feature_columns)
        self.touch: Any | None = None
        self.seal: Any | None = None
        self.touch_calibrator = _ProbabilityCalibrator()
        self.seal_calibrator = _ProbabilityCalibrator()

    def _estimator(self) -> Any:
        if self.kind == "lightgbm":
            try:
                from lightgbm import LGBMClassifier
            except (ImportError, OSError) as exc:  # pragma: no cover - CLI error path
                raise RuntimeError(
                    "LightGBM research dependencies are unavailable; run "
                    "`uv sync --group dev` and install the "
                    "platform OpenMP runtime when required"
                ) from exc
            return LGBMClassifier(
                objective="binary", n_estimators=300, learning_rate=0.05,
                num_leaves=31, min_child_samples=200, subsample=0.8,
                subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
                class_weight="balanced", random_state=self.seed,
                n_jobs=max(self.threads, 1), verbosity=-1,
            )
        if self.kind == "logistic":
            try:
                from sklearn.impute import SimpleImputer
                from sklearn.linear_model import SGDClassifier
                from sklearn.pipeline import make_pipeline
                from sklearn.preprocessing import StandardScaler
            except ImportError as exc:  # pragma: no cover - CLI error path
                raise RuntimeError(
                    "scikit-learn research dependencies are missing; run "
                    "`uv sync --group dev`"
                ) from exc
            return make_pipeline(
                SimpleImputer(strategy="median"),
                StandardScaler(),
                SGDClassifier(
                    loss="log_loss", alpha=1e-5, class_weight="balanced",
                    max_iter=500, tol=1e-4, random_state=self.seed,
                ),
            )
        raise ValueError(f"unknown model kind: {self.kind}")

    def fit(self, train: pl.DataFrame, calibration: pl.DataFrame) -> "TwoStageModel":
        self.touch = self._estimator()
        self.seal = self._estimator()
        x_train = _matrix(train, self.feature_columns)
        touch_y = _labels(train, "actual_touch")
        self.touch.fit(x_train, touch_y)
        touched = train.filter(pl.col("actual_touch"))
        if touched.is_empty() or touched["actual_close"].n_unique() < 2:
            raise ValueError("conditional seal training requires both outcomes")
        self.seal.fit(
            _matrix(touched, self.feature_columns), _labels(touched, "actual_close")
        )

        if calibration.height:
            x_cal = _matrix(calibration, self.feature_columns)
            raw_touch = self.touch.predict_proba(x_cal)[:, 1]
            self.touch_calibrator.fit(raw_touch, _labels(calibration, "actual_touch"))
            touched_cal = calibration.filter(pl.col("actual_touch"))
            if touched_cal.height:
                raw_seal = self.seal.predict_proba(
                    _matrix(touched_cal, self.feature_columns)
                )[:, 1]
                self.seal_calibrator.fit(
                    raw_seal, _labels(touched_cal, "actual_close")
                )
        return self

    def predict(self, frame: pl.DataFrame) -> tuple[Any, Any, Any]:
        if self.touch is None or self.seal is None:
            raise RuntimeError("model has not been fitted")
        x = _matrix(frame, self.feature_columns)
        p_touch = self.touch_calibrator.predict(self.touch.predict_proba(x)[:, 1])
        p_seal = self.seal_calibrator.predict(self.seal.predict_proba(x)[:, 1])
        return p_touch, p_seal, p_touch * p_seal

    def feature_importance(self) -> pl.DataFrame:
        if self.kind != "lightgbm" or self.touch is None or self.seal is None:
            return pl.DataFrame({"feature": [], "touch_gain": [], "seal_gain": []})
        return pl.DataFrame({
            "feature": self.feature_columns,
            "touch_gain": self.touch.booster_.feature_importance("gain"),
            "seal_gain": self.seal.booster_.feature_importance("gain"),
        }).sort("touch_gain", descending=True)


def _prediction_rows(
    frame: pl.DataFrame, *, model: str, p_touch: Sequence[float],
    p_seal: Sequence[float], p_close: Sequence[float],
) -> pl.DataFrame:
    return frame.select(*IDENTITY_COLUMNS).with_columns(
        pl.lit(model).alias("model"),
        pl.Series("p_touch", p_touch, dtype=pl.Float64),
        pl.Series("p_seal_given_touch", p_seal, dtype=pl.Float64),
        pl.Series("p_close", p_close, dtype=pl.Float64),
    ).with_columns(
        pl.col("p_close").rank("ordinal", descending=True).over("prediction_date")
        .cast(pl.Int64).alias("rank")
    )


def walk_forward_predictions(
    dataset: pl.DataFrame, config: ResearchConfig
) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, TwoStageModel]]:
    """Fit frozen model configurations on expanding, strictly prior windows."""
    outputs: list[pl.DataFrame] = []
    importances: list[pl.DataFrame] = []
    last_models: dict[str, TwoStageModel] = {}
    for month in evaluation_months(dataset):
        calibration_start = _shift_month(month, -config.calibration_months)
        train = dataset.filter(pl.col("prediction_date") < calibration_start)
        calibration = dataset.filter(
            pl.col("prediction_date").is_between(calibration_start, month - timedelta(days=1))
        )
        predict_end = _shift_month(month, 1) - timedelta(days=1)
        target = dataset.filter(
            pl.col("prediction_date").is_between(month, predict_end)
        )
        if train.is_empty() or calibration.is_empty() or target.is_empty():
            raise ValueError(f"insufficient walk-forward data for {month}")
        for kind in ("logistic", "lightgbm"):
            model = TwoStageModel(kind, seed=config.random_seed, threads=config.threads)
            model.fit(train, calibration)
            probabilities = model.predict(target)
            outputs.append(_prediction_rows(
                target, model=kind, p_touch=probabilities[0],
                p_seal=probabilities[1], p_close=probabilities[2],
            ))
            if kind == "lightgbm":
                importances.append(model.feature_importance().with_columns(
                    pl.lit(month).alias("prediction_month")
                ))
            last_models[kind] = model
    return (
        pl.concat(outputs, how="vertical_relaxed").sort(
            ["prediction_date", "model", "rank"]
        ),
        pl.concat(importances, how="vertical_relaxed") if importances else pl.DataFrame(),
        last_models,
    )


def baseline_predictions(dataset: pl.DataFrame) -> pl.DataFrame:
    evaluation = dataset.filter(pl.col("split").is_in(["TEST", "OBS"]))
    momentum = evaluation.with_columns(
        pl.col("ret_1d").fill_null(float("-inf")).alias("score")
    )
    heuristic = evaluation.with_columns(heuristic_score(evaluation).alias("score"))
    frames = []
    for name, scored in (("momentum", momentum), ("heuristic", heuristic)):
        rank = scored.select(*IDENTITY_COLUMNS, "score").with_columns(
            pl.lit(name).alias("model"),
            pl.col("score").rank("ordinal", descending=True).over("prediction_date")
            .cast(pl.Int64).alias("rank"),
            pl.lit(None, dtype=pl.Float64).alias("p_touch"),
            pl.lit(None, dtype=pl.Float64).alias("p_seal_given_touch"),
            pl.lit(None, dtype=pl.Float64).alias("p_close"),
        ).drop("score").select(*PREDICTION_COLUMNS)
        frames.append(rank)
    return pl.concat(frames, how="vertical_relaxed")


def _average_precision(labels: list[bool], scores: list[float]) -> float:
    positives = sum(labels)
    if positives == 0:
        return 0.0
    ordered = sorted(zip(scores, labels, strict=True), reverse=True)
    hits = 0
    total = 0.0
    for index, (_score, label) in enumerate(ordered, 1):
        if label:
            hits += 1
            total += hits / index
    return total / positives


def ranking_metrics(predictions: pl.DataFrame) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for (model, split), group in predictions.group_by("model", "split"):
        for label in LABEL_COLUMNS:
            for k in KS:
                daily: list[tuple[float, float, float]] = []
                for (_day,), day_frame in group.group_by("prediction_date"):
                    top = day_frame.sort("rank").head(k)
                    positives = int(day_frame[label].sum())
                    hits = int(top[label].sum())
                    denominator = min(k, day_frame.height)
                    prevalence = positives / day_frame.height if day_frame.height else 0.0
                    precision = hits / denominator if denominator else 0.0
                    recall = hits / positives if positives else 0.0
                    lift = precision / prevalence if prevalence else 0.0
                    daily.append((precision, recall, lift))
                rows.append({
                    "model": model, "split": split, "label": label, "k": k,
                    "sessions": len(daily),
                    "precision": sum(x[0] for x in daily) / len(daily),
                    "recall": sum(x[1] for x in daily) / len(daily),
                    "lift": sum(x[2] for x in daily) / len(daily),
                })
        if group["p_close"].null_count() == 0:
            labels = group["actual_close"].to_list()
            scores = group["p_close"].to_list()
            brier = sum((score - int(label)) ** 2 for score, label in zip(scores, labels, strict=True)) / len(labels)
            log_loss = -sum(
                int(label) * math.log(max(score, 1e-12))
                + (1 - int(label)) * math.log(max(1 - score, 1e-12))
                for score, label in zip(scores, labels, strict=True)
            ) / len(labels)
            rows.append({
                "model": model, "split": split, "label": "actual_close",
                "k": 0, "sessions": group["prediction_date"].n_unique(),
                "precision": None, "recall": _average_precision(labels, scores),
                "lift": None, "brier": brier, "log_loss": log_loss,
            })
    return pl.DataFrame(rows).sort(["split", "label", "k", "model"])


def monthly_precision(predictions: pl.DataFrame, *, k: int = 20) -> pl.DataFrame:
    return (
        predictions.filter(pl.col("rank") <= k)
        .with_columns(pl.col("prediction_date").dt.truncate("1mo").alias("month"))
        .group_by("model", "split", "month")
        .agg(
            pl.col("actual_close").mean().alias("precision_at_k"),
            pl.len().alias("predictions"),
            pl.col("actual_close").sum().alias("hits"),
        )
        .with_columns(pl.lit(k).alias("k"))
        .sort(["split", "month", "model"])
    )


def calibration_table(predictions: pl.DataFrame, *, bins: int = 20) -> pl.DataFrame:
    frames: list[pl.DataFrame] = []
    probability = predictions.filter(pl.col("p_close").is_not_null())
    for (model, split), group in probability.group_by("model", "split"):
        ordered = group.sort("p_close").with_row_index("position")
        ordered = ordered.with_columns(
            (pl.col("position") * bins / max(ordered.height, 1))
            .floor().clip(upper_bound=bins - 1).cast(pl.Int64).alias("bin")
        )
        frames.append(ordered.group_by("bin").agg(
            pl.len().alias("rows"),
            pl.col("p_close").mean().alias("mean_probability"),
            pl.col("actual_close").mean().alias("observed_rate"),
        ).with_columns(pl.lit(model).alias("model"), pl.lit(split).alias("split")))
    return (
        pl.concat(frames, how="vertical_relaxed").select(
            "model", "split", "bin", "rows", "mean_probability", "observed_rate"
        ).sort(["split", "model", "bin"])
        if frames else pl.DataFrame()
    )


def top20_breakdown(predictions: pl.DataFrame) -> pl.DataFrame:
    top = predictions.filter(pl.col("rank") <= 20)
    board = top.group_by("model", "split", "board").agg(
        pl.len().alias("predictions"),
        pl.col("actual_close").sum().alias("hits"),
        pl.col("actual_close").mean().alias("precision"),
        pl.col("opened_at_limit").mean().alias("opened_at_limit_rate"),
        pl.col("one_price_limit").mean().alias("one_price_limit_rate"),
    ).with_columns(pl.lit("board").alias("dimension"), pl.col("board").alias("value"))
    regime = top.group_by("model", "split", "market_state").agg(
        pl.len().alias("predictions"),
        pl.col("actual_close").sum().alias("hits"),
        pl.col("actual_close").mean().alias("precision"),
        pl.col("opened_at_limit").mean().alias("opened_at_limit_rate"),
        pl.col("one_price_limit").mean().alias("one_price_limit_rate"),
    ).with_columns(
        pl.lit("market_state").alias("dimension"),
        pl.col("market_state").alias("value"),
    )
    return pl.concat(
        [
            board.select(
                "model", "split", "dimension", "value", "predictions", "hits",
                "precision", "opened_at_limit_rate", "one_price_limit_rate",
            ),
            regime.select(
                "model", "split", "dimension", "value", "predictions", "hits",
                "precision", "opened_at_limit_rate", "one_price_limit_rate",
            ),
        ],
        how="vertical_relaxed",
    ).sort(["split", "model", "dimension", "value"])


def bootstrap_precision_difference(
    predictions: pl.DataFrame, candidate: str, baseline: str, *, split: str = "OBS",
    k: int = 20, samples: int = 2_000, seed: int = 210,
) -> tuple[float, float, float]:
    daily: dict[str, dict[date, float]] = {}
    for model in (candidate, baseline):
        rows = predictions.filter(
            (pl.col("model") == model) & (pl.col("split") == split) & (pl.col("rank") <= k)
        ).group_by("prediction_date").agg(
            pl.col("actual_close").mean().alias("precision")
        )
        daily[model] = dict(rows.iter_rows())
    days = sorted(set(daily[candidate]) & set(daily[baseline]))
    if not days:
        raise ValueError("no overlapping prediction days for bootstrap")
    differences = [daily[candidate][day] - daily[baseline][day] for day in days]
    observed = sum(differences) / len(differences)
    rng = random.Random(seed)
    draws = sorted(
        sum(differences[rng.randrange(len(differences))] for _ in differences)
        / len(differences)
        for _ in range(samples)
    )
    lo = draws[int(samples * 0.025)]
    hi = draws[min(int(samples * 0.975), samples - 1)]
    return observed, lo, hi


def promotion_verdict(
    predictions: pl.DataFrame, metrics: pl.DataFrame, config: ResearchConfig, *,
    candidate: str = "lightgbm",
    baselines: tuple[str, ...] = ("momentum", "heuristic", "logistic"),
) -> dict[str, Any]:
    headline = metrics.filter(
        (pl.col("label") == "actual_close") & (pl.col("k") == 20)
    )
    values = {
        (row["model"], row["split"]): row
        for row in headline.to_dicts()
    }
    best = max(
        baselines,
        # The comparator is frozen on TEST.  Picking the easiest baseline after
        # looking at OBS would turn the final observation window into selection.
        key=lambda model: float(values[(model, "TEST")]["precision"]),
    )
    test_win = values[(candidate, "TEST")]["precision"] > values[(best, "TEST")]["precision"]
    obs_win = values[(candidate, "OBS")]["precision"] > values[(best, "OBS")]["precision"]
    observed, ci_low, ci_high = bootstrap_precision_difference(
        predictions, candidate, best, samples=config.bootstrap_samples,
        seed=config.random_seed,
    )
    monthly = monthly_precision(predictions)
    pivot = monthly.filter(
        (pl.col("split") == "OBS") & pl.col("model").is_in([candidate, best])
    ).pivot(on="model", index="month", values="precision_at_k")
    month_win_rate = (
        pivot.filter(pl.col(candidate) > pl.col(best)).height / pivot.height
        if pivot.height else 0.0
    )
    lift = float(values[(candidate, "OBS")]["lift"])
    probability = metrics.filter(
        (pl.col("k") == 0) & (pl.col("split") == "OBS")
        & pl.col("model").is_in([candidate, "logistic"])
    )
    probability_rows = {row["model"]: row for row in probability.to_dicts()}
    calibration_ok = (
        probability_rows[candidate]["brier"] <= probability_rows["logistic"]["brier"]
        and probability_rows[candidate]["log_loss"] <= probability_rows["logistic"]["log_loss"]
    )
    checks = {
        "test_beats_best_baseline": bool(test_win),
        "obs_beats_best_baseline": bool(obs_win),
        "obs_bootstrap_ci_low_positive": ci_low > 0,
        "obs_month_win_rate_at_least_60pct": month_win_rate >= 0.60,
        "obs_lift_at_least_3x": lift >= 3.0,
        "probability_quality_not_worse_than_logistic": bool(calibration_ok),
    }
    return {
        "promote_to_shadow": all(checks.values()),
        "best_baseline": best,
        "checks": checks,
        "obs_precision_difference": observed,
        "obs_precision_difference_ci95": [ci_low, ci_high],
        "obs_month_win_rate": month_win_rate,
        "obs_lift_at_20": lift,
    }


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def config_dict(config: ResearchConfig) -> dict[str, Any]:
    return {key: str(value) if isinstance(value, date) else value for key, value in asdict(config).items()}
