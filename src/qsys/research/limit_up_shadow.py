"""Offline scoring bridge from the promoted #210 model to the #212 snapshot."""

from __future__ import annotations

import hashlib
import io
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl

from qsys.research.limit_up_ranking import FEATURE_COLUMNS, file_sha256, scoring_frame
from qsys.web.limit_up_shadow import (
    LimitUpShadowEntry,
    LimitUpShadowOutcome,
    LimitUpShadowSnapshot,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
DRIFT_SCHEMA_VERSION = 1
DRIFT_BINS = (0.0, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0)


@dataclass(frozen=True, slots=True)
class PromotedModel:
    model: Any
    version: str
    source_sha: str
    artifact_sha: str
    research_data_sha: str


@dataclass(frozen=True, slots=True)
class DriftReference:
    """Compact OBS prediction distribution; never contains per-symbol scores."""

    source_sha: str
    rows: int
    bin_edges: tuple[float, ...]
    proportions: tuple[float, ...]
    schema_version: int = DRIFT_SCHEMA_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source": "issue-210 OBS lightgbm p_close",
            "source_sha": self.source_sha,
            "rows": self.rows,
            "bin_edges": list(self.bin_edges),
            "proportions": list(self.proportions),
        }


def _probability_histogram(
    probabilities: list[float], edges: tuple[float, ...],
) -> tuple[float, ...]:
    if len(edges) < 2 or edges[0] != 0 or edges[-1] != 1:
        raise ValueError("drift bin edges must span 0 through 1")
    if any(left >= right for left, right in zip(edges, edges[1:])):
        raise ValueError("drift bin edges must be strictly increasing")
    if not probabilities:
        raise ValueError("drift distribution is empty")
    counts = [0] * (len(edges) - 1)
    for raw in probabilities:
        value = float(raw)
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("drift distribution contains an invalid probability")
        index = len(counts) - 1
        for candidate, upper in enumerate(edges[1:]):
            if value < upper:
                index = candidate
                break
        counts[index] += 1
    return tuple(count / len(probabilities) for count in counts)


def build_drift_reference(predictions_path: Path) -> DriftReference:
    """Build a compact reference from the frozen #210 OBS LightGBM output."""
    path = Path(predictions_path)
    predictions = pl.read_parquet(path).filter(
        (pl.col("model") == "lightgbm") & (pl.col("split") == "OBS")
    )
    if "p_close" not in predictions.columns:
        raise ValueError("research predictions are missing p_close")
    values = predictions["p_close"].drop_nulls().to_list()
    if len(values) != predictions.height or not values:
        raise ValueError("OBS LightGBM predictions are empty or contain null probabilities")
    return DriftReference(
        source_sha=file_sha256(path), rows=len(values), bin_edges=DRIFT_BINS,
        proportions=_probability_histogram(values, DRIFT_BINS),
    )


def load_drift_reference(path: Path) -> DriftReference:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    reference = DriftReference(
        schema_version=int(payload.get("schema_version", 0)),
        source_sha=str(payload.get("source_sha", "")),
        rows=int(payload.get("rows", 0)),
        bin_edges=tuple(float(value) for value in payload.get("bin_edges", ())),
        proportions=tuple(float(value) for value in payload.get("proportions", ())),
    )
    if reference.schema_version != DRIFT_SCHEMA_VERSION:
        raise ValueError("unsupported drift reference schema")
    if len(reference.source_sha) != 64 or reference.rows < 1:
        raise ValueError("drift reference has invalid provenance")
    _probability_histogram([0.0], reference.bin_edges)  # validate edges
    if len(reference.proportions) != len(reference.bin_edges) - 1:
        raise ValueError("drift reference proportions do not match bins")
    if any(not math.isfinite(value) or value < 0 for value in reference.proportions):
        raise ValueError("drift reference contains invalid proportions")
    if not math.isclose(sum(reference.proportions), 1.0, abs_tol=1e-9):
        raise ValueError("drift reference proportions do not sum to one")
    return reference


def prediction_psi(probabilities: list[float], reference: DriftReference) -> float:
    """Population Stability Index for the full eligible-universe p_close."""
    current = _probability_histogram(probabilities, reference.bin_edges)
    epsilon = 1e-6
    return float(sum(
        (actual - expected) * math.log(actual / expected)
        for raw_actual, raw_expected in zip(current, reference.proportions, strict=True)
        for actual, expected in ((max(raw_actual, epsilon), max(raw_expected, epsilon)),)
    ))


