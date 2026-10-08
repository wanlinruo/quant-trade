"""盘前交易事实：当日涨跌停价与停复牌信息。

Tushare 文档（doc_id=183）：stk_limit「每个交易日 9 点左右更新当日股票涨跌停价格」。
QMT `get_full_tick` 不给涨跌停价，所以模拟盘、影子盘、实盘的盘中涨停价与回测
同源——都读 `price_limits_daily`；Windows 行情网关读不到仓库，改读 Redis 上的同一份值。
取不到就是取不到：不按板块档位自算，也不拿 QMT 顶替。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.live.config import StreamConfig
from qsys.live.transport import Transport

log = structlog.get_logger(__name__)

# 当日条数低于上一交易日的这个比例，视为尚未发布完整，不写半张表
COMPLETE_RATIO = 0.9


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


def publish_key(streams: StreamConfig, day: date) -> str:
    return f"{streams.price_limits}:{day:%Y%m%d}"


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
    payload = frame.write_csv().encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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


def sync_premarket_limits(
    provider, warehouse: Warehouse, transport: Transport, streams: StreamConfig, day: date,
) -> bool:
    """取一次当日 stk_limit；完整则写仓库并发布，返回 True。"""
    raw = provider.stk_limit(day)
    if raw.height == 0:
        log.info("premarket_limits.not_published", day=str(day))
        return False
    query = WarehouseQuery(warehouse)
    previous = query.price_limits(None, day - timedelta(days=30), day - timedelta(days=1))
    expected = (previous.filter(pl.col("dt") == previous["dt"].max()).height
                if previous.height else 0)
    if raw.height < expected * COMPLETE_RATIO:
        log.warning("premarket_limits.incomplete", day=str(day), rows=raw.height,
                    previous_rows=expected)
        return False

    frame = raw.select(
        pl.col("ts_code").alias("symbol"),
        pl.col("trade_date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d").alias("dt"),
        # 盘前没有当日日线，pre_close 如实记 null；17:00 同步按 (symbol, dt) 覆盖
        pl.lit(None, dtype=pl.Float64).alias("pre_close"),
        pl.col("up_limit").cast(pl.Float64).alias("limit_up"),
        pl.col("down_limit").cast(pl.Float64).alias("limit_down"),
    ).filter(pl.col("dt") == day)
    existing = query.price_limits(None, date(day.year, 1, 1), date(day.year, 12, 31))
    merged = (
        pl.concat([existing.filter(pl.col("dt") != day), frame], how="vertical_relaxed")
        .sort(["symbol", "dt"]).select(schema.PRICE_LIMITS_DAILY.names)
    )
    warehouse.write("price_limits_daily",
                    merged.to_arrow().cast(schema.PRICE_LIMITS_DAILY), year=day.year)
    transport.set_key(publish_key(streams, day), json.dumps({
        row["symbol"]: [row["limit_up"], row["limit_down"]]
        for row in frame.iter_rows(named=True)
    }))
    log.info("premarket_limits.synced", day=str(day), rows=frame.height)
    return True


def run_premarket_limits(
    provider, warehouse: Warehouse, transport: Transport, streams: StreamConfig, day: date,
    *, deadline: datetime, interval: timedelta = timedelta(minutes=1),
    now: Callable[[], datetime] = datetime.now,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """重试到 `deadline`；成功返回 0，到点仍未取到完整数据返回 1。"""
    while True:
        try:
            if sync_premarket_limits(provider, warehouse, transport, streams, day):
                return 0
        except Exception as exc:  # 单次接口失败继续重试，到点仍失败再报错
            log.warning("premarket_limits.attempt_failed", day=str(day), error=str(exc))
        if now() + interval > deadline:
            log.error("premarket_limits.gave_up", day=str(day), deadline=str(deadline))
            return 1
        sleep(interval.total_seconds())
