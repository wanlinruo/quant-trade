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


# 分区读取一律带上：跨年分区可能由不同 schema 版本写下，
# 没有它 duckdb 会在「某年有这列、某年没有」时直接报错。
_UNION = "union_by_name=true"


def _read(glob: str) -> str:
    return f"read_parquet('{glob}', {_UNION})"


def _projection(con, glob: str, arrow_schema: pa.Schema) -> str:
    """按当前 schema 生成 SELECT 列表，文件里没有的列取 NULL。

    schema 加列后旧分区依然要能读：派生表虽可整体重算，但重算本身要先读
    旧分区来合并——读不了就成了死锁。列名也必须显式列出，否则 hive 分区
    目录 year=2024 会让 duckdb 多返回一列 year。
    """
    present = {
        row[0]
        for row in con.execute(f"DESCRIBE SELECT * FROM {_read(glob)}").fetchall()
    }
    parts = []
    for field in arrow_schema:
        if field.name in present:
            parts.append(field.name)
        else:
            parts.append(f"CAST(NULL AS {_DUCKDB_TYPES[str(field.type)]}) AS {field.name}")
    return ", ".join(parts)


_DUCKDB_TYPES = {
    "string": "VARCHAR",
    "date32[day]": "DATE",
    "double": "DOUBLE",
    "int64": "BIGINT",
    "bool": "BOOLEAN",
    "timestamp[us]": "TIMESTAMP",
}


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
            f"FROM {_read(glob)} WHERE dt BETWEEN ? AND ?"
        )
        params: list[object] = [start, end]
        if symbols is not None:
            placeholders = ", ".join("?" for _ in symbols)
            sql += f" AND symbol IN ({placeholders})"
            params.extend(symbols)
        sql += " ORDER BY symbol, dt"
        return self._con.execute(sql, params).pl()

    def index_bars(
        self,
        symbols: Sequence[str] | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> pl.DataFrame:
        if symbols is not None and len(symbols) == 0:
            return _empty(schema.INDEX_BARS)
        glob = self._glob("index_bars", partitioned=True)
        if glob is None:
            return _empty(schema.INDEX_BARS)

        columns = _projection(self._con, glob, schema.INDEX_BARS)
        sql = f"SELECT {columns} FROM {_read(glob)}"
        params: list[object] = []
        clauses: list[str] = []
        if start is not None and end is not None:
            clauses.append("dt BETWEEN ? AND ?")
            params.extend([start, end])
        if symbols is not None:
            placeholders = ", ".join("?" for _ in symbols)
            clauses.append(f"symbol IN ({placeholders})")
            params.extend(symbols)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return self._con.execute(sql + " ORDER BY symbol, dt", params).pl()

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

        columns = _projection(self._con, glob, schema.MINUTE_BARS)
        sql = f"SELECT {columns} FROM {_read(glob)} WHERE dt BETWEEN ? AND ?"
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
        columns = _projection(self._con, glob, schema.DAILY_FLAGS)
        sql = f"SELECT {columns} FROM {_read(glob)}"
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
        columns = _projection(self._con, glob, schema.MARKET_REGIME)
        sql = f"SELECT {columns} FROM {_read(glob)}"
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
        columns = _projection(self._con, glob, schema.VALUATION_SNAPSHOT)
        sql = f"SELECT {columns} FROM {_read(glob)}"
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

    def share_capital(self, symbols: Sequence[str] | None = None) -> pl.DataFrame:
        return self._read_unpartitioned("share_capital", schema.SHARE_CAPITAL, symbols)

    def instruments(self) -> pl.DataFrame:
        return self._read_unpartitioned("instruments", schema.INSTRUMENTS)

    def corporate_actions(self, symbols: Sequence[str] | None = None) -> pl.DataFrame:
        return self._read_unpartitioned(
            "corporate_actions", schema.CORPORATE_ACTIONS, symbols
        )

    def adj_factors(self, symbols: Sequence[str] | None = None) -> pl.DataFrame:
        return self._read_unpartitioned("adj_factors", schema.ADJ_FACTORS, symbols)