def load_promoted_model(run: Path) -> PromotedModel:
    """Load only a model whose frozen research verdict explicitly passed."""
    run = Path(run)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    verdict = json.loads((run / "verdict.json").read_text(encoding="utf-8"))
    if verdict.get("promote_to_shadow") is not True:
        raise ValueError("research verdict does not permit shadow publication")
    feature_columns = tuple(manifest.get("feature_columns", ()))
    if feature_columns != FEATURE_COLUMNS:
        raise ValueError("research feature contract does not match runtime code")
    source_sha = str(manifest.get("git_sha", ""))
    research_data_sha = str(manifest.get("dataset", {}).get("sha256", ""))
    if len(source_sha) != 40 or len(research_data_sha) != 64:
        raise ValueError("research manifest is missing full source/data fingerprints")
    model_path = run / "models" / "last_walk_forward_lightgbm.joblib"
    try:
        import joblib
    except ImportError as exc:  # pragma: no cover - operational dependency
        raise RuntimeError("production ML runtime dependencies are required") from exc
    model = joblib.load(model_path)
    artifact_features = getattr(model, "feature_columns", None)
    if artifact_features is None:
        # Artifacts promoted before the configurable-feature refactor did not
        # pickle this attribute.  The signed manifest is the authoritative
        # feature contract, so restore only that exact, already-validated list.
        model.feature_columns = feature_columns
    elif tuple(artifact_features) != feature_columns:
        raise ValueError("model artifact feature contract does not match manifest")
    return PromotedModel(
        model=model,
        version=f"limit-up-lgbm-{source_sha[:8]}-{research_data_sha[:8]}",
        source_sha=source_sha,
        artifact_sha=file_sha256(model_path),
        research_data_sha=research_data_sha,
    )


def scoring_frame_sha(frame: pl.DataFrame) -> str:
    """Fingerprint the exact identities and features supplied to the model."""
    canonical = frame.select(
        "prediction_date", "target_session", "symbol", "board", "market_state",
        *FEATURE_COLUMNS,
    ).sort("symbol")
    payload = io.BytesIO()
    canonical.write_ipc(payload, compression="zstd")
    return hashlib.sha256(payload.getvalue()).hexdigest()


def score_snapshot(
    frame: pl.DataFrame, promoted: PromotedModel, *,
    names: dict[str, str] | None = None,
    generated_at: datetime | None = None,
    drift_reference: DriftReference | None = None,
) -> LimitUpShadowSnapshot:
    """Score a feature slice and retain only the compact Top-20 snapshot."""
    prediction_days = frame["prediction_date"].unique().to_list()
    target_days = frame["target_session"].unique().to_list()
    if len(prediction_days) != 1 or len(target_days) != 1:
        raise ValueError("scoring requires exactly one prediction and target session")
    p_touch, p_seal, p_close = promoted.model.predict(frame)
    scored: list[tuple[float, str, int]] = []
    for index, (touch, seal, close) in enumerate(zip(
        p_touch, p_seal, p_close, strict=True,
    )):
        values = (float(touch), float(seal), float(close))
        if not all(math.isfinite(value) and 0 <= value <= 1 for value in values):
            raise ValueError("model returned a non-finite or out-of-range probability")
        if abs(values[2] - values[0] * values[1]) > 1e-6:
            raise ValueError("model violated the two-stage probability identity")
        scored.append((values[2], str(frame["symbol"][index]), index))
    top = sorted(scored, key=lambda item: (-item[0], item[1]))[:20]
    entries = tuple(LimitUpShadowEntry(
        rank=rank,
        symbol=symbol,
        name=(names or {}).get(symbol, ""),
        board=str(frame["board"][index]),
        market_state=str(frame["market_state"][index]),
        p_touch=float(p_touch[index]),
        p_seal_given_touch=float(p_seal[index]),
        p_close_limit=float(p_close[index]),
    ) for rank, (_probability, symbol, index) in enumerate(top, 1))
    prediction_date = prediction_days[0]
    return LimitUpShadowSnapshot(
        prediction_date=prediction_date,
        target_session=target_days[0],
        generated_at=generated_at or datetime.now(SHANGHAI),
        status="ready",
        model_version=promoted.version,
        model_sha=promoted.artifact_sha,
        data_version=f"warehouse-{prediction_date:%Y%m%d}",
        data_sha=scoring_frame_sha(frame),
        eligible_count=frame.height,
        drift_score=(prediction_psi([item[0] for item in scored], drift_reference)
                     if drift_reference is not None else None),
        entries=entries,
    )


