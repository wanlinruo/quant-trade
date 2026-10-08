"""同日 Tushare 停复牌快照：盘前观测、归档前刷新与审计证据。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SuspensionSnapshot:
    """一次 ``suspend_d`` 观测；空集合也是成功观测，不能和调用失败混淆。"""

    observed_at: datetime
    row_count: int
    content_hash: str
    accepted: bool = True
    previous_rows: int = 0

    @property
    def detail(self) -> str:
        status = "accepted" if self.accepted else "regressed-preserved"
        return (
            f"query_status=success; snapshot_status={status}; "
            f"observed_at={self.observed_at.isoformat(timespec='seconds')}; "
            f"rows={self.row_count}; previous_rows={self.previous_rows}; "
            f"content_hash={self.content_hash}"
        )


def _suspension_frame(raw: pl.DataFrame, day: date) -> pl.DataFrame:
    """把单次 ``suspend_d`` 响应规范化；成功空集合保持为有类型的空表。"""
    if raw.height == 0:
        return pl.DataFrame(schema={
            "symbol": pl.Utf8, "dt": pl.Date,
            "suspend_type": pl.Utf8, "suspend_timing": pl.Utf8,
        })
    required = {"ts_code", "trade_date", "suspend_type"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"suspend_d 缺少字段：{', '.join(sorted(missing))}")
    timing = (pl.col("suspend_timing").cast(pl.Utf8)
              if "suspend_timing" in raw.columns else pl.lit(None, dtype=pl.Utf8))
    return (
        raw.select(
            pl.col("ts_code").cast(pl.Utf8).alias("symbol"),
            pl.col("trade_date").cast(pl.Utf8)
            .str.strptime(pl.Date, "%Y%m%d").alias("dt"),
            pl.col("suspend_type").cast(pl.Utf8),
            timing.alias("suspend_timing"),
        )
        .filter(pl.col("dt") == day)
        .unique(subset=["symbol", "dt", "suspend_type", "suspend_timing"])
        .sort(["symbol", "suspend_type", "suspend_timing"], nulls_last=False)
    )


def _snapshot_hash(frame: pl.DataFrame) -> str:
    return hashlib.sha256(frame.write_csv().encode("utf-8")).hexdigest()


def sync_premarket_suspensions(
    provider, warehouse: Warehouse, day: date, *, observed_at: datetime | None = None,
) -> SuspensionSnapshot:
    """采集并按交易日幂等替换 ``suspend_daily``。

    盘前同一交易日会多次观测。接口“不定期更新”，较晚响应若比已经落库的
    快照更少，不能把它当作权威删除信号；保留较完整版本并留下退化证据。
    真正的调用异常直接抛出，由 ServiceRun 记录为失败，绝不伪装成空集合。
    """
    observed_at = observed_at or datetime.now()
    frame = _suspension_frame(provider.suspend_d(day), day)
    query = WarehouseQuery(warehouse)
    existing_day = query.suspensions(None, day, day)
    accepted = frame.height >= existing_day.height
    digest = _snapshot_hash(frame)
    if accepted:
        year_start, year_end = date(day.year, 1, 1), date(day.year, 12, 31)
        existing_year = query.suspensions(None, year_start, year_end)
        merged = (
            pl.concat(
                [existing_year.filter(pl.col("dt") != day), frame],
                how="vertical_relaxed",
            )
            .sort(["symbol", "dt", "suspend_type", "suspend_timing"],
                  nulls_last=False)
            .select(schema.SUSPEND_DAILY.names)
        )
        warehouse.write(
            "suspend_daily", merged.to_arrow().cast(schema.SUSPEND_DAILY), year=day.year
        )
    else:
        log.warning(
            "premarket_suspensions.regressed", day=str(day), rows=frame.height,
            previous_rows=existing_day.height, content_hash=digest,
        )
    snapshot = SuspensionSnapshot(
        observed_at, frame.height, digest, accepted, existing_day.height
    )
    log.info("premarket_suspensions.observed", day=str(day), **{
        "rows": snapshot.row_count,
        "previous_rows": snapshot.previous_rows,
        "content_hash": snapshot.content_hash,
        "accepted": snapshot.accepted,
        "observed_at": snapshot.observed_at.isoformat(timespec="seconds"),
    })
    return snapshot
