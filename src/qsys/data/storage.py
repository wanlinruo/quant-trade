"""Parquet 分区仓库的字节层。只管文件进出，不懂业务语义。"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from qsys.data import schema


class Warehouse:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, dataset: str, year: int | None = None) -> Path:
        if dataset not in schema.ALL:
            raise KeyError(f"unknown dataset: {dataset!r}")
        base = self.root / dataset
        if year is not None:
            base = base / f"year={year}"
        return base / "part.parquet"

    def day_path(self, dataset: str, day: date) -> Path:
        if dataset not in schema.ALL:
            raise KeyError(f"unknown dataset: {dataset!r}")
        return self.root / dataset / f"year={day.year}" / f"date={day:%Y%m%d}" / "part.parquet"

    def write_day(self, dataset: str, table: pa.Table, *, day: date) -> Path:
        """原子覆盖写单日分区，并拒绝混入其他日期。"""
        expected = schema.ALL.get(dataset)
        if expected is None:
            raise KeyError(f"unknown dataset: {dataset!r}")
        if not table.schema.equals(expected):
            raise ValueError(f"schema mismatch for {dataset!r}")
        if table.num_rows:
            import pyarrow.compute as pc
            partition_column = "dt" if "dt" in table.column_names else "ts"
            days = set(pc.unique(pc.cast(table.column(partition_column), pa.date32())).to_pylist())
            if days != {day}:
                raise ValueError(f"{dataset} 分区 {day} 混入其他交易日")
        path = self.day_path(dataset, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp)
        os.replace(tmp, path)
        return path

    def write(self, dataset: str, table: pa.Table, *, year: int | None = None) -> Path:
        """原子幂等覆盖写：同一 (dataset, year) 重复写入结果一致。"""
        expected = schema.ALL.get(dataset)
        if expected is None:
            raise KeyError(f"unknown dataset: {dataset!r}")
        if not table.schema.equals(expected):
            raise ValueError(f"schema mismatch for {dataset!r}")

        path = self.path_for(dataset, year)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp)
        os.replace(tmp, path)
        return path
