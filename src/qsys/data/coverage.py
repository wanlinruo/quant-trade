"""数据覆盖查询。回答「我有哪些数据」。

Web 上要能一眼看到每张表有多少行、多少标的、覆盖到哪一天。
直接对 parquet 做聚合，不把数据读进内存。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from tempfile import TemporaryDirectory

import duckdb

from qsys.data import schema
from qsys.data.storage import Warehouse


@dataclass(frozen=True, slots=True)
class DatasetCoverage:
    dataset: str
    rows: int
    symbols: int
    first_dt: date | datetime | None
    last_dt: date | datetime | None


def _files(warehouse: Warehouse, dataset: str) -> list[str]:
    base = warehouse.root / dataset
    if dataset not in schema.PARTITIONED:
        path = base / "part.parquet"
        return [str(path)] if path.exists() else []
    files = []
    for year in sorted(base.glob("year=*")):
        # 与生产查询层一致：某年已有日分区时，不再重复统计旧年度副本。
        days = sorted(year.glob("date=*/part.parquet"))
        if days:
            files.extend(str(path) for path in days)
        elif (year / "part.parquet").exists():
            files.append(str(year / "part.parquet"))
    return files


def _describe(
    con,
    files: str | list[str],
    time_column: str | None,
    has_symbol: bool,
    where: str = "",
    params: list | None = None,
) -> tuple:
    parts = ["count(*)"]
    parts.append("count(distinct symbol)" if has_symbol else "0")
    parts.append(f"min({time_column})" if time_column else "NULL")
    parts.append(f"max({time_column})" if time_column else "NULL")
    sql = (f"SELECT {', '.join(parts)} FROM "
           f"read_parquet(?, union_by_name=true, hive_partitioning=false) {where}")
    return con.execute(sql, [files, *(params or [])]).fetchone()


def coverage(warehouse: Warehouse) -> list[DatasetCoverage]:
    """每张已落盘的表一行。从未写过的表不出现。"""
    out: list[DatasetCoverage] = []
    # 分钟表可能有数十亿行；只聚合所需列，限制内存、线程和临时落盘。
    with TemporaryDirectory(prefix="qsys-coverage-") as temporary:
        with duckdb.connect(config={
            "memory_limit": "256MiB", "threads": "1",
            "temp_directory": temporary,
            "max_temp_directory_size": "512MiB",
        }) as con:
            for dataset, arrow_schema in sorted(schema.ALL.items()):
                files = _files(warehouse, dataset)
                if not files:
                    continue
                names = set(arrow_schema.names)
                time_column = "dt" if "dt" in names else "ts" if "ts" in names else None
                rows, symbols, first, last = _describe(
                    con, files, time_column, "symbol" in names
                )
                out.append(DatasetCoverage(dataset, int(rows), int(symbols),
                                           first, last))
    return out


def symbol_coverage(warehouse: Warehouse, symbol: str) -> DatasetCoverage | None:
    """单只标的的日线覆盖。没有数据返回 None。"""
    base = warehouse.root / "bars_daily"
    if not list(base.glob("year=*/part.parquet")):
        return None
    with duckdb.connect() as con:
        rows, symbols, first, last = _describe(
            con,
            str(base / "year=*/part.parquet"),
            "dt",
            True,
            where="WHERE symbol = ?",
            params=[symbol],
        )
    if not rows:
        return None
    return DatasetCoverage("bars_daily", int(rows), int(symbols), first, last)
