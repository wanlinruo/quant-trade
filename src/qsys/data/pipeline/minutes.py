"""分钟线导入。

Windows 侧把 parquet 丢进一个目录，这里从目录导入。**传输方式不进代码**
——rsync 也好、共享盘也好，换传输不该改导入逻辑。

单个坏文件不中断整批：跨机传输中断出半个文件是常态，一个坏文件让整批
白导才是问题。坏文件也不归档，留在原地才有得查。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
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
            shutil.move(str(path), str(archive / path.name))

    log.info("minutes.imported", files=len(good), rows=rows, errors=errors)
    return ImportResult(len(good), rows, errors)


def _write_by_year(warehouse: Warehouse, fresh: pl.DataFrame) -> int:
    query = WarehouseQuery(warehouse)
    written = 0
    for year in sorted({t.year for t in fresh["dt"].to_list()}):
        lo = datetime(year, 1, 1)
        hi = datetime(year, 12, 31, 23, 59, 59)
        incoming = fresh.filter(pl.col("dt").is_between(lo, hi))
        merged = (
            pl.concat([query.minute_bars(None, lo, hi), incoming], how="vertical")
            # 重复导入覆盖旧值；maintain_order 让「保留最后一条」有确定语义
            .unique(subset=["symbol", "dt"], keep="last", maintain_order=True)
            .sort(["symbol", "dt"])
            .select(_COLUMNS)
        )
        warehouse.write(_DATASET, merged.to_arrow().cast(schema.MINUTE_BARS), year=year)
        written += incoming.height
    return written
