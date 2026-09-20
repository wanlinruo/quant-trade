"""阶段 3：规范基础资料的并行构建与对账。

新表只从已确认的旧规范输入或 Tushare ``trade_cal`` 构建；不改旧表，不切换
消费者。名称点观测、名称区间和交易日历三种有效性不互相填充。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import math

import polars as pl

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


@dataclass(frozen=True, slots=True)
class Reconciliation:
    left_rows: int
    right_rows: int
    left_only: tuple[tuple[object, ...], ...]
    right_only: tuple[tuple[object, ...], ...]
    mismatched: tuple[tuple[object, ...], ...]

    @property
    def matches(self) -> bool:
        return not (self.left_only or self.right_only or self.mismatched)


@dataclass(frozen=True, slots=True)
class IntervalIssue:
    symbol: str
    kind: str
    previous_end: date | None
    next_start: date


def reference_preflight(warehouse: Warehouse) -> tuple[str, ...]:
    """在任何写入前确认旧表足以构建规范表；绝不补造缺失语义。"""
    query = WarehouseQuery(warehouse)
    master = query.instruments()
    history = query.name_history()
    issues: list[str] = []
    missing_master = set(schema.SECURITY_MASTER.names) - set(master.columns)
    if master.height == 0:
        issues.append("instruments 为空")
    if missing_master:
        issues.append(f"instruments 缺列 {sorted(missing_master)}")
    if "as_of" in master.columns and master["as_of"].null_count():
        issues.append(f"instruments.as_of 含 {master['as_of'].null_count()} 个 null")
    missing_history = set(schema.SECURITY_NAME_HISTORY.names) - set(history.columns)
    if history.height == 0:
        issues.append("name_history 为空")
    if missing_history:
        issues.append(f"name_history 缺列 {sorted(missing_history)}")
    return tuple(issues)


def require_reference_sources(warehouse: Warehouse) -> None:
    issues = reference_preflight(warehouse)
    if issues:
        raise RuntimeError("规范基础资料预检失败：" + "；".join(issues))


def build_security_master(warehouse: Warehouse) -> int:
    source = WarehouseQuery(warehouse).instruments()
    if source.height == 0:
        return 0
    missing = set(schema.SECURITY_MASTER.names) - set(source.columns)
    if missing:
        raise RuntimeError(
            f"instruments 缺规范构建必需列 {sorted(missing)}；先刷新来源，禁止填值"
        )
    if source["as_of"].null_count():
        raise RuntimeError("instruments.as_of 含 null；禁止用当前日期补造观测日")
    target = source.select(schema.SECURITY_MASTER.names).sort("symbol")
    warehouse.write(
        "security_master", target.to_arrow().cast(schema.SECURITY_MASTER)
    )
    return target.height


def build_security_name_history(warehouse: Warehouse) -> int:
    source = WarehouseQuery(warehouse).name_history()
    if source.height == 0:
        return 0
    missing = set(schema.SECURITY_NAME_HISTORY.names) - set(source.columns)
    if missing:
        raise RuntimeError(
            f"name_history 缺规范构建必需列 {sorted(missing)}；禁止推断区间"
        )
    target = source.select(schema.SECURITY_NAME_HISTORY.names).sort(
        ["symbol", "start_date", "name"]
    )
    warehouse.write(
        "security_name_history",
        target.to_arrow().cast(schema.SECURITY_NAME_HISTORY),
    )
    return target.height


def _source_date(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d", strict=False)


def normalize_trading_calendar(
    raw: pl.DataFrame,
    *,
    market: str = "SSE",
) -> pl.DataFrame:
    """规范化 trade_cal 原始响应；缺字段直接失败，不按星期推断。"""
    required = {"cal_date", "is_open", "pretrade_date"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"trade_cal 缺字段：{sorted(missing)}")
    if raw.height == 0:
        return pl.from_arrow(schema.TRADING_CALENDAR.empty_table())
    exchange = (
        pl.col("exchange").cast(pl.Utf8)
        if "exchange" in raw.columns else pl.lit(market, dtype=pl.Utf8)
    )
    result = raw.select(
        exchange.alias("market"),
        _source_date("cal_date").alias("dt"),
        pl.col("is_open").cast(pl.Int64, strict=False).eq(1).alias("is_open"),
        _source_date("pretrade_date").alias("pretrade_date"),
    )
    if result["dt"].null_count():
        raise ValueError("trade_cal 含无法解析的 cal_date")
    return result.unique(
        subset=["market", "dt"], keep="last", maintain_order=True
    ).sort(["market", "dt"])


def require_calendar_coverage(
    raw: pl.DataFrame,
    start: date,
    end: date,
    *,
    market: str = "SSE",
) -> None:
    """来源必须逐自然日给出开闭市事实；这里只验覆盖，不推断开市。"""
    normalized = normalize_trading_calendar(raw, market=market)
    expected = {
        start + timedelta(days=offset)
        for offset in range((end - start).days + 1)
    }
    actual = set(normalized["dt"].to_list())
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        missing_text = [day.isoformat() for day in missing[:10]]
        extra_text = [day.isoformat() for day in extra[:10]]
        raise RuntimeError(
            f"trade_cal 自然日覆盖不完整：missing={missing_text} extra={extra_text}"
        )


def publish_trading_calendar(warehouse: Warehouse, raw: pl.DataFrame, *, market: str = "SSE") -> int:
    incoming = normalize_trading_calendar(raw, market=market)
    if incoming.height == 0:
        return 0
    query = WarehouseQuery(warehouse)
    for year in sorted(set(incoming["dt"].dt.year().to_list())):
        lo, hi = date(year, 1, 1), date(year, 12, 31)
        chunk = incoming.filter(pl.col("dt").dt.year() == year)
        existing = query.trading_calendar(lo, hi, market=market)
        covered = set(chunk["dt"].to_list())
        untouched = existing.filter(~pl.col("dt").is_in(list(covered)))
        merged = pl.concat([untouched, chunk], how="vertical_relaxed").unique(
            subset=["market", "dt"], keep="last"
        ).sort(["market", "dt"]).select(schema.TRADING_CALENDAR.names)
        warehouse.write(
            "trading_calendar",
            merged.to_arrow().cast(schema.TRADING_CALENDAR),
            year=year,
        )
    return incoming.height


def reconcile_frames(
    left: pl.DataFrame,
    right: pl.DataFrame,
    keys: tuple[str, ...],
    *,
    float_tolerances: dict[str, tuple[float, float]] | None = None,
) -> Reconciliation:
    """逐键逐字段比较；仅对显式登记的浮点列使用 ``(abs, rel)`` 容差。"""
    tolerances = float_tolerances or {}

    def indexed(frame: pl.DataFrame) -> dict[tuple[object, ...], tuple[object, ...]]:
        values = [name for name in frame.columns if name not in keys]
        return {
            tuple(row[name] for name in keys): tuple(row[name] for name in values)
            for row in frame.to_dicts()
        }

    lhs, rhs = indexed(left), indexed(right)
    value_columns = [name for name in left.columns if name not in keys]

    def row_matches(left_values: tuple[object, ...], right_values: tuple[object, ...]) -> bool:
        if len(left_values) != len(right_values):
            return False
        for column, left_value, right_value in zip(
            value_columns, left_values, right_values
        ):
            if column in tolerances and isinstance(left_value, float) \
                    and isinstance(right_value, float):
                absolute, relative = tolerances[column]
                if not math.isclose(
                    left_value, right_value, abs_tol=absolute, rel_tol=relative
                ):
                    return False
            elif left_value != right_value:
                return False
        return True

    left_only = tuple(sorted(set(lhs) - set(rhs), key=str))
    right_only = tuple(sorted(set(rhs) - set(lhs), key=str))
    mismatched = tuple(sorted(
        (
            key for key in set(lhs) & set(rhs)
            if not row_matches(lhs[key], rhs[key])
        ),
        key=str,
    ))
    return Reconciliation(
        left.height, right.height, left_only, right_only, mismatched
    )


def reference_reconciliation(warehouse: Warehouse) -> dict[str, Reconciliation]:
    query = WarehouseQuery(warehouse)
    return {
        "security_master": reconcile_frames(
            query.instruments(), query.security_master(), ("symbol",)
        ),
        "security_name_history": reconcile_frames(
            query.name_history(), query.security_name_history(),
            ("symbol", "start_date", "name"),
        ),
    }


def calendar_reconciliation(
    raw: pl.DataFrame,
    stored: pl.DataFrame,
    *,
    market: str = "SSE",
) -> Reconciliation:
    expected = normalize_trading_calendar(raw, market=market)
    return reconcile_frames(expected, stored, ("market", "dt"))


def name_interval_issues(frame: pl.DataFrame) -> tuple[IntervalIssue, ...]:
    """只报告重叠/断档，不把点观测拿来补区间。"""
    issues: list[IntervalIssue] = []
    for symbol, group in frame.sort(["symbol", "start_date"]).group_by(
        "symbol", maintain_order=True
    ):
        ident = symbol[0] if isinstance(symbol, tuple) else symbol
        rows = group.to_dicts()
        for previous, current in zip(rows, rows[1:]):
            previous_end = previous["end_date"]
            next_start = current["start_date"]
            if previous_end is None or next_start <= previous_end:
                issues.append(IntervalIssue(ident, "overlap", previous_end, next_start))
            elif next_start > previous_end + timedelta(days=1):
                issues.append(IntervalIssue(ident, "gap", previous_end, next_start))
    return tuple(issues)
