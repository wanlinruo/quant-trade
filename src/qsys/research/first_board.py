"""Issue #230 first-board discovery research.

This module deliberately reuses the proven Issue #210 modelling machinery while
changing the population and target.  A first-board candidate must not be closed
at limit on prediction day T; the close label is authoritative
``daily_flags.is_first_board`` on T+1.

The auction/trading verdict is kept separate.  Ranking support alone must never
be mistaken for evidence that the signal is executable or profitable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from qsys.research import limit_up_ranking as ranking


TRAIN = ranking.TRAIN
VALID = ranking.VALID
TEST = ranking.TEST
OBS = ranking.OBS
BASE_FEATURE_COLUMNS = ranking.FEATURE_COLUMNS
MONEYFLOW_FEATURES = (
    "net_mf_amount_ratio", "large_net_amount_ratio",
    "net_mf_3d_ratio", "net_mf_5d_ratio",
)
INDUSTRY_FEATURES = (
    "industry_limit_up_count", "industry_first_board_count",
    "industry_broken_board_rate", "industry_advance_ratio",
    "industry_amount_market_ratio",
)
FEATURE_COLUMNS = BASE_FEATURE_COLUMNS + MONEYFLOW_FEATURES + INDUSTRY_FEATURES
PREDICTION_COLUMNS = ranking.PREDICTION_COLUMNS

SEGMENT_COLD = "cold_20d"
SEGMENT_REBOUND = "rebound_20d"
SEGMENT_COLUMN = "first_board_segment"


@dataclass(frozen=True)
class FirstBoardConfig(ranking.ResearchConfig):
    """Frozen Issue #230 protocol configuration."""

    random_seed: int = 230
    overnight_top_k: int = 20
    max_positions: int = 3
    position_weight: float = 0.10
    require_enrichment: bool = True


