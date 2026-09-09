"""数据覆盖查询。回答「我有哪些数据」。

Web 上要能一眼看到每张表有多少行、多少标的、覆盖到哪一天。
直接对 parquet 做聚合，不把数据读进内存。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb

from qsys.data import schema
from qsys.data.storage import Warehouse


@dataclass(frozen=True, slots=True)
class DatasetCoverage:
    dataset: str
    rows: int
    symbols: int
    first_dt: date | None
    last_dt: date | None


def _pattern(dataset: str) -> str:
    return "year=*/part.parquet" if dataset in schema.PARTITIONED else "part.parquet"


def _describe(
    con,
    glob: str,
    has_dt: bool,
    has_symbol: bool,
    where: str = "",
    params: list | None = None,
) -> tuple:
    parts = ["count(*)"]
    parts.append("count(distinct symbol)" if has_symbol else "0")
    parts.append("min(dt)" if has_dt else "NULL")
    parts.append("max(dt)" if has_dt else "NULL")
    sql = f"SELECT {', '.join(parts)} FROM read_parquet('{glob}') {where}"
    return con.execute(sql, params or []).fetchone()


def coverage(warehouse: Warehouse) -> list[DatasetCoverage]:
    """每张已落盘的表一行。从未写过的表不出现。"""
    con = duckdb.connect()
    out: list[DatasetCoverage] = []
    for dataset, arrow_schema in sorted(schema.ALL.items()):
        pattern = _pattern(dataset)
        base = warehouse.root / dataset
        if not list(base.glob(pattern)):
            continue
        names = set(arrow_schema.names)
        rows, symbols, first, last = _describe(
            con, str(base / pattern), "dt" in names, "symbol" in names
        )
        out.append(
            DatasetCoverage(dataset, int(rows or 0), int(symbols or 0), first, last)
        )
    return out


def symbol_coverage(warehouse: Warehouse, symbol: str) -> DatasetCoverage | None:
    """单只标的的日线覆盖。没有数据返回 None。"""
    base = warehouse.root / "bars_daily"
    if not list(base.glob("year=*/part.parquet")):
        return None
    con = duckdb.connect()
    rows, symbols, first, last = _describe(
        con,
        str(base / "year=*/part.parquet"),
        True,
        True,
        where="WHERE symbol = ?",
        params=[symbol],
    )
    if not rows:
        return None
    return DatasetCoverage("bars_daily", int(rows), int(symbols), first, last)