def build_daily_snapshot(
    warehouse: Path, run: Path, prediction_date: date, target_session: date, *,
    threads: int = 2, min_eligible: int = 3_000,
    drift_reference_path: Path | None = None,
) -> LimitUpShadowSnapshot:
    promoted = load_promoted_model(run)
    frame = scoring_frame(
        warehouse, prediction_date, target_session, threads=threads,
    )
    instruments = pl.read_parquet(Path(warehouse) / "instruments" / "part.parquet")
    if "as_of" in instruments.columns:
        as_of = instruments["as_of"].max()
        if as_of is None or as_of < prediction_date:
            raise ValueError("instrument snapshot is stale for prediction date")
    if frame.height < min_eligible:
        raise ValueError(
            f"eligible universe is unexpectedly small: {frame.height} < {min_eligible}"
        )
    names = dict(instruments.select("symbol", "name").iter_rows())
    reference = load_drift_reference(drift_reference_path) if drift_reference_path else None
    return score_snapshot(frame, promoted, names=names, drift_reference=reference)


def latest_feature_session(warehouse: Path) -> date:
    paths = list((Path(warehouse) / "market_regime").glob("year=*/part.parquet"))
    if not paths:
        raise FileNotFoundError("market_regime is unavailable")
    latest = pl.scan_parquet(paths).select(pl.col("dt").max()).collect().item()
    if latest is None:
        raise ValueError("market_regime has no sessions")
    return latest


def outcome_truth(
    warehouse: Path, target_session: date, symbols: tuple[str, ...],
) -> tuple[tuple[LimitUpShadowOutcome, ...], tuple[str, ...], str]:
    """Read T+1 truth without turning an unknown close state into ``False``.

    ``limit_list_d`` can permanently omit a touched symbol, leaving
    ``daily_flags.is_limit_up`` unknown even though OHLC and the price limit are
    present.  Known rows are still publishable; unresolved rows remain pending
    and are excluded from rolling metrics until their authoritative truth
    arrives.
    """
    root = Path(warehouse)
    flags_path = root / "daily_flags" / f"year={target_session.year}" / "part.parquet"
    bars_path = root / "tushare_daily" / f"year={target_session.year}" / "part.parquet"
    flags = pl.read_parquet(flags_path).filter(
        (pl.col("dt") == target_session) & pl.col("symbol").is_in(symbols)
    ).select("symbol", "touched_limit_up", "is_limit_up", "limit_up")
    bars = pl.read_parquet(bars_path).filter(
        (pl.col("dt") == target_session) & pl.col("symbol").is_in(symbols)
    ).select("symbol", "open", "high", "low")
    truth = flags.join(bars, on="symbol", how="inner").sort("symbol")
    if truth.height != len(symbols) or set(truth["symbol"]) != set(symbols):
        raise ValueError("target truth does not completely cover the published ranking")
    required = ("touched_limit_up", "is_limit_up", "limit_up", "open", "high", "low")
    unresolved_mask = pl.any_horizontal(pl.col(required).is_null())
    unresolved = tuple(truth.filter(unresolved_mask)["symbol"].to_list())
    resolved = truth.filter(~unresolved_mask)
    buffer = io.BytesIO()
    truth.write_ipc(buffer, compression="zstd")
    source_sha = hashlib.sha256(buffer.getvalue()).hexdigest()
    outcomes = tuple(LimitUpShadowOutcome(
        symbol=row["symbol"],
        actual_touched=bool(row["touched_limit_up"]),
        actual_closed_limit=bool(row["is_limit_up"]),
        opened_at_limit=float(row["open"]) >= float(row["limit_up"]) - 1e-6,
        one_price_limit=(
            float(row["low"]) >= float(row["limit_up"]) - 1e-6
            and float(row["high"]) <= float(row["limit_up"]) + 1e-6
        ),
    ) for row in resolved.iter_rows(named=True))
    return outcomes, unresolved, source_sha


def latest_truth_session(warehouse: Path) -> date:
    paths = list((Path(warehouse) / "daily_flags").glob("year=*/part.parquet"))
    if not paths:
        raise FileNotFoundError("daily_flags is unavailable")
    latest = pl.scan_parquet(paths).select(pl.col("dt").max()).collect().item()
    if latest is None:
        raise ValueError("daily_flags has no sessions")
    return latest
