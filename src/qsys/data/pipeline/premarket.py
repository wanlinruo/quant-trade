"""盘前涨跌停价：当日 Tushare stk_limit 写入仓库并发布给盘中进程（D-19）。

Tushare 文档（doc_id=183）：stk_limit「每个交易日 9 点左右更新当日股票涨跌停价格」。
QMT `get_full_tick` 不给涨跌停价，所以模拟盘、影子盘、实盘的盘中涨停价与回测
同源——都读 `price_limits_daily`；Windows 行情网关读不到仓库，改读 Redis 上的同一份值。
取不到就是取不到：不按板块档位自算，也不拿 QMT 顶替。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
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


def publish_key(streams: StreamConfig, day: date) -> str:
    return f"{streams.price_limits}:{day:%Y%m%d}"


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
