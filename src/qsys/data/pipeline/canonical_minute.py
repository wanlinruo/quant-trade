"""阶段 6：并行发布统一历史一分钟线，不切换既有消费者。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import polars as pl

from qsys.data import schema
from qsys.data.pipeline.canonical_reference import Reconciliation, reconcile_frames
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


@dataclass(frozen=True, slots=True)
class MinuteBuildResult:
    days: int
    rows: int
    symbol_days: int
    nonstandard_counts: int
    sources: dict[str, int]


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    return datetime.combine(day, time.min), datetime.combine(day, time.max)


def _source_symbols(query: WarehouseQuery, warehouse: Warehouse, day: date) -> list[str]:
    lo, hi = _day_bounds(day)
    symbols: set[str] = set()
    for frame in (
        query.paid_minute_bars(None, lo, hi),
        query.minute_bars(None, lo, hi),
    ):
        if frame.height:
            symbols.update(frame["symbol"].to_list())
    for dataset in ("tushare_minute_bars", "joinquant_minute_bars"):
        path = warehouse.root / dataset / f"year={day.year}" / f"date={day:%Y%m%d}" / "part.parquet"
        if path.exists():
            symbols.update(pl.read_parquet(path, columns=["symbol"])["symbol"].unique().to_list())
    return sorted(symbols)


def _regular_session(frame: pl.DataFrame) -> pl.DataFrame:
    # Polars 的 hour/minute 是 Int8；小时乘 60 前先提升，避免 09:30 溢出成 58。
    minute = (
        pl.col("dt").dt.hour().cast(pl.Int32) * 60
        + pl.col("dt").dt.minute().cast(pl.Int32)
    )
    regular = (minute == 570) | minute.is_between(571, 690) | minute.is_between(781, 900)
    return frame.filter(regular)


def _invalid_market_rows(frame: pl.DataFrame) -> pl.DataFrame:
    """Return rows that cannot represent an executable market price."""
    return frame.filter(
        (pl.col("low") > pl.col("high"))
        | (pl.col("open") < pl.col("low")) | (pl.col("open") > pl.col("high"))
        | (pl.col("close") < pl.col("low")) | (pl.col("close") > pl.col("high"))
        | (pl.col("open") <= 0) | (pl.col("high") <= 0)
        | (pl.col("low") <= 0) | (pl.col("close") <= 0)
        | (pl.col("volume") < 0) | (pl.col("amount") < 0)
    )


def _quarantine_invalid_symbol_days(frame: pl.DataFrame) -> pl.DataFrame:
    """Drop a whole symbol-day when its selected source contains invalid bars.

    QMT represents some suspended instruments as 240/241 zero-price placeholders.
    They are not market facts. Keeping only the nonzero rows of such a symbol-day
    would create a partial session, so quarantine the entire selected symbol-day.
    """
    invalid = _invalid_market_rows(frame)
    if invalid.height == 0:
        return frame
    symbols = invalid.select("symbol").unique()
    return frame.join(symbols, on="symbol", how="anti")


def _quarantine_suspension_placeholders(
    frame: pl.DataFrame, query: WarehouseQuery, day: date
) -> pl.DataFrame:
    """Drop QMT symbol-days that are suspension placeholders, not market facts.

    QMT 旧数据会给停牌标的写一整天零成交、价格不变的分钟线。付费源同类标的日按
    ``zero-trade-flat-no-daily`` 隔离；这里用同一判据：全天零成交、最高价等于最低价、
    当天没有 Tushare 日线，整日剔除。有日线的零成交日保留，由日线说明它是交易日。
    """
    if frame.height == 0:
        return frame
    flat = (
        frame.filter(pl.col("source") == "qmt")
        .group_by("symbol")
        .agg(
            (pl.col("volume").sum() == 0).alias("zero"),
            (pl.col("high").max() == pl.col("low").min()).alias("flat"),
        )
        .filter(pl.col("zero") & pl.col("flat"))
    )
    if flat.height == 0:
        return frame
    daily = query.tushare_daily(
        flat["symbol"].to_list(), day, day, columns=("symbol", "dt")
    )
    placeholders = flat.join(daily.select("symbol"), on="symbol", how="anti")
    if placeholders.height == 0:
        return frame
    return frame.join(placeholders.select("symbol"), on="symbol", how="anti")


def _validate(frame: pl.DataFrame, day: date) -> None:
    if frame.height == 0:
        return
    if frame.select(pl.any_horizontal(pl.all().is_null()).any()).item():
        raise RuntimeError(f"{day} 规范分钟线含 null")
    if frame.height != frame.n_unique(subset=["symbol", "ts"]):
        raise RuntimeError(f"{day} 规范分钟线含重复 (symbol, ts)")
    if frame.filter(pl.col("ts").dt.date() != day).height:
        raise RuntimeError(f"{day} 分区混入其他日期")
    if frame.filter(
        (pl.col("ts").dt.second() != 0)
        | (pl.col("ts").dt.microsecond() != 0)
        | (pl.col("visible_at") < pl.col("ts"))
    ).height:
        raise RuntimeError(f"{day} 时间戳或 PIT visible_at 不符合契约")
    numeric = ["open", "high", "low", "close", "volume", "amount"]
    if frame.select(pl.any_horizontal(
        pl.col(numeric).is_nan() | pl.col(numeric).is_infinite()
    ).any()).item():
        raise RuntimeError(f"{day} 规范分钟线含非有限数值")
    invalid = _invalid_market_rows(frame)
    if invalid.height:
        raise RuntimeError(f"{day} 有 {invalid.height} 根分钟线违反 OHLC/量额约束")
    mixed = frame.group_by("symbol").agg(pl.col("source").n_unique().alias("n")).filter(pl.col("n") != 1)
    if mixed.height:
        raise RuntimeError(f"{day} 有 {mixed.height} 个标的日发生逐分钟混源")


def canonical_minute_frame(warehouse: Warehouse, day: date) -> pl.DataFrame:
    """按 paid > Tushare override > JoinQuant override > QMT 选择整个标的日。"""
    query = WarehouseQuery(warehouse)
    symbols = _source_symbols(query, warehouse, day)
    if not symbols:
        return pl.from_arrow(schema.BAR_MINUTE_1M.empty_table())
    selected = _quarantine_suspension_placeholders(
        _quarantine_invalid_symbol_days(
            _regular_session(query.intraday_bars(symbols, day))
        ),
        query, day,
    )
    frame = selected.rename({"dt": "ts"}).select(schema.BAR_MINUTE_1M.names)
    _validate(frame, day)
    return frame.sort(["symbol", "ts"])


def canonical_minute_preflight(warehouse: Warehouse, start: date, end: date) -> tuple[str, ...]:
    if start > end:
        return ("start 晚于 end",)
    query = WarehouseQuery(warehouse)
    issues: list[str] = []
    if query.intraday_bounds() is None:
        issues.append("没有任何已发布分钟来源")
    return tuple(issues)


def build_bar_minute_1m(warehouse: Warehouse, start: date, end: date) -> MinuteBuildResult:
    issues = canonical_minute_preflight(warehouse, start, end)
    if issues:
        raise RuntimeError("规范分钟预检失败：" + "；".join(issues))
    days = rows = symbol_days = nonstandard = 0
    sources: dict[str, int] = {}
    current = start
    while current <= end:
        frame = canonical_minute_frame(warehouse, current)
        if frame.height:
            warehouse.write_day(
                "bar_minute_1m", frame.to_arrow().cast(schema.BAR_MINUTE_1M), day=current
            )
            lo, hi = _day_bounds(current)
            written = WarehouseQuery(warehouse).bar_minute_1m(None, lo, hi)
            diff = reconcile_frames(frame, written, ("symbol", "ts"))
            if not diff.matches:
                raise RuntimeError(f"{current} 规范分钟写后对账失败")
            counts = frame.group_by("symbol").len()
            days += 1
            rows += frame.height
            symbol_days += counts.height
            nonstandard += counts.filter(~pl.col("len").is_in([240, 241])).height
            for row in frame.group_by("source").len().iter_rows(named=True):
                sources[row["source"]] = sources.get(row["source"], 0) + row["len"]
        current += timedelta(days=1)
    return MinuteBuildResult(days, rows, symbol_days, nonstandard, sources)


def minute_reconciliation(warehouse: Warehouse, day: date) -> Reconciliation:
    return reconcile_frames(
        canonical_minute_frame(warehouse, day),
        WarehouseQuery(warehouse).bar_minute_1m(None, *_day_bounds(day)),
        ("symbol", "ts"),
    )
