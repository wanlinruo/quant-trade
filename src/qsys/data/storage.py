"""Parquet 分区仓库的字节层。只管文件进出，不懂业务语义。"""

from __future__ import annotations

import os
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
