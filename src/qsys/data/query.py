"""DuckDB 查询层。只管 SQL，不懂 PIT 语义——PIT 由 feed.py 负责。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

import duckdb
import polars as pl
import pyarrow as pa

from qsys.data import schema
from qsys.data.storage import Warehouse


def _empty(arrow_schema: pa.Schema) -> pl.DataFrame:
    return pl.from_arrow(arrow_schema.empty_table())


class WarehouseQuery:
    def __init__(self, warehouse: Warehouse) -> None:
        self._wh = warehouse
        self._con = duckdb.connect()

    def _glob(self, dataset: str, partitioned: bool) -> str | None:
        base = self._wh.root / dataset
        pattern = "year=*/part.parquet" if partitioned else "part.parquet"
        if not list(base.glob(pattern)):
            return None
        return str(base / pattern)

    def daily_bars(
        self,
        symbols: Sequence[str] | None,
        start: date,
        end: date,
    ) -> pl.DataFrame:
        if symbols is not None and len(symbols) == 0:
            return _empty(schema.BARS_DAILY)
        glob = self._glob("bars_daily", partitioned=True)
        if glob is None:
            return _empty(schema.BARS_DAILY)

        sql = (
            "SELECT symbol, dt, open, high, low, close, volume, amount "
            f"FROM read_parquet('{glob}') WHERE dt BETWEEN ? AND ?"
        )
        params: list[object] = [start, end]
        if symbols is not None:
            placeholders = ", ".join("?" for _ in symbols)
            sql += f" AND symbol IN ({placeholders})"
            params.extend(symbols)
        sql += " ORDER BY symbol, dt"
        return self._con.execute(sql, params).pl()

    def minute_bars(
        self,
        symbols: Sequence[str] | None,
        start: datetime,
        end: datetime,
    ) -> pl.DataFrame:
        if symbols is not None and len(symbols) == 0:
            return _empty(schema.MINUTE_BARS)
        glob = self._glob("minute_bars", partitioned=True)
        if glob is None:
            return _empty(schema.MINUTE_BARS)

        columns = ", ".join(schema.MINUTE_BARS.names)
        sql = f"SELECT {columns} FROM read_parquet('{glob}') WHERE dt BETWEEN ? AND ?"
        params: list[object] = [start, end]
        if symbols is not None:
            placeholders = ", ".join("?" for _ in symbols)
            sql += f" AND symbol IN ({placeholders})"
            params.extend(symbols)
        return self._con.execute(sql + " ORDER BY symbol, dt", params).pl()

    def daily_flags(
        self, start: date | None = None, end: date | None = None
    ) -> pl.DataFrame:
        glob = self._glob("daily_flags", partitioned=True)
        if glob is None:
            return _empty(schema.DAILY_FLAGS)
        # 列名必须显式列出：分区目录名形如 year=2024，duckdb 会据此
        # 自动推断 hive 分区并多返回一列 year，SELECT * 拿到的宽度与
        # schema 不符，写回时 concat/cast 都会炸。
        columns = ", ".join(schema.DAILY_FLAGS.names)
        sql = f"SELECT {columns} FROM read_parquet('{glob}')"
        params: list[object] = []
        if start is not None and end is not None:
            sql += " WHERE dt BETWEEN ? AND ?"
            params = [start, end]
        return self._con.execute(sql + " ORDER BY dt, symbol", params).pl()

    def market_regime(
        self, start: date | None = None, end: date | None = None
    ) -> pl.DataFrame:
        glob = self._glob("market_regime", partitioned=True)
        if glob is None:
            return _empty(schema.MARKET_REGIME)
        # 列名必须显式列出：分区目录名形如 year=2024，duckdb 会据此
        # 自动推断 hive 分区并多返回一列 year，SELECT * 拿到的宽度与
        # schema 不符，写回时 concat/cast 都会炸。
        columns = ", ".join(schema.MARKET_REGIME.names)
        sql = f"SELECT {columns} FROM read_parquet('{glob}')"
        params: list[object] = []
        if start is not None and end is not None:
            sql += " WHERE dt BETWEEN ? AND ?"
            params = [start, end]
        return self._con.execute(sql + " ORDER BY dt", params).pl()

    def valuation(
        self, start: date | None = None, end: date | None = None
    ) -> pl.DataFrame:
        glob = self._glob("valuation_snapshot", partitioned=True)
        if glob is None:
            return _empty(schema.VALUATION_SNAPSHOT)
        # 列名必须显式列出：分区目录名形如 year=2024，duckdb 会据此
        # 自动推断 hive 分区并多返回一列 year，SELECT * 拿到的宽度与
        # schema 不符，写回时 concat/cast 都会炸。
        columns = ", ".join(schema.VALUATION_SNAPSHOT.names)
        sql = f"SELECT {columns} FROM read_parquet('{glob}')"
        params: list[object] = []
        if start is not None and end is not None:
            sql += " WHERE dt BETWEEN ? AND ?"
            params = [start, end]
        return self._con.execute(sql + " ORDER BY dt, symbol", params).pl()

    def _read_unpartitioned(
        self,
        dataset: str,
        arrow_schema: pa.Schema,
        symbols: Sequence[str] | None = None,
    ) -> pl.DataFrame:
        if symbols is not None and len(symbols) == 0:
            return _empty(arrow_schema)
        glob = self._glob(dataset, partitioned=False)
        if glob is None:
            return _empty(arrow_schema)
        sql = f"SELECT * FROM read_parquet('{glob}')"
        params: list[object] = []
        if symbols is not None:
            placeholders = ", ".join("?" for _ in symbols)
            sql += f" WHERE symbol IN ({placeholders})"
            params.extend(symbols)
        return self._con.execute(sql, params).pl()

    def instruments(self) -> pl.DataFrame:
        return self._read_unpartitioned("instruments", schema.INSTRUMENTS)

    def corporate_actions(self, symbols: Sequence[str] | None = None) -> pl.DataFrame:
        return self._read_unpartitioned(
            "corporate_actions", schema.CORPORATE_ACTIONS, symbols
        )

    def adj_factors(self, symbols: Sequence[str] | None = None) -> pl.DataFrame:
        return self._read_unpartitioned("adj_factors", schema.ADJ_FACTORS, symbols)
