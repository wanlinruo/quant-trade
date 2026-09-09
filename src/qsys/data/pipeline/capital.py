"""股本导入。

与分钟线导入同构：Windows 侧把 parquet 丢进目录，这里按目录导入，
传输方式不进代码。单个坏文件不中断整批。

股本是全市场约 15 万行的小表，不分区。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.pipeline.minutes import ImportResult, _free_name
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

_DATASET = "share_capital"
_COLUMNS = list(schema.SHARE_CAPITAL.names)


def import_share_capital(
    warehouse: Warehouse,
    incoming_dir: Path,
    *,
    archive_dir: Path | None = None,
) -> ImportResult:
    """导入目录下所有股本 parquet。返回统计。"""
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
            log.warning("capital.bad_file", path=str(path), error=str(exc))

    rows = 0
    if frames:
        fresh = pl.concat(frames, how="vertical")
        merged = (
            pl.concat(
                [WarehouseQuery(warehouse).share_capital(), fresh], how="vertical"
            )
            # 新行在后，keep="last" 让重采覆盖旧值
            .unique(subset=["symbol", "dt"], keep="last", maintain_order=True)
            .sort(["symbol", "dt"])
            .select(_COLUMNS)
        )
        warehouse.write(_DATASET, merged.to_arrow().cast(schema.SHARE_CAPITAL))
        rows = fresh.height

    if archive_dir is not None:
        archive = Path(archive_dir)
        archive.mkdir(parents=True, exist_ok=True)
        for path in good:
            shutil.move(str(path), str(_free_name(archive, path.name)))

    log.info("capital.imported", files=len(good), rows=rows, errors=errors)
    return ImportResult(len(good), rows, errors)
