"""QMT 日线补采的导入。

与分钟线同一条规矩：Windows 侧把 parquet 丢进目录，这里从目录导入；
单个坏文件不中断整批；按年分区**合并**写入，绝不覆盖同年其余数据。

**只补不覆盖**：仓库里已有的那一天保持原样。mootdx 的数据经过跨源校验，
QMT 只用来填它漏掉的洞——两个源的同一天若都有，没有理由用后者盖前者。
`suspend_flag` 不入库，它只在补采时用来判断这个洞是不是真停牌。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.pipeline.bars import BAR_COLUMNS
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BackfillResult:
    files: int
    candidate_rows: int   # QMT 给出的行数
    filled_rows: int      # 真正补进仓库的（仓库原本没有的）
    suspended_rows: int   # QMT 标记停牌、被跳过的
    errors: int


def backfill_daily_bars(
    warehouse: Warehouse,
    incoming_dir: Path,
    *,
    archive_dir: Path | None = None,
    only: pl.DataFrame | None = None,
) -> BackfillResult:
    """导入目录下所有 parquet，**只填仓库里没有的股-日**。

    `only` 给定 (symbol, dt) 时，仅补这些——避免把整段历史重灌进来。
    """
    incoming = Path(incoming_dir)
    if not incoming.is_dir():
        return BackfillResult(0, 0, 0, 0, 0)

    frames: list[pl.DataFrame] = []
    good: list[Path] = []
    errors = 0
    for path in sorted(incoming.glob("*.parquet")):
        try:
            frames.append(pl.read_parquet(path))
            good.append(path)
        except Exception as exc:  # 单个坏文件不中断整批
            errors += 1
            log.warning("daily_backfill.bad_file", path=str(path), error=str(exc))

    if not frames:
        return BackfillResult(0, 0, 0, 0, errors)

    fresh = pl.concat(frames, how="vertical")
    candidates = fresh.height

    suspended = 0
    if "suspend_flag" in fresh.columns:
        before = fresh.height
        fresh = fresh.filter(pl.col("suspend_flag") == 0)
        suspended = before - fresh.height

    if only is not None:
        fresh = fresh.join(only.select(["symbol", "dt"]), on=["symbol", "dt"],
                           how="semi")

    query = WarehouseQuery(warehouse)
    filled = 0
    for year in sorted({d.year for d in fresh["dt"].to_list()}):
        lo, hi = date(year, 1, 1), date(year, 12, 31)
        incoming_year = fresh.filter(pl.col("dt").is_between(lo, hi)).select(
            BAR_COLUMNS
        )
        existing = query.daily_bars(None, lo, hi)
        # 只补洞：仓库已有的股-日原样保留
        new_rows = incoming_year.join(
            existing.select(["symbol", "dt"]), on=["symbol", "dt"], how="anti"
        )
        if new_rows.height == 0:
            continue
        merged = (
            pl.concat([existing, new_rows], how="vertical")
            .unique(subset=["symbol", "dt"], keep="first", maintain_order=True)
            .sort(["symbol", "dt"])
            .select(BAR_COLUMNS)
        )
        warehouse.write("bars_daily",
                        merged.to_arrow().cast(schema.BARS_DAILY), year=year)
        filled += new_rows.height

    if archive_dir is not None:
        archive = Path(archive_dir)
        archive.mkdir(parents=True, exist_ok=True)
        for path in good:
            shutil.move(str(path), str(_free_name(archive, path.name)))

    log.info("daily_backfill.done", files=len(good), candidates=candidates,
             filled=filled, suspended=suspended, errors=errors)
    return BackfillResult(len(good), candidates, filled, suspended, errors)


def _free_name(archive: Path, name: str) -> Path:
    target = archive / name
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for n in range(1, 10_000):
        candidate = archive / f"{stem}.{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"归档目录里 {name} 的重名太多")
