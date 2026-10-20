"""数据覆盖查询。回答「我有哪些数据」。

Web 上要能一眼看到每张表有多少行、多少标的、覆盖到哪一天。
直接对 parquet 做聚合，不把数据读进内存。
"""

from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict
from contextlib import ExitStack
from datetime import date, datetime
from tempfile import TemporaryDirectory
from pathlib import Path
from threading import RLock

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
    measured_at: datetime | None = None


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


def _snapshot(warehouse: Warehouse) -> tuple:
    snapshot = []
    for dataset, arrow_schema in sorted(schema.ALL.items()):
        files = []
        for filename in _files(warehouse, dataset):
            path = Path(filename).resolve()
            info = path.stat()
            files.append((str(path), info.st_dev, info.st_ino, info.st_size,
                          info.st_mtime_ns, info.st_ctime_ns))
        if files:
            snapshot.append((dataset, tuple(arrow_schema.names), tuple(files),
                             str(warehouse.root.resolve())))
    return tuple(snapshot)


def coverage(warehouse: Warehouse) -> list[DatasetCoverage]:
    """精确统计已落盘的表；单表变化只使该表缓存失效。"""
    snapshot = _snapshot(warehouse)
    with _COVERAGE_LOCK:
        return list(_coverage_snapshot(snapshot))


def cached_coverage(warehouse: Warehouse) -> tuple[list[DatasetCoverage], frozenset[str]]:
    """只发现文件并读取匹配当前文件身份的缓存，不在请求线程扫描数据。"""
    ready = []
    pending = set()
    for key in _snapshot(warehouse):
        row = _cached(key)
        if row is None:
            pending.add(key[0])
        else:
            ready.append(row)
    return ready, frozenset(pending)


def previous_coverage(warehouse: Warehouse, datasets: frozenset[str]) -> list[DatasetCoverage]:
    """返回同一仓库最后一次真实统计；调用方必须标注时间及待刷新/失败状态。"""
    root = str(warehouse.root.resolve())
    found: dict[str, DatasetCoverage] = {}
    with _CACHE_LOCK:
        for key, row in reversed(_COVERAGE_RESULTS.items()):
            if key[3] == root and key[0] in datasets:
                prior = found.get(key[0])
                if prior is None or (row.measured_at or datetime.min) > (prior.measured_at or datetime.min):
                    found[key[0]] = row
    return list(found.values())


_COVERAGE_LOCK = RLock()
_CACHE_LOCK = RLock()
_COVERAGE_RESULTS: OrderedDict[tuple, DatasetCoverage] = OrderedDict()
_MAX_CACHED_DATASETS = 128


def _cached(key: tuple) -> DatasetCoverage | None:
    with _CACHE_LOCK:
        row = _COVERAGE_RESULTS.get(key)
        if row is not None:
            _COVERAGE_RESULTS.move_to_end(key)
        return row


def _unchanged(observed_files: tuple) -> bool:
    for filename, *expected in observed_files:
        try:
            info = Path(filename).stat()
        except FileNotFoundError:
            return False
        if [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns] != expected:
            return False
    return True


def _coverage_snapshot(snapshot: tuple) -> tuple[DatasetCoverage, ...]:
    out: list[DatasetCoverage] = []
    # 分钟表可能有数十亿行；只聚合所需列，限制内存、线程和临时落盘。
    with ExitStack() as stack:
        con = None
        for key in snapshot:
            row = _cached(key)
            if row is not None:
                out.append(row)
                continue
            dataset, names, observed_files, _ = key
            if con is None:
                temporary = stack.enter_context(TemporaryDirectory(prefix="qsys-coverage-"))
                con = stack.enter_context(duckdb.connect(config={
                    "memory_limit": "256MiB", "threads": "1",
                    "temp_directory": temporary,
                    "max_temp_directory_size": "512MiB",
                }))
            files = [item[0] for item in observed_files]
            time_column = "dt" if "dt" in names else "ts" if "ts" in names else None
            rows, symbols, first, last = _describe(con, files, time_column, "symbol" in names)
            row = DatasetCoverage(dataset, int(rows), int(symbols), first, last, datetime.now())
            # 查询期间发生原子替换时，不将结果记到旧文件身份下。
            if _unchanged(observed_files):
                with _CACHE_LOCK:
                    _COVERAGE_RESULTS[key] = row
                    _COVERAGE_RESULTS.move_to_end(key)
                    while len(_COVERAGE_RESULTS) > _MAX_CACHED_DATASETS:
                        _COVERAGE_RESULTS.popitem(last=False)
            out.append(row)
    return tuple(out)


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
