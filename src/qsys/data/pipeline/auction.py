"""集合竞价 tick 导入。

**存原始快照，不存派生结论。** 原策略的 `get_first_auction_record` 取的是
`get_call_auction` 的第 1 条；将来买点前移要用的是 09:20 之后的某一条。
两种口径都从同一份原始数据算得出来——先归成结论就都算不出来了。

**为什么必须每天跑**：QMT 的 tick 只回溯约 20 个交易日（实测边界
2026-08-10，锚点 2026-09-08），比分钟线的滚动一年紧得多。分钟线漏一天
丢一天，tick 漏三周就全丢。

单个坏文件不中断整批——与分钟线同一条规矩。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

_DATASET = "auction_ticks"
_COLUMNS = list(schema.AUCTION_TICKS.names)


@dataclass(frozen=True, slots=True)
class ImportResult:
    files: int
    rows: int
    errors: int


def import_auction_ticks(
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
            # 跨机传过来的 parquet 可能是别的时间精度/整数宽度。
            # 统一 cast 成仓库 schema，不合就当坏文件处理。
            frames.append(
                pl.from_arrow(frame.select(_COLUMNS).to_arrow().cast(
                    schema.AUCTION_TICKS
                ))
            )
            good.append(path)
        except Exception as exc:  # 单个坏文件不中断整批
            errors += 1
            log.warning("auction.bad_file", path=str(path), error=str(exc))

    rows = 0
    if frames:
        rows = _write_by_year(warehouse, pl.concat(frames, how="vertical"))

    if archive_dir is not None:
        archive = Path(archive_dir)
        archive.mkdir(parents=True, exist_ok=True)
        for path in good:
            shutil.move(str(path), str(_free_name(archive, path.name)))

    log.info("auction.imported", files=len(good), rows=rows, errors=errors)
    return ImportResult(len(good), rows, errors)


def _free_name(archive: Path, name: str) -> Path:
    """归档时避开重名：Windows 侧每天从 00000 重新编号。"""
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
    """按年分区合并写。**合并**——部分重算不能抹掉同年其余数据。"""
    query = WarehouseQuery(warehouse)
    written = 0
    for year in sorted({d.year for d in fresh["dt"].to_list()}):
        lo, hi = date(year, 1, 1), date(year, 12, 31)
        incoming = fresh.filter(pl.col("dt").is_between(lo, hi))
        merged = (
            pl.concat([query.auction_ticks(None, lo, hi), incoming], how="vertical")
            # 同一 (标的, 时刻) 重复导入以最后一条为准
            .unique(subset=["symbol", "ts"], keep="last", maintain_order=True)
            .sort(["symbol", "ts"])
            .select(_COLUMNS)
        )
        warehouse.write(
            _DATASET, merged.to_arrow().cast(schema.AUCTION_TICKS), year=year
        )
        written += incoming.height
    return written


def missing_auction_days(
    warehouse: Warehouse, wanted: pl.DataFrame
) -> pl.DataFrame:
    """`wanted`（symbol, dt）里仓库还没有竞价 tick 的那些。"""
    query = WarehouseQuery(warehouse)
    if wanted.height == 0:
        return wanted
    lo, hi = min(wanted["dt"].to_list()), max(wanted["dt"].to_list())
    have = (
        query.auction_ticks(None, lo, hi)
        .select(["symbol", "dt"])
        .unique()
    )
    return wanted.join(have, on=["symbol", "dt"], how="anti")


def first_auction_record(
    query: WarehouseQuery, day: date, symbols: list[str]
) -> dict[str, dict]:
    """复现原策略 `get_first_auction_record`：竞价段**第一条有效快照**。

    「有效」= 已经有虚拟开盘价。09:15:00 那笔常常整行是 0（集合竞价刚开始，
    还没有委托可撮合），聚宽的 `get_call_auction` 不会把它当第一条。
    """
    frame = query.auction_ticks(symbols, day, day)
    if frame.height == 0:
        return {}
    valid = frame.filter(pl.col("bid1") > 0).sort("ts")
    out: dict[str, dict] = {}
    for row in valid.group_by("symbol", maintain_order=True).first().iter_rows(
        named=True
    ):
        out[row["symbol"]] = {
            "ts": row["ts"],
            "current": row["bid1"],       # 虚拟开盘价
            "volume": row["bid_vol1"],    # 匹配量（手）
        }
    return out


def auction_at(
    query: WarehouseQuery, day: date, symbols: list[str], moment: datetime
) -> dict[str, dict]:
    """`moment` 时刻（含）之前的**最后一条**快照——买点前移要用的就是它。

    09:20 之后委托不可撤，那之后的虚拟开盘价才是可交易的信号。
    """
    frame = query.auction_ticks(symbols, day, day)
    if frame.height == 0:
        return {}
    upto = frame.filter((pl.col("bid1") > 0) & (pl.col("ts") <= moment)).sort("ts")
    out: dict[str, dict] = {}
    for row in upto.group_by("symbol", maintain_order=True).last().iter_rows(
        named=True
    ):
        out[row["symbol"]] = {
            "ts": row["ts"],
            "current": row["bid1"],
            "volume": row["bid_vol1"],
        }
    return out