def dataset_sql(
    warehouse: Path,
    config: FirstBoardConfig,
    *,
    target_session_override: date | None = None,
) -> str:
    """Build #210's PIT table with the first-board target and T-day exclusion.

    Keeping the SQL transformation small and explicit makes future upstream
    fixes to the shared feature construction flow into this study.  Guards make
    an upstream contract change fail loudly instead of silently changing the
    research population.
    """
    base_sql = ranking.dataset_sql(
        warehouse, config, target_session_override=target_session_override
    )
    old_label = "coalesce(nf.is_limit_up, false) AS actual_close"
    new_label = "coalesce(nf.is_first_board, false) AS actual_close"
    if base_sql.count(old_label) != 1:
        raise RuntimeError("shared ranking label SQL contract changed")
    base_sql = base_sql.replace(old_label, new_label)

    old_filter = "  AND has_status AND NOT is_st AND NOT is_suspended AND NOT is_delisting\n"
    new_filter = old_filter + "  AND is_close_limit = 0\n"
    if base_sql.count(old_filter) != 1:
        raise RuntimeError("shared ranking universe SQL contract changed")
    base_sql = base_sql.replace(old_filter, new_filter)

    if not config.require_enrichment:
        nulls = ",\n".join(
            f"CAST(NULL AS DOUBLE) AS {name}" for name in (*MONEYFLOW_FEATURES,
                                                            *INDUSTRY_FEATURES)
        )
        return f"SELECT base.*, {nulls} FROM ({base_sql}) base"

    def source(dataset: str, *, partitioned: bool = True) -> str:
        pattern = "year=*/part.parquet" if partitioned else "part.parquet"
        path = warehouse / dataset / pattern
        if not list((warehouse / dataset).glob(pattern)):
            raise FileNotFoundError(f"required first-board input is missing: {path}")
        escaped = str(path).replace("'", "''")
        return f"read_parquet('{escaped}', union_by_name=true)"

    moneyflow = source("moneyflow_daily")
    membership = source("industry_membership", partitioned=False)
    daily = source("tushare_daily")
    flags = source("daily_flags")
    return f"""
WITH base AS ({base_sql}),
mf AS (
    SELECT symbol, dt, net_mf_amount,
           buy_lg_amount + buy_elg_amount - sell_lg_amount - sell_elg_amount
             AS large_net_amount,
           sum(net_mf_amount) OVER (
             PARTITION BY symbol ORDER BY dt ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
           ) AS net_mf_3d,
           sum(net_mf_amount) OVER (
             PARTITION BY symbol ORDER BY dt ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
           ) AS net_mf_5d
    FROM {moneyflow}
),
industry_daily AS (
    SELECT d.dt, d.symbol, m.l1_code, d.amount,
           d.close > d.pre_close AS advanced,
           coalesce(f.is_limit_up, false) AS is_limit_up,
           coalesce(f.is_first_board, false) AS is_first_board,
           coalesce(f.is_broken_board, false) AS is_broken_board
    FROM {daily} d
    JOIN {membership} m ON m.symbol = d.symbol
      AND m.in_date <= d.dt AND (m.out_date IS NULL OR m.out_date >= d.dt)
    JOIN {flags} f ON f.symbol = d.symbol AND f.dt = d.dt
),
industry AS (
    SELECT dt, l1_code,
           sum(is_limit_up::INTEGER) AS industry_limit_up_count,
           sum(is_first_board::INTEGER) AS industry_first_board_count,
           avg(is_broken_board::INTEGER) AS industry_broken_board_rate,
           avg(advanced::INTEGER) AS industry_advance_ratio,
           sum(amount) AS industry_amount
    FROM industry_daily GROUP BY dt, l1_code
),
active_membership AS (
    SELECT b.prediction_date, b.symbol, min(m.l1_code) AS l1_code,
           count(*) AS membership_count
    FROM base b JOIN {membership} m ON m.symbol = b.symbol
      AND m.in_date <= b.prediction_date
      AND (m.out_date IS NULL OR m.out_date >= b.prediction_date)
    GROUP BY b.prediction_date, b.symbol
)
SELECT base.*,
       mf.net_mf_amount / nullif(exp(base.log_amount), 0) AS net_mf_amount_ratio,
       mf.large_net_amount / nullif(exp(base.log_amount), 0) AS large_net_amount_ratio,
       mf.net_mf_3d / nullif(exp(base.log_amount), 0) AS net_mf_3d_ratio,
       mf.net_mf_5d / nullif(exp(base.log_amount), 0) AS net_mf_5d_ratio,
       ind.industry_limit_up_count,
       ind.industry_first_board_count,
       ind.industry_broken_board_rate,
       ind.industry_advance_ratio,
       ind.industry_amount / nullif(base.market_total_money, 0)
         AS industry_amount_market_ratio
FROM base
LEFT JOIN mf ON mf.symbol = base.symbol AND mf.dt = base.prediction_date
LEFT JOIN active_membership am ON am.symbol = base.symbol
  AND am.prediction_date = base.prediction_date AND am.membership_count = 1
LEFT JOIN industry ind ON ind.dt = base.prediction_date AND ind.l1_code = am.l1_code
"""


def add_segment(frame: pl.DataFrame) -> pl.DataFrame:
    """Classify cold starts versus recent limit-up-memory candidates."""
    if "close_count_20d" not in frame.columns:
        raise ValueError("first-board dataset misses close_count_20d")
    return frame.with_columns(
        pl.when(pl.col("close_count_20d").fill_null(0) == 0)
        .then(pl.lit(SEGMENT_COLD))
        .otherwise(pl.lit(SEGMENT_REBOUND))
        .alias(SEGMENT_COLUMN)
    )


def materialise_dataset(
    warehouse: Path,
    output: Path,
    config: FirstBoardConfig = FirstBoardConfig(),
) -> dict[str, Any]:
    """Materialise and fingerprint the first-board research table."""
    import duckdb

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    con = duckdb.connect()
    try:
        con.execute(f"PRAGMA threads={max(config.threads, 1)}")
        escaped = str(temporary).replace("'", "''")
        con.execute(
            f"COPY ({dataset_sql(warehouse, config)}) TO '{escaped}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)"
        )
    finally:
        con.close()
    frame = add_segment(pl.read_parquet(temporary))
    frame.write_parquet(output, compression="zstd")
    temporary.unlink(missing_ok=True)
    return dataset_manifest(output)


