"""DuckDB 查询层。只管 SQL，不懂 PIT 语义——PIT 由 feed.py 负责。"""

from __future__ import annotations

from collections.abc import Sequence
import json
from datetime import date, datetime, time, timedelta

import duckdb
import polars as pl
import pyarrow as pa

from qsys.data import schema
from qsys.data.storage import Warehouse

PAID_MINUTE_VISIBILITY_LAG = timedelta(minutes=1)


class CanonicalMinuteCoverageError(RuntimeError):
    """请求包含交易日，但规范分钟分区尚未发布。"""


def _empty(arrow_schema: pa.Schema) -> pl.DataFrame:
    return pl.from_arrow(arrow_schema.empty_table())


# 分区读取一律带上：跨年分区可能由不同 schema 版本写下，
# 没有它 duckdb 会在「某年有这列、某年没有」时直接报错。
_UNION = "union_by_name=true"


def _read(glob: str | Sequence[str]) -> str:
    if isinstance(glob, str):
        return f"read_parquet('{glob}', {_UNION})"
    files = ", ".join(f"'{path}'" for path in glob)
    return f"read_parquet([{files}], {_UNION})"


def _projection(con, glob: str | Sequence[str], arrow_schema: pa.Schema) -> str:
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

    def dataset_bounds(
        self, dataset: str, column: str = "dt", *, partitioned: bool = True
    ) -> tuple[object, object] | None:
        """只读元数据聚合，不把整张大表搬进 Python。"""
        table_schema = schema.ALL.get(dataset)
        if table_schema is None or column not in table_schema.names:
            raise ValueError(f"unknown dataset column: {dataset}.{column}")
        glob = (self._minute_files(None, None) or None) if dataset == "minute_bars" \
            else self._glob(dataset, partitioned)
        if glob is None:
            return None
        row = self._con.execute(
            f"SELECT min({column}), max({column}) FROM {_read(glob)}"
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return row[0], row[1]

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

    def bar_daily(
        self, symbols: Sequence[str] | None, start: date, end: date, *,
        columns: Sequence[str] | None = None,
    ) -> pl.DataFrame:
        """阶段 4 规范日线窄表；不回退 ``tushare_daily``。"""
        names = list(columns) if columns is not None else schema.BAR_DAILY.names
        unknown = set(names) - set(schema.BAR_DAILY.names)
        if unknown:
            raise ValueError(f"unknown bar_daily columns: {sorted(unknown)}")
        selected_schema = pa.schema([schema.BAR_DAILY.field(name) for name in names])
        return self._dated_query(
            "bar_daily", ", ".join(names), selected_schema,
            symbols, start, end,
        )

    def historical_daily_bars(
        self, symbols: Sequence[str] | None, start: date, end: date, *,
        columns: Sequence[str] | None = None,
    ) -> pl.DataFrame:
        """阶段 8 稳定历史日线门面；只读 ``bar_daily``，禁止来源回退。"""
        return self.bar_daily(symbols, start, end, columns=columns)

    def valuation_daily(
        self, symbols: Sequence[str] | None, start: date, end: date
    ) -> pl.DataFrame:
        """阶段 4 规范估值窄表。"""
        return self._dated_query(
            "valuation_daily", ", ".join(schema.VALUATION_DAILY.names),
            schema.VALUATION_DAILY, symbols, start, end,
        )

    def adjustment_factor_daily(
        self, symbols: Sequence[str] | None, start: date, end: date
    ) -> pl.DataFrame:
        """阶段 4 规范价格/股本累计因子。"""
        return self._dated_query(
            "adjustment_factor_daily",
            ", ".join(schema.ADJUSTMENT_FACTOR_DAILY.names),
            schema.ADJUSTMENT_FACTOR_DAILY, symbols, start, end,
        )

    def price_limit_daily(
        self, symbols: Sequence[str] | None, start: date, end: date
    ) -> pl.DataFrame:
        """阶段 5 规范涨跌停价；不回退旧来源。"""
        return self._dated_query(
            "price_limit_daily", ", ".join(schema.PRICE_LIMIT_DAILY.names),
            schema.PRICE_LIMIT_DAILY, symbols, start, end,
        )

    def security_status_daily(
        self, symbols: Sequence[str] | None, start: date, end: date
    ) -> pl.DataFrame:
        """阶段 5 规范证券日状态；未知状态保持 null。"""
        return self._dated_query(
            "security_status_daily",
            ", ".join(schema.SECURITY_STATUS_DAILY.names),
            schema.SECURITY_STATUS_DAILY, symbols, start, end,
        )

    def market_flags_daily(
        self, symbols: Sequence[str] | None, start: date, end: date
    ) -> pl.DataFrame:
        """阶段 5 规范市场标志；生产 ``daily_flags`` 尚未切换。"""
        return self._dated_query(
            "market_flags_daily", ", ".join(schema.MARKET_FLAGS_DAILY.names),
            schema.MARKET_FLAGS_DAILY, symbols, start, end,
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

    def stock_st(
        self, symbols: Sequence[str] | None, start: date, end: date
    ) -> pl.DataFrame:
        """每日 ST 真值；每个有日线的股票日都有显式 True/False。"""
        return self._dated_query(
            "stock_st_daily", ", ".join(schema.STOCK_ST_DAILY.names),
            schema.STOCK_ST_DAILY, symbols, start, end,
        )

    def limit_list(
        self, symbols: Sequence[str] | None, start: date, end: date
    ) -> pl.DataFrame:
        """涨跌停列表（Tushare ``limit_list_d``；自 2020 年起）。"""
        return self._dated_query(
            "limit_list_daily", ", ".join(schema.LIMIT_LIST_DAILY.names),
            schema.LIMIT_LIST_DAILY, symbols, start, end,
        )

    def _minute_files(self, start: date | None, end: date | None) -> list[str]:
        """QMT 分钟线：日分区优先，尚未迁移的年份兼容旧整年文件。"""
        base = self._wh.root / "minute_bars"
        files: list[str] = []
        for year_dir in sorted(base.glob("year=*")):
            try:
                year = int(year_dir.name.split("=", 1)[1])
            except ValueError:
                continue
            if (start is not None and year < start.year) or (end is not None and year > end.year):
                continue
            days = sorted(year_dir.glob("date=*/part.parquet"))
            if days:
                for path in days:
                    day = datetime.strptime(path.parent.name.split("=", 1)[1], "%Y%m%d").date()
                    if (start is None or day >= start) and (end is None or day <= end):
                        files.append(str(path))
            elif (year_dir / "part.parquet").exists():
                files.append(str(year_dir / "part.parquet"))
        return files

    def _minute_days(self, dataset: str, start: date, end: date) -> set[date]:
        base = self._wh.root / dataset
        covered: set[date] = set()
        for year in range(start.year, end.year + 1):
            year_dir = base / f"year={year}"
            for path in year_dir.glob("date=*/part.parquet"):
                day = datetime.strptime(path.parent.name.split("=", 1)[1], "%Y%m%d").date()
                if start <= day <= end:
                    covered.add(day)
            manifest = year_dir / "manifest.json"
            if dataset == "paid_minute_bars" and manifest.exists() \
                    and any(year_dir.glob("shard=*/part.parquet")):
                meta = json.loads(manifest.read_text())
                lo = datetime.fromisoformat(str(meta["start"])).date()
                hi = datetime.fromisoformat(str(meta["end"])).date()
                covered.update(
                    d for d in (lo + timedelta(days=i) for i in range((hi - lo).days + 1))
                    if start <= d <= end
                )
        return covered

    def paid_minute_days(self, start: date, end: date) -> set[date]:
        return self._minute_days("paid_minute_bars", start, end)

    def joinquant_minute_days(self, start: date, end: date) -> set[date]:
        return self._minute_days("joinquant_minute_bars", start, end)

    def tushare_minute_days(self, start: date, end: date) -> set[date]:
        return self._minute_days("tushare_minute_bars", start, end)

    def intraday_bounds(self) -> tuple[date, date] | None:
        """检查点可用分钟线的最早与最晚交易日（显式来源并集）。"""
        days: list[date] = []
        for dataset, reader in (
            ("paid_minute_bars", self.paid_minute_days),
            ("tushare_minute_bars", self.tushare_minute_days),
            ("joinquant_minute_bars", self.joinquant_minute_days),
        ):
            base = self._wh.root / dataset
            years = sorted(
                int(path.name.split("=", 1)[1]) for path in base.glob("year=*")
            ) if base.exists() else []
            if years:
                covered = reader(date(years[0], 1, 1), date(years[-1], 12, 31))
                if covered:
                    days.extend((min(covered), max(covered)))
        qmt = self.dataset_bounds("minute_bars")
        if qmt is not None:
            days.extend(v.date() if isinstance(v, datetime) else v for v in qmt)
        return (min(days), max(days)) if days else None

    def intraday_bars(self, symbols: Sequence[str], day: date) -> pl.DataFrame:
        """检查点统一分钟事实：显式覆盖优先，最后才读 QMT。

        同一标的日不混源；成交量统一为手，并保留来源与 PIT 可见时刻。
        """
        columns = {
            "symbol": pl.Utf8, "dt": pl.Datetime("us"),
            "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
            "close": pl.Float64, "volume": pl.Float64, "amount": pl.Float64,
            "source": pl.Utf8,
            "visible_at": pl.Datetime("us"),
        }
        if not symbols:
            return pl.DataFrame(schema=columns)
        lo = datetime.combine(day, time(0, 0))
        hi = datetime.combine(day, time(23, 59, 59))
        frames: list[pl.DataFrame] = []
        remaining = list(symbols)

        if day in self.paid_minute_days(day, day):
            paid = self.paid_minute_bars(remaining, lo, hi)
            if paid.height:
                frames.append(paid.select(
                    "symbol", "dt", "open", "high", "low", "close",
                    (pl.col("volume") / 100).alias("volume"), "amount",
                    pl.lit("paid").alias("source"),
                    (pl.col("dt") + PAID_MINUTE_VISIBILITY_LAG).alias("visible_at"),
                ).cast(columns))
                covered = set(paid["symbol"].to_list())
                remaining = [symbol for symbol in remaining if symbol not in covered]

        for dataset, source, day_reader in (
            ("tushare_minute_bars", "tushare", self.tushare_minute_days),
            ("joinquant_minute_bars", "joinquant", self.joinquant_minute_days),
        ):
            if not remaining or day not in day_reader(day, day):
                continue
            path = self._wh.root / dataset / f"year={day.year}" \
                / f"date={day:%Y%m%d}" / "part.parquet"
            frame = pl.read_parquet(path).filter(pl.col("symbol").is_in(remaining))
            if frame.height:
                frames.append(frame.select(
                    "symbol", "dt", "open", "high", "low", "close",
                    (pl.col("volume") / 100).alias("volume"), "amount",
                    pl.lit(source).alias("source"), pl.col("dt").alias("visible_at"),
                ).cast(columns))
                covered = set(frame["symbol"].to_list())
                remaining = [symbol for symbol in remaining if symbol not in covered]

        qmt = self.minute_bars(remaining, lo, hi).select(
            "symbol", "dt", "open", "high", "low", "close",
            pl.col("volume").cast(pl.Float64), "amount",
            pl.lit("qmt").alias("source"), pl.col("dt").alias("visible_at"),
        ).cast(columns)
        frames.append(qmt)
        return pl.concat(frames, how="vertical").sort(["symbol", "dt"])

    def bar_minute_1m(
        self, symbols: Sequence[str] | None, start: datetime, end: datetime, *,
        columns: Sequence[str] | None = None,
    ) -> pl.DataFrame:
        """阶段 6 规范分钟线；只读新表，不回退或混合旧来源。"""
        names = list(columns) if columns is not None else schema.BAR_MINUTE_1M.names
        unknown = set(names) - set(schema.BAR_MINUTE_1M.names)
        if unknown:
            raise ValueError(f"unknown bar_minute_1m columns: {sorted(unknown)}")
        selected_schema = pa.schema([
            schema.BAR_MINUTE_1M.field(name) for name in names
        ])
        if symbols is not None and len(symbols) == 0:
            return _empty(selected_schema)
        files: list[str] = []
        base = self._wh.root / "bar_minute_1m"
        for year in range(start.year, end.year + 1):
            for path in sorted((base / f"year={year}").glob("date=*/part.parquet")):
                day = datetime.strptime(path.parent.name.split("=", 1)[1], "%Y%m%d").date()
                if start.date() <= day <= end.date():
                    files.append(str(path))
        if not files:
            return _empty(selected_schema)
        projection = _projection(self._con, files, selected_schema)
        sql = f"SELECT {projection} FROM {_read(files)} WHERE ts BETWEEN ? AND ?"
        params: list[object] = [start, end]
        if symbols is not None:
            placeholders = ", ".join("?" for _ in symbols)
            sql += f" AND symbol IN ({placeholders})"
            params.extend(symbols)
        return self._con.execute(sql + " ORDER BY symbol, ts", params).pl()

    def historical_minute_bars(
        self, symbols: Sequence[str] | None, start: datetime, end: datetime, *,
        columns: Sequence[str] | None = None,
    ) -> pl.DataFrame:
        """阶段 8 稳定历史分钟门面；只读 ``bar_minute_1m``。

        规范门面已经是生产默认读取路径。某个交易日没有分区时必须显式失败，
        不能把「尚未发布」伪装成「当天没有行情」。低层 ``bar_minute_1m``
        仍保留空表语义，供回填器和诊断代码检查未发布状态。
        """
        sessions = self.trading_calendar(
            start.date(), end.date(), open_only=True
        )
        if sessions.height:
            expected = set(sessions["dt"].to_list())
            base = self._wh.root / "bar_minute_1m"
            published = {
                datetime.strptime(path.parent.name.split("=", 1)[1], "%Y%m%d").date()
                for year in range(start.year, end.year + 1)
                for path in (base / f"year={year}").glob("date=*/part.parquet")
            }
            missing = sorted(expected - published)
            if missing:
                preview = ", ".join(day.isoformat() for day in missing[:10])
                suffix = f" 等 {len(missing)} 日" if len(missing) > 10 else ""
                raise CanonicalMinuteCoverageError(
                    f"规范分钟线尚未发布：{preview}{suffix}"
                )
        return self.bar_minute_1m(symbols, start, end, columns=columns)

    def minute_bars(
        self,
        symbols: Sequence[str] | None,
        start: datetime,
        end: datetime,
    ) -> pl.DataFrame:
        if symbols is not None and len(symbols) == 0:
            return _empty(schema.MINUTE_BARS)
        glob = self._minute_files(start.date(), end.date())
        if not glob:
            return _empty(schema.MINUTE_BARS)

        columns = _projection(self._con, glob, schema.MINUTE_BARS)
        sql = f"SELECT {columns} FROM {_read(glob)} WHERE dt BETWEEN ? AND ?"
        params: list[object] = [start, end]
        if symbols is not None:
            placeholders = ", ".join("?" for _ in symbols)
            sql += f" AND symbol IN ({placeholders})"
            params.extend(symbols)
        return self._con.execute(sql + " ORDER BY symbol, dt", params).pl()

    def paid_minute_bars(
        self,
        symbols: Sequence[str] | None,
        start: datetime,
        end: datetime,
    ) -> pl.DataFrame:
        """查询已发布的只读付费分钟事实，不与 QMT 分钟线静默混合。"""
        if symbols is not None and len(symbols) == 0:
            return _empty(schema.PAID_MINUTE_BARS)
        base = self._wh.root / "paid_minute_bars"
        files: list[str] = []
        for year in range(start.year, end.year + 1):
            year_dir = base / f"year={year}"
            files.extend(str(path) for path in sorted(year_dir.glob("shard=*/part.parquet")))
            files.extend(str(path) for path in sorted(year_dir.glob("date=*/part.parquet")))
        if not files:
            return _empty(schema.PAID_MINUTE_BARS)
        columns = _projection(self._con, files, schema.PAID_MINUTE_BARS)
        sql = f"SELECT {columns} FROM {_read(files)} WHERE dt BETWEEN ? AND ?"
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

    def market_regime_daily(
        self, start: date | None = None, end: date | None = None
    ) -> pl.DataFrame:
        """阶段 5 规范市场生态；生产 ``market_regime`` 尚未切换。"""
        glob = self._glob("market_regime_daily", partitioned=True)
        if glob is None:
            return _empty(schema.MARKET_REGIME_DAILY)
        columns = _projection(self._con, glob, schema.MARKET_REGIME_DAILY)
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

    def share_capital_history(
        self, symbols: Sequence[str] | None = None
    ) -> pl.DataFrame:
        frame = self._read_unpartitioned(
            "share_capital_history", schema.SHARE_CAPITAL_HISTORY, symbols
        )
        return frame.sort(["symbol", "effective_date"])

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

    def security_master(self) -> pl.DataFrame:
        """规范证券主数据；阶段 3 并行表，不回退旧 ``instruments``。"""
        return self._read_unpartitioned(
            "security_master", schema.SECURITY_MASTER
        ).sort("symbol")

    def security_name_history(
        self, symbols: Sequence[str] | None = None
    ) -> pl.DataFrame:
        """规范名称有效区间；不以名称点观测填补历史。"""
        return self._read_unpartitioned(
            "security_name_history", schema.SECURITY_NAME_HISTORY, symbols
        ).sort(["symbol", "start_date", "name"])

    def trading_calendar(
        self,
        start: date,
        end: date,
        *,
        market: str = "SSE",
        open_only: bool = False,
    ) -> pl.DataFrame:
        """规范交易日历；只返回已落库来源事实，不推算工作日。"""
        glob = self._glob("trading_calendar", partitioned=True)
        if glob is None:
            return _empty(schema.TRADING_CALENDAR)
        columns = _projection(self._con, glob, schema.TRADING_CALENDAR)
        sql = (
            f"SELECT {columns} FROM {_read(glob)} "
            "WHERE market = ? AND dt BETWEEN ? AND ?"
        )
        params: list[object] = [market, start, end]
        if open_only:
            sql += " AND is_open"
        return self._con.execute(sql + " ORDER BY dt", params).pl()

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

    def dataset_checks(
        self, dataset_id: str | None = None, check_id: str | None = None
    ) -> pl.DataFrame:
        """表级健康证据；与字段级 ``metric_checks`` 物理隔离。"""
        glob = self._glob("dataset_checks", partitioned=True)
        if glob is None:
            return _empty(schema.DATASET_CHECKS)
        columns = _projection(self._con, glob, schema.DATASET_CHECKS)
        sql = f"SELECT {columns} FROM {_read(glob)}"
        clauses: list[str] = []
        params: list[object] = []
        if dataset_id is not None:
            clauses.append("dataset_id = ?")
            params.append(dataset_id)
        if check_id is not None:
            clauses.append("check_id = ?")
            params.append(check_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return self._con.execute(sql + " ORDER BY checked_at DESC", params).pl()

    def relation_checks(
        self, upstream: str | None = None, downstream: str | None = None
    ) -> pl.DataFrame:
        """跨表连接、基数、孤儿行和刷新顺序证据。"""
        glob = self._glob("relation_checks", partitioned=True)
        if glob is None:
            return _empty(schema.RELATION_CHECKS)
        columns = _projection(self._con, glob, schema.RELATION_CHECKS)
        sql = f"SELECT {columns} FROM {_read(glob)}"
        clauses: list[str] = []
        params: list[object] = []
        if upstream is not None:
            clauses.append("upstream = ?")
            params.append(upstream)
        if downstream is not None:
            clauses.append("downstream = ?")
            params.append(downstream)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return self._con.execute(sql + " ORDER BY checked_at DESC", params).pl()

    def name_snapshots(
        self, symbols: Sequence[str] | None, start: date, end: date
    ) -> pl.DataFrame:
        """每日 stock_basic 名称快照。`dt` 是观测日，不是生效日。"""
        return self._dated_query(
            "name_snapshots", ", ".join(schema.NAME_SNAPSHOTS.names),
            schema.NAME_SNAPSHOTS, symbols, start, end,
        )

    def delisting_pool(self, start: date, end: date) -> pl.DataFrame:
        """每日退市整理期池（派生自 namechange，见 pipeline.delisting）。"""
        return self._dated_query(
            "delisting_pool", ", ".join(schema.DELISTING_POOL.names),
            schema.DELISTING_POOL, None, start, end,
        )

    def name_history(self, symbols: Sequence[str] | None = None) -> pl.DataFrame:
        """曾用名区间表；ST 状态由区间覆盖关系派生。"""
        frame = self._read_unpartitioned("name_history", schema.NAME_HISTORY, symbols)
        return frame.sort(["symbol", "start_date"])

    def delisting_pool_coverage(self, year: int) -> tuple[date, date] | None:
        """该年池子文件实际构建过的交易日区间（写入时记在 Parquet 元数据里）。

        池子只存「在整理期的标的」，某天 0 行既可能是当天确实没有、也可能是没建。
        所以覆盖范围单独记录，不能拿行的最小最大日期代替。
        """
        path = self._wh.path_for("delisting_pool", year)
        if not path.exists():
            return None
        import pyarrow.parquet as pq

        meta = pq.read_schema(path).metadata or {}
        if b"built_from" not in meta or b"built_through" not in meta:
            return None
        return (date.fromisoformat(meta[b"built_from"].decode()),
                date.fromisoformat(meta[b"built_through"].decode()))

    def delisting_symbols(self, day: date, *, announced_by: date) -> set[str]:
        """`day` 当天处于退市整理期、且在 `announced_by` 及以前已公告的标的。

        **唯一出处是退市整理期池** `delisting_pool`（派生自 Tushare namechange 的
        `change_reason='退市整理期'`，见 pipeline.delisting），不从名称里找「退」字（D-11），
        也不在这里另写一份区间判断。`announced_by` 取 T-1：T 日开盘前能看到的只有
        此前公告的事件，回测与实盘同一口径。公告日缺失的记录按已知处理——
        候选池宁可漏、不可错。

        池子没有覆盖 `day` 时**显式报错**：返回空集会让整理期标的静默进入候选池。
        """
        coverage = self.delisting_pool_coverage(day.year)
        if coverage is None or not coverage[0] <= day <= coverage[1]:
            raise RuntimeError(
                f"退市整理期池未覆盖 {day}（已构建 {coverage or '无'}）。"
                "先运行 scripts/build_delisting_pool.py 或等每晚同步刷新"
            )
        pool = self.delisting_pool(day, day)
        hit = pool.filter(pl.col("ann_date").is_null() | (pl.col("ann_date") <= announced_by))
        return set(hit["symbol"].to_list())

    def corporate_actions(self, symbols: Sequence[str] | None = None) -> pl.DataFrame:
        return self._read_unpartitioned(
            "corporate_actions", schema.CORPORATE_ACTIONS, symbols
        )

    def adj_factors(
        self,
        symbols: Sequence[str] | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> pl.DataFrame:
        """累计复权因子；可按日期下推过滤，避免回填时反复加载全表。"""
        if (start is None) != (end is None):
            raise ValueError("start and end must be provided together")
        if symbols is not None and len(symbols) == 0:
            return _empty(schema.ADJ_FACTORS)
        glob = self._glob("adj_factors", partitioned=False)
        if glob is None:
            return _empty(schema.ADJ_FACTORS)
        columns = _projection(self._con, glob, schema.ADJ_FACTORS)
        sql = f"SELECT {columns} FROM {_read(glob)}"
        clauses: list[str] = []
        params: list[object] = []
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
