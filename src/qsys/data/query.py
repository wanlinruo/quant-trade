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

# daily_bars() 与 legacy_daily_bars() 共用的 8 列投影——
# 两者列名相同，差别只在读哪张表、volume 是不是带小数。
_DAILY_BAR_COLUMNS = "symbol, dt, open, high, low, close, volume, amount"


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

    def _dated_query(
        self,
        dataset: str,
        columns: str,
        empty_schema: pa.Schema,
        symbols: Sequence[str] | None,
        start: date,
        end: date,
    ) -> pl.DataFrame:
        """按 (symbols, 日期区间) 取一张分区表，三个日线读取方法共用。

        三处分头拼 SQL 的话，将来改一处忘一处，同一份数据会从不同入口
        返回不同的列序或排序——`dataset`/`columns`/`empty_schema` 三个参数
        分别对应「读哪张表、投影哪些列、空表用哪个 schema」，这是三个
        公开方法之间唯一的差异。
        """
        if symbols is not None and len(symbols) == 0:
            return _empty(empty_schema)
        glob = self._glob(dataset, partitioned=True)
        if glob is None:
            return _empty(empty_schema)

        sql = f"SELECT {columns} FROM {_read(glob)} WHERE dt BETWEEN ? AND ?"
        params: list[object] = [start, end]
        if symbols is not None:
            placeholders = ", ".join("?" for _ in symbols)
            sql += f" AND symbol IN ({placeholders})"
            params.extend(symbols)
        sql += " ORDER BY symbol, dt"
        return self._con.execute(sql, params).pl()

    def daily_bars(
        self,
        symbols: Sequence[str] | None,
        start: date,
        end: date,
    ) -> pl.DataFrame:
        """生产日线。**读 Tushare 直连表 `tushare_daily`**，投影 8 列。

        2026-09-13 由 ``paid_daily`` 原子换底，依据是 Tushare 换源对账报告。
        两者逐字段同源，直连消除了人工转存中断。``volume`` 保持 Float64。

        **不回退旧表。** Tushare 没有的日期就是没有，回退会让调用方在不知情的
        情况下拿到另一个源的值。
        """
        return self._dated_query(
            "tushare_daily", _DAILY_BAR_COLUMNS, schema.DAILY_BARS, symbols, start, end
        )

    def paid_daily(
        self,
        symbols: Sequence[str] | None,
        start: date,
        end: date,
        *,
        columns: Sequence[str] | None = None,
    ) -> pl.DataFrame:
        """付费源日线；可投影列以控制全历史任务的内存占用。"""
        names = list(columns) if columns is not None else schema.PAID_DAILY.names
        unknown = set(names) - set(schema.PAID_DAILY.names)
        if unknown:
            raise ValueError(f"unknown paid_daily columns: {sorted(unknown)}")
        selected_schema = pa.schema([schema.PAID_DAILY.field(name) for name in names])
        return self._dated_query(
            "paid_daily", ", ".join(names), selected_schema, symbols, start, end
        )

    def tushare_daily(
        self,
        symbols: Sequence[str] | None,
        start: date,
        end: date,
        *,
        columns: Sequence[str] | None = None,
    ) -> pl.DataFrame:
        """Tushare 直连日线；可投影列以控制全历史任务内存。"""
        names = list(columns) if columns is not None else schema.TUSHARE_DAILY.names
        unknown = set(names) - set(schema.TUSHARE_DAILY.names)
        if unknown:
            raise ValueError(f"unknown tushare_daily columns: {sorted(unknown)}")
        selected_schema = pa.schema([schema.TUSHARE_DAILY.field(name) for name in names])
        return self._dated_query(
            "tushare_daily", ", ".join(names), selected_schema, symbols, start, end
        )

    def legacy_paid_daily(
        self,
        symbols: Sequence[str] | None,
        start: date,
        end: date,
        *,
        columns: Sequence[str] | None = None,
    ) -> pl.DataFrame:
        """冻结的百度网盘日线，仅供换源审计与对账。"""
        return self.paid_daily(symbols, start, end, columns=columns)

    def legacy_daily_bars(
        self,
        symbols: Sequence[str] | None,
        start: date,
        end: date,
    ) -> pl.DataFrame:
        """冻结的 mootdx/QMT 旧日线表。**唯一用途是换源回归对账。**

        不要在生产路径上调用它。表已不再更新（mootdx 上游失效），
        保留只为给这次换源提供逐键比对的参照物。
        """
        return self._dated_query(
            "bars_daily", _DAILY_BAR_COLUMNS, schema.BARS_DAILY, symbols, start, end
        )

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
        return self._con.execute(sql + " ORDER BY symbol, dt", params).pl().with_columns(
            pl.col("volume").cast(pl.Float64)
        )

    def price_limits(
        self, symbols: Sequence[str] | None, start: date, end: date
    ) -> pl.DataFrame:
        """每日涨跌停价格真值（Tushare ``stk_limit``）。"""
        return self._dated_query(
            "price_limits_daily", ", ".join(schema.PRICE_LIMITS_DAILY.names),
            schema.PRICE_LIMITS_DAILY, symbols, start, end,
        )

    def suspensions(
        self, symbols: Sequence[str] | None, start: date, end: date
    ) -> pl.DataFrame:
        """每日停复牌真值（Tushare ``suspend_d``）。"""
        return self._dated_query(
            "suspend_daily", ", ".join(schema.SUSPEND_DAILY.names),
            schema.SUSPEND_DAILY, symbols, start, end,
        )

    def limit_list(
        self, symbols: Sequence[str] | None, start: date, end: date
    ) -> pl.DataFrame:
        """涨跌停列表（Tushare ``limit_list_d``；自 2020 年起）。"""
        return self._dated_query(
            "limit_list_daily", ", ".join(schema.LIMIT_LIST_DAILY.names),
            schema.LIMIT_LIST_DAILY, symbols, start, end,
        )

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

    def auction_ticks(
        self,
        symbols: Sequence[str] | None,
        start: date,
        end: date,
    ) -> pl.DataFrame:
        """集合竞价快照。`dt` 是交易日，`ts` 是快照时刻（北京时间）。"""
        if symbols is not None and len(symbols) == 0:
            return _empty(schema.AUCTION_TICKS)
        glob = self._glob("auction_ticks", partitioned=True)
        if glob is None:
            return _empty(schema.AUCTION_TICKS)

        columns = _projection(self._con, glob, schema.AUCTION_TICKS)
        sql = f"SELECT {columns} FROM {_read(glob)} WHERE dt BETWEEN ? AND ?"
        params: list[object] = [start, end]
        if symbols is not None:
            placeholders = ", ".join("?" for _ in symbols)
            sql += f" AND symbol IN ({placeholders})"
            params.extend(symbols)
        return self._con.execute(sql + " ORDER BY symbol, ts", params).pl()

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

    def listing_dates(self) -> dict[str, date]:
        """每只标的在库内的首个 bar 日期。

        策略的 `filter_new_stock(days=50)` 只需区分「上市不足 50 天」——
        近 50 天上市的标的其首根日线必在库内，故首个 bar 日期足以替代
        真正的上市日（免费源不提供 list_date，实测 94% 为空）。

        **库内最早日期本身不可信**：那天有 bar 的标的可能上市很久了，
        也可能恰好那天上市，区分不了。调用方按「不新」处理即可——
        入场判定另需 60 根历史，前 60 个交易日本就选不出票。

        **读 `tushare_daily`，不是两张冻结旧表。** 与 `daily_bars()` 同源
        （见其文档字符串）：`bars_daily` 已冻结不再更新，若还从那张表算
        「首个 bar 日期」，换源之后新上市的标的永远查不到自己的上市日。
        而 `candidate_pool` 的 `keep()` 是「不知道上市日就不放行」，
        于是这些标的会被**永久排除**在候选池之外——哪怕早已上市超过 50 天。
        是静默少选，不是多选。
        """
        glob = self._glob("tushare_daily", partitioned=True)
        if glob is None:
            return {}
        rows = self._con.execute(
            f"SELECT symbol, min(dt) FROM {_read(glob)} GROUP BY symbol"
        ).fetchall()
        return {symbol: first for symbol, first in rows}

    def instruments(self) -> pl.DataFrame:
        return self._read_unpartitioned("instruments", schema.INSTRUMENTS)

    def metric_checks(
        self, metric_id: str | None = None, check_id: str | None = None
    ) -> pl.DataFrame:
        """自动验证结果；空仓库也返回带完整 schema 的空表。"""
        glob = self._glob("metric_checks", partitioned=True)
        if glob is None:
            return _empty(schema.METRIC_CHECKS)
        columns = _projection(self._con, glob, schema.METRIC_CHECKS)
        sql = f"SELECT {columns} FROM {_read(glob)}"
        clauses: list[str] = []
        params: list[object] = []
        if metric_id is not None:
            clauses.append("metric_id = ?")
            params.append(metric_id)
        if check_id is not None:
            clauses.append("check_id = ?")
            params.append(check_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return self._con.execute(sql + " ORDER BY checked_at DESC", params).pl()

    def latest_metric_checks(self) -> pl.DataFrame:
        """每个 (metric_id, check_id) 最新的一次结果。"""
        frame = self.metric_checks()
        if frame.height == 0:
            return frame
        return frame.sort("checked_at", descending=True).unique(
            subset=["metric_id", "check_id"], keep="first", maintain_order=True
        )

    def metric_evidence(self, metric_id: str | None = None) -> pl.DataFrame:
        """人工或第三方抽样证据；这张表与自动结果物理隔离。"""
        glob = self._glob("metric_evidence", partitioned=True)
        if glob is None:
            return _empty(schema.METRIC_EVIDENCE)
        columns = _projection(self._con, glob, schema.METRIC_EVIDENCE)
        sql = f"SELECT {columns} FROM {_read(glob)}"
        params: list[object] = []
        if metric_id is not None:
            sql += " WHERE metric_id = ?"
            params.append(metric_id)
        return self._con.execute(sql + " ORDER BY dt DESC, symbol", params).pl()

    def name_snapshots(
        self, symbols: Sequence[str] | None, start: date, end: date
    ) -> pl.DataFrame:
        """每日 stock_basic 名称快照。`dt` 是观测日，不是生效日。"""
        return self._dated_query(
            "name_snapshots", ", ".join(schema.NAME_SNAPSHOTS.names),
            schema.NAME_SNAPSHOTS, symbols, start, end,
        )

    def name_history(self, symbols: Sequence[str] | None = None) -> pl.DataFrame:
        """曾用名区间表；ST 状态由区间覆盖关系派生。"""
        frame = self._read_unpartitioned("name_history", schema.NAME_HISTORY, symbols)
        return frame.sort(["symbol", "start_date"])

    def corporate_actions(self, symbols: Sequence[str] | None = None) -> pl.DataFrame:
        return self._read_unpartitioned(
            "corporate_actions", schema.CORPORATE_ACTIONS, symbols
        )

    def adj_factors(self, symbols: Sequence[str] | None = None) -> pl.DataFrame:
        return self._read_unpartitioned("adj_factors", schema.ADJ_FACTORS, symbols)