def dataset_manifest(output: Path) -> dict[str, Any]:
    frame = pl.read_parquet(output)
    required = {*ranking.IDENTITY_COLUMNS, *FEATURE_COLUMNS, SEGMENT_COLUMN}
    if missing := required - set(frame.columns):
        raise ValueError(f"first-board dataset misses columns: {sorted(missing)}")
    invalid = frame.filter(pl.col("actual_close") & ~pl.col("actual_touch")).height
    if invalid:
        raise ValueError(f"label invariant violated: {invalid} first boards did not touch")
    duplicates = frame.select(
        pl.struct("prediction_date", "target_session", "symbol").is_duplicated().sum()
    ).item()
    if duplicates:
        raise ValueError(f"first-board dataset contains {duplicates} duplicate identities")
    summary = frame.group_by("split", SEGMENT_COLUMN).agg(
        pl.len().alias("rows"),
        pl.col("prediction_date").n_unique().alias("sessions"),
        pl.col("actual_touch").sum().alias("touches"),
        pl.col("actual_close").sum().alias("first_boards"),
    ).sort(["split", SEGMENT_COLUMN])
    return {
        "path": str(output),
        "sha256": ranking.file_sha256(output),
        "rows": summary.to_dicts(),
        "features": list(FEATURE_COLUMNS),
        "segment": "cold_20d iff T-day rolling close_count_20d is zero",
    }


def scoring_frame(
    warehouse: Path,
    prediction_date: date,
    target_session: date,
    *,
    threads: int = 2,
    min_observations: int = 60,
) -> pl.DataFrame:
    """Build a current T-day slice without requiring T+1 outcome facts."""
    import duckdb
    config = FirstBoardConfig(
        history_start=prediction_date - timedelta(days=400),
        feature_start=prediction_date,
        feature_end=prediction_date,
        min_observations=min_observations,
        threads=max(threads, 1),
        require_enrichment=True,
    )
    con = duckdb.connect()
    try:
        con.execute(f"PRAGMA threads={config.threads}")
        frame = con.execute(dataset_sql(
            warehouse, config, target_session_override=target_session,
        )).pl()
    finally:
        con.close()
    if frame.is_empty():
        raise ValueError(f"no eligible first-board scoring rows for {prediction_date}")
    return add_segment(frame)


def rerank_legacy_predictions(
    dataset: pl.DataFrame, legacy: pl.DataFrame
) -> pl.DataFrame:
    """Freeze #210 as a comparator after applying #230's eligible universe."""
    required = [
        "prediction_date", "target_session", "symbol", "model",
        "p_touch", "p_seal_given_touch", "p_close",
    ]
    if missing := set(required) - set(legacy.columns):
        raise ValueError(f"legacy ranking misses columns: {sorted(missing)}")
    eligible = dataset.filter(pl.col("split").is_in(["TEST", "OBS"])).select(
        *ranking.IDENTITY_COLUMNS
    ).unique(
        subset=["prediction_date", "target_session", "symbol"]
    )
    joined = eligible.join(
        legacy.filter(pl.col("model") == "lightgbm").select(*required),
        on=["prediction_date", "target_session", "symbol"], how="inner",
    )
    expected = eligible.group_by("prediction_date").len().rename({"len": "expected"})
    actual = joined.group_by("prediction_date").len().rename({"len": "actual"})
    gaps = expected.join(actual, on="prediction_date", how="left").filter(
        pl.col("actual").fill_null(0) != pl.col("expected")
    )
    if gaps.height:
        raise ValueError("legacy ranking does not cover the complete first-board universe")
    return joined.with_columns(
        pl.lit("legacy_210").alias("model"),
        pl.col("p_close").rank("ordinal", descending=True)
        .over("prediction_date").cast(pl.Int64).alias("rank"),
    ).select(*PREDICTION_COLUMNS).sort(["prediction_date", "rank"])


