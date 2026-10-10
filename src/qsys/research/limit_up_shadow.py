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
from qsys.web.limit_up_shadow import LimitUpShadowEntry, LimitUpShadowSnapshot

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class PromotedModel:
    model: Any
    version: str
    source_sha: str
    artifact_sha: str
    research_data_sha: str


def load_promoted_model(run: Path) -> PromotedModel:
    """Load only a model whose frozen research verdict explicitly passed."""
    run = Path(run)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    verdict = json.loads((run / "verdict.json").read_text(encoding="utf-8"))
    if verdict.get("promote_to_shadow") is not True:
        raise ValueError("research verdict does not permit shadow publication")
    if tuple(manifest.get("feature_columns", ())) != FEATURE_COLUMNS:
        raise ValueError("research feature contract does not match runtime code")
    source_sha = str(manifest.get("git_sha", ""))
    research_data_sha = str(manifest.get("dataset", {}).get("sha256", ""))
    if len(source_sha) != 40 or len(research_data_sha) != 64:
        raise ValueError("research manifest is missing full source/data fingerprints")
    model_path = run / "models" / "last_walk_forward_lightgbm.joblib"
    try:
        import joblib
    except ImportError as exc:  # pragma: no cover - operational dependency
        raise RuntimeError("ml-research dependency group is required") from exc
    return PromotedModel(
        model=joblib.load(model_path),
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
        entries=entries,
    )


def build_daily_snapshot(
    warehouse: Path, run: Path, prediction_date: date, target_session: date, *,
    threads: int = 2, min_eligible: int = 3_000,
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
    return score_snapshot(frame, promoted, names=names)


def latest_feature_session(warehouse: Path) -> date:
    paths = list((Path(warehouse) / "market_regime").glob("year=*/part.parquet"))
    if not paths:
        raise FileNotFoundError("market_regime is unavailable")
    latest = pl.scan_parquet(paths).select(pl.col("dt").max()).collect().item()
    if latest is None:
        raise ValueError("market_regime has no sessions")
    return latest
