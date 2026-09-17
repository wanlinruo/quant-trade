"""分钟线导入。

Windows 侧把 parquet 丢进一个目录，这里从目录导入。**传输方式不进代码**
——rsync 也好、共享盘也好，换传输不该改导入逻辑。

单个坏文件不中断整批：跨机传输中断出半个文件是常态，一个坏文件让整批
白导才是问题。坏文件也不归档，留在原地才有得查。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

_DATASET = "minute_bars"
_COLUMNS = list(schema.MINUTE_BARS.names)


@dataclass(frozen=True, slots=True)
class ImportResult:
    files: int
    rows: int
    errors: int


def import_minute_bars(
    warehouse: Warehouse,
    incoming_dir: Path,
    *,
    archive_dir: Path | None = None,
) -> ImportResult:
    """导入目录下所有 parquet。返回统计。"""
    incoming = Path(incoming_dir)
    if not incoming.is_dir():
        return ImportResult(0, 0, 0)

    frames: list[pl.DataFrame] = []
    good: list[Path] = []
    errors = 0

    for path in sorted(incoming.glob("*.parquet")):
        try:
            frame = pl.read_parquet(path)
            missing = set(_COLUMNS) - set(frame.columns)
            if missing:
                raise ValueError(f"missing columns: {sorted(missing)}")
            frames.append(frame.select(_COLUMNS))
            good.append(path)
        except Exception as exc:  # 单个坏文件不中断整批
            errors += 1
            log.warning("minutes.bad_file", path=str(path), error=str(exc))

    rows = 0
    if frames:
        rows = _write_by_year(warehouse, pl.concat(frames, how="vertical"))

    if archive_dir is not None:
        archive = Path(archive_dir)
        archive.mkdir(parents=True, exist_ok=True)
        for path in good:
            shutil.move(str(path), str(_free_name(archive, path.name)))

    log.info("minutes.imported", files=len(good), rows=rows, errors=errors)
    return ImportResult(len(good), rows, errors)


def _free_name(archive: Path, name: str) -> Path:
    """归档时避开重名。

    Windows 侧每天在新目录里从 minutes_00000.parquet 重新编号，直接按原名
    存会让后一天覆盖前一天的归档记录。
    """
    target = archive / name
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for n in range(1, 10_000):
        candidate = archive / f"{stem}.{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"归档目录里 {name} 的重名太多")


def _write_by_year(warehouse: Warehouse, fresh: pl.DataFrame) -> int:
    """按交易日合并写（D-20）。名字保留以免调用方改动；只重写涉及的那几天。"""
    written = 0
    fresh = fresh.with_columns(pl.col("dt").dt.date().alias("_day"))
    for day in sorted(set(fresh["_day"].to_list())):
        incoming = fresh.filter(pl.col("_day") == day).drop("_day")
        existing = _read_day(warehouse, day)
        merged = (
            pl.concat([existing, incoming], how="vertical")
            # 重复导入覆盖旧值；maintain_order 让「保留最后一条」有确定语义
            .unique(subset=["symbol", "dt"], keep="last", maintain_order=True)
            .sort(["symbol", "dt"])
            .select(_COLUMNS)
        )
        warehouse.write_day(_DATASET, merged.to_arrow().cast(schema.MINUTE_BARS), day=day)
        written += incoming.height
    return written


def _read_day(warehouse: Warehouse, day: date) -> pl.DataFrame:
    """当日已有的行：日分区优先，否则从尚未拆分的整年文件里取这一天。"""
    path = warehouse.day_path(_DATASET, day)
    if path.exists():
        return pl.read_parquet(path).select(_COLUMNS)
    return WarehouseQuery(warehouse).minute_bars(
        None, datetime.combine(day, time(0, 0)), datetime.combine(day, time(23, 59, 59))
    ).select(_COLUMNS)


def split_minute_year(warehouse: Warehouse, year: int, *, backup_dir: Path) -> dict[str, int]:
    """把旧的整年文件拆成交易日分区，逐日核对后把原文件移入备份目录（不删除）。"""
    legacy = warehouse.path_for(_DATASET, year)
    if not legacy.exists():
        return {"year": year, "days": 0, "rows": 0}
    frame = pl.read_parquet(legacy).select(_COLUMNS).with_columns(pl.col("dt").dt.date().alias("_day"))
    days = sorted(set(frame["_day"].to_list()))
    for day in days:
        chunk = frame.filter(pl.col("_day") == day).drop("_day").sort(["symbol", "dt"])
        warehouse.write_day(_DATASET, chunk.to_arrow().cast(schema.MINUTE_BARS), day=day)
        back = pl.read_parquet(warehouse.day_path(_DATASET, day)).select(_COLUMNS)
        if not back.equals(chunk):
            raise RuntimeError(f"minute_bars {day} 拆分后回读不一致，保留原整年文件")
    backup = Path(backup_dir)
    backup.mkdir(parents=True, exist_ok=True)
    os.replace(legacy, backup / f"{_DATASET}_year={year}.parquet")
    log.info("minutes.year_split", year=year, days=len(days), rows=frame.height)
    return {"year": year, "days": len(days), "rows": frame.height}


def missing_stock_days(warehouse: Warehouse, candidates: pl.DataFrame) -> pl.DataFrame:
    """候选里还没有分钟线的那些股-日。

    每日增量只该下缺的那部分：候选窗口会与前几天重叠，全量重下既浪费
    时间又白占 QMT 的本地缓存。
    """
    if candidates.height == 0:
        return candidates

    days = candidates["dt"].to_list()
    owned = WarehouseQuery(warehouse).minute_stock_days(min(days), max(days))
    if owned.height == 0:
        return candidates
    return candidates.join(owned, on=["symbol", "dt"], how="anti").sort(["dt", "symbol"])