def walk_forward_predictions(
    dataset: pl.DataFrame, config: FirstBoardConfig,
) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, ranking.TwoStageModel]]:
    """Compare frozen feature ablations on identical expanding windows."""
    specs = (
        ("logistic", "logistic", FEATURE_COLUMNS),
        ("lightgbm_price", "lightgbm", BASE_FEATURE_COLUMNS),
        ("lightgbm_moneyflow", "lightgbm", BASE_FEATURE_COLUMNS + MONEYFLOW_FEATURES),
        ("lightgbm_industry", "lightgbm", BASE_FEATURE_COLUMNS + INDUSTRY_FEATURES),
        ("lightgbm", "lightgbm", FEATURE_COLUMNS),
    )
    outputs: list[pl.DataFrame] = []
    importances: list[pl.DataFrame] = []
    last_models: dict[str, ranking.TwoStageModel] = {}
    months = sorted({
        day.replace(day=1)
        for day, split in dataset.select("prediction_date", "split").iter_rows()
        if split in {"VALID", "TEST", "OBS"}
    })
    for month in months:
        calibration_start = ranking._shift_month(month, -config.calibration_months)
        train = dataset.filter(pl.col("prediction_date") < calibration_start)
        calibration = dataset.filter(pl.col("prediction_date").is_between(
            calibration_start, month - timedelta(days=1)
        ))
        target = dataset.filter(pl.col("prediction_date").is_between(
            month, ranking._shift_month(month, 1) - timedelta(days=1)
        ))
        if train.is_empty() or calibration.is_empty() or target.is_empty():
            raise ValueError(f"insufficient first-board data for {month}")
        for name, kind, features in specs:
            model = ranking.TwoStageModel(
                kind, seed=config.random_seed, threads=config.threads,
                feature_columns=features,
            ).fit(train, calibration)
            p_touch, p_seal, p_close = model.predict(target)
            outputs.append(ranking._prediction_rows(
                target, model=name, p_touch=p_touch,
                p_seal=p_seal, p_close=p_close,
            ))
            if kind == "lightgbm":
                importances.append(model.feature_importance().with_columns(
                    pl.lit(month).alias("prediction_month"),
                    pl.lit(name).alias("model"),
                ))
            last_models[name] = model
    return (
        pl.concat(outputs, how="vertical_relaxed").sort(
            ["prediction_date", "model", "rank"]
        ),
        pl.concat(importances, how="vertical_relaxed") if importances else pl.DataFrame(),
        last_models,
    )


def selected_candidate(metrics: pl.DataFrame) -> str:
    """Freeze the feature ablation winner exclusively on VALID Precision@20."""
    candidates = (
        "lightgbm_price", "lightgbm_moneyflow", "lightgbm_industry", "lightgbm",
    )
    rows = metrics.filter(
        (pl.col("split") == "VALID") & (pl.col("label") == "actual_close")
        & (pl.col("k") == 20) & pl.col("model").is_in(candidates)
    )
    values = {row["model"]: float(row["precision"]) for row in rows.to_dicts()}
    if set(values) != set(candidates):
        raise ValueError("VALID metrics do not cover every LightGBM ablation")
    # Tuple order is the deterministic simplicity tie-breaker.
    return max(candidates, key=lambda name: (values[name], -candidates.index(name)))


def segment_breakdown(
    predictions: pl.DataFrame, dataset: pl.DataFrame, *, k: int = 20,
) -> pl.DataFrame:
    segments = dataset.select(
        "prediction_date", "target_session", "symbol", SEGMENT_COLUMN
    ).unique()
    return (
        predictions.filter(pl.col("rank") <= k)
        .join(segments, on=["prediction_date", "target_session", "symbol"], how="left")
        .group_by("model", "split", SEGMENT_COLUMN)
        .agg(
            pl.len().alias("predictions"),
            pl.col("actual_close").sum().alias("hits"),
            pl.col("actual_close").mean().alias("precision"),
        )
        .with_columns(pl.lit(k).alias("k"))
        .sort(["split", "model", SEGMENT_COLUMN])
    )


def ranking_verdict(
    predictions: pl.DataFrame,
    metrics: pl.DataFrame,
    config: FirstBoardConfig,
) -> dict[str, Any]:
    """Apply ranking gates while keeping trading promotion impossible."""
    candidate = selected_candidate(metrics)
    base = ranking.promotion_verdict(
        predictions, metrics, config,
        candidate=candidate,
        baselines=("legacy_210", "momentum", "heuristic", "logistic"),
    )
    return {
        "ranking_support": bool(base["promote_to_shadow"]),
        "promote_to_shadow": False,
        "promote_to_trading": False,
        "auction_evidence": "missing",
        "selected_on_valid": candidate,
        "research_status": (
            "RANKING_PASS_TRADING_PENDING"
            if base["promote_to_shadow"] else "RANKING_FAIL"
        ),
        **{key: value for key, value in base.items() if key != "promote_to_shadow"},
    }


def config_dict(config: FirstBoardConfig) -> dict[str, Any]:
    return {
        key: str(value) if isinstance(value, date) else value
        for key, value in asdict(config).items()
    }
