"""阶段 7 收盘发布：门禁通过后原子替换规范日线与分钟分区。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Mapping, Sequence

import polars as pl
import pyarrow.parquet as pq

from qsys.data import schema
from qsys.data.pipeline.canonical_reference import reconcile_frames
from qsys.data.query import WarehouseQuery
from qsys.data.realtime import RealtimeDailyBar, RealtimeMinuteBar
from qsys.data.storage import Warehouse


@dataclass(frozen=True, slots=True)
class ClosePublishResult:
    status: str
    day: date
    daily_rows: int
    minute_rows: int
    manifest: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")
    os.replace(temp, path)


def _frames(
    day: date,
    daily: Sequence[RealtimeDailyBar],
    minutes: Sequence[RealtimeMinuteBar],
) -> tuple[pl.DataFrame, pl.DataFrame]:
    daily_rows = [
        {
            "symbol": row.symbol, "dt": row.dt, "pre_close": row.prev_close,
            "open": row.open, "high": row.high, "low": row.low, "close": row.close,
            "volume": row.volume, "amount": row.amount,
        }
        for row in daily
    ]
    minute_rows = [
        {
            "symbol": row.symbol, "ts": row.ts, "open": row.open,
            "high": row.high, "low": row.low, "close": row.close,
            "volume": row.volume, "amount": row.amount, "source": row.source,
            "visible_at": row.visible_at,
        }
        for row in minutes
    ]
    daily_frame = (
        pl.DataFrame(daily_rows).select(schema.BAR_DAILY.names).sort("symbol")
        if daily_rows else pl.from_arrow(schema.BAR_DAILY.empty_table())
    )
    minute_frame = (
        pl.DataFrame(minute_rows).select(schema.BAR_MINUTE_1M.names).sort(["symbol", "ts"])
        if minute_rows else pl.from_arrow(schema.BAR_MINUTE_1M.empty_table())
    )
    return daily_frame, minute_frame


def _gate(
    day: date,
    published_at: datetime,
    daily: Sequence[RealtimeDailyBar],
    minutes: Sequence[RealtimeMinuteBar],
    expected_minutes: Mapping[str, int],
) -> tuple[pl.DataFrame, pl.DataFrame]:
    if published_at < datetime.combine(day, time(15, 1)):
        raise RuntimeError("收盘发布早于 15:01")
    if not daily:
        raise RuntimeError("实时日线候选为空")
    if any(row.dt != day or row.as_of.date() != day for row in daily):
        raise RuntimeError("实时日线候选跨日")
    if len({row.symbol for row in daily}) != len(daily):
        raise RuntimeError("实时日线候选含重复 symbol")
    if any(not row.finalized or row.partial for row in minutes):
        raise RuntimeError("分钟候选包含未定稿或不完整 bar")
    if any(row.ts.date() != day or row.visible_at < row.ts for row in minutes):
        raise RuntimeError("分钟候选日期或 visible_at 不合法")
    daily_frame, minute_frame = _frames(day, daily, minutes)
    if minute_frame.height != minute_frame.n_unique(subset=["symbol", "ts"]):
        raise RuntimeError("分钟候选含重复 (symbol, ts)")
    daily_symbols = set(daily_frame["symbol"].to_list())
    if set(expected_minutes) != daily_symbols:
        raise RuntimeError("expected_minutes 必须精确覆盖实时日线标的")
    counts = {symbol: 0 for symbol in expected_minutes}
    if minute_frame.height:
        for symbol, count in minute_frame.group_by("symbol").len().iter_rows():
            if symbol not in counts:
                raise RuntimeError(f"分钟候选含日线范围外标的：{symbol}")
            counts[symbol] = count
    bad_counts = {
        symbol: (counts[symbol], expected)
        for symbol, expected in expected_minutes.items()
        if counts[symbol] != expected
    }
    if bad_counts:
        raise RuntimeError(f"分钟根数门禁失败：{bad_counts}")
    if minute_frame.height:
        aggregates = minute_frame.group_by("symbol").agg(
            pl.col("open").first(), pl.col("high").max(), pl.col("low").min(),
            pl.col("close").last(), pl.col("volume").sum(), pl.col("amount").sum(),
        )
        compared = daily_frame.join(aggregates, on="symbol", suffix="_minute")
        mismatch = compared.filter(
            ((pl.col("open") - pl.col("open_minute")).abs() > 0.005)
            | ((pl.col("high") - pl.col("high_minute")).abs() > 0.005)
            | ((pl.col("low") - pl.col("low_minute")).abs() > 0.005)
            | ((pl.col("close") - pl.col("close_minute")).abs() > 0.005)
            | ((pl.col("volume") - pl.col("volume_minute")).abs() > 0.5)
            | ((pl.col("amount") - pl.col("amount_minute")).abs() > 100.0)
        )
        if mismatch.height:
            raise RuntimeError(f"分钟聚合与实时日线有 {mismatch.height} 个标的不一致")
    return daily_frame, minute_frame


def _already_published(
    warehouse: Warehouse, day: date, daily: pl.DataFrame, minutes: pl.DataFrame
) -> bool:
    query = WarehouseQuery(warehouse)
    existing_daily = query.bar_daily(None, day, day)
    lo, hi = datetime.combine(day, time.min), datetime.combine(day, time.max)
    existing_minutes = query.bar_minute_1m(None, lo, hi)
    return (
        reconcile_frames(daily, existing_daily, ("symbol", "dt")).matches
        and reconcile_frames(minutes, existing_minutes, ("symbol", "ts")).matches
    )


def _publish(
    warehouse: Warehouse,
    day: date,
    published_at: datetime,
    daily: pl.DataFrame,
    minutes: pl.DataFrame,
) -> ClosePublishResult:
    run_id = published_at.strftime("%Y%m%dT%H%M%S%f")
    stage = warehouse.root / ".realtime_close_staging" / f"{day:%Y%m%d}-{run_id}"
    stage_daily = stage / "bar_daily.parquet"
    stage_minute_dir = stage / "bar_minute_1m"
    stage_minute = stage_minute_dir / "part.parquet"
    stage.mkdir(parents=True, exist_ok=False)
    stage_minute_dir.mkdir()

    query = WarehouseQuery(warehouse)
    year_start, year_end = date(day.year, 1, 1), date(day.year, 12, 31)
    outside = query.bar_daily(None, year_start, year_end).filter(pl.col("dt") != day)
    merged_daily = pl.concat([outside, daily], how="vertical_relaxed").sort(["symbol", "dt"])
    pq.write_table(merged_daily.to_arrow().cast(schema.BAR_DAILY), stage_daily)
    pq.write_table(minutes.to_arrow().cast(schema.BAR_MINUTE_1M), stage_minute)

    manifest_data = {
        "status": "PUBLISHED", "day": str(day), "published_at": published_at,
        "daily_rows": daily.height, "minute_rows": minutes.height,
        "files": {
            "bar_daily": {"rows": merged_daily.height, "sha256": _sha256(stage_daily)},
            "bar_minute_1m": {"rows": minutes.height, "sha256": _sha256(stage_minute)},
        },
    }
    stage_manifest = stage_minute_dir / "manifest.json"
    _atomic_json(stage_manifest, manifest_data)
    if pq.read_schema(stage_daily) != schema.BAR_DAILY or pq.read_schema(stage_minute) != schema.BAR_MINUTE_1M:
        raise RuntimeError("暂存分区 Schema 校验失败")
    if _sha256(stage_daily) != manifest_data["files"]["bar_daily"]["sha256"] \
            or _sha256(stage_minute) != manifest_data["files"]["bar_minute_1m"]["sha256"]:
        raise RuntimeError("暂存分区 Manifest 哈希校验失败")

    daily_target = warehouse.path_for("bar_daily", day.year)
    minute_target = warehouse.day_path("bar_minute_1m", day).parent
    daily_target.parent.mkdir(parents=True, exist_ok=True)
    minute_target.parent.mkdir(parents=True, exist_ok=True)
    backup_daily, backup_minute = stage / "old-bar-daily.parquet", stage / "old-bar-minute"
    had_daily, had_minute = daily_target.exists(), minute_target.exists()
    try:
        if had_daily:
            os.replace(daily_target, backup_daily)
        os.replace(stage_daily, daily_target)
        if had_minute:
            os.replace(minute_target, backup_minute)
        os.replace(stage_minute_dir, minute_target)
    except Exception:
        if daily_target.exists():
            daily_target.unlink()
        if had_daily and backup_daily.exists():
            os.replace(backup_daily, daily_target)
        if minute_target.exists():
            shutil.rmtree(minute_target)
        if had_minute and backup_minute.exists():
            os.replace(backup_minute, minute_target)
        raise
    if backup_daily.exists():
        backup_daily.unlink()
    if backup_minute.exists():
        shutil.rmtree(backup_minute)
    shutil.rmtree(stage)
    return ClosePublishResult("published", day, daily.height, minutes.height, minute_target / "manifest.json")


def publish_realtime_close(
    warehouse: Warehouse,
    day: date,
    *,
    published_at: datetime,
    daily: Sequence[RealtimeDailyBar],
    minutes: Sequence[RealtimeMinuteBar],
    expected_minutes: Mapping[str, int],
) -> ClosePublishResult:
    """门禁、暂存、校验并发布；失败证据独立保存且不覆盖历史目标。"""
    try:
        daily_frame, minute_frame = _gate(
            day, published_at, daily, minutes, expected_minutes
        )
        manifest = warehouse.day_path("bar_minute_1m", day).parent / "manifest.json"
        if _already_published(warehouse, day, daily_frame, minute_frame):
            return ClosePublishResult(
                "already_published", day, daily_frame.height, minute_frame.height, manifest
            )
        return _publish(warehouse, day, published_at, daily_frame, minute_frame)
    except Exception as exc:
        evidence = warehouse.root / "realtime_publish_failures" / f"date={day:%Y%m%d}" \
            / f"{published_at:%Y%m%dT%H%M%S%f}.json"
        _atomic_json(evidence, {
            "status": "FAILED", "day": day, "published_at": published_at,
            "error_type": type(exc).__name__, "error": str(exc),
            "daily_candidates": len(daily), "minute_candidates": len(minutes),
            "expected_minutes": dict(expected_minutes),
        })
        raise
