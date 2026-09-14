"""日线采集流水线：增量、幂等、单点失败不中断。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.pipeline.ingest_log import IngestLog
from qsys.data.providers.base import BarsProvider
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

BAR_COLUMNS = ["symbol", "dt", "open", "high", "low", "close", "volume", "amount"]


def ingest_daily_bars(
    provider: BarsProvider,
    warehouse: Warehouse,
    symbols: Sequence[str],
    start: date,
    end: date,
) -> int:
    ingest_log = IngestLog(warehouse)
    collected: list[pl.DataFrame] = []

    for symbol in symbols:
        try:
            frame = provider.fetch_daily_bars(symbol, start, end)
        except Exception as exc:  # 单个标的失败不影响其余
            log.warning("bars.fetch_failed", symbol=symbol, error=str(exc))
            ingest_log.record("bars_daily", end, provider.name, 0, False, str(exc))
            continue

        if frame.height == 0:
            continue
        collected.append(frame.with_columns(pl.lit(symbol).alias("symbol")).select(BAR_COLUMNS))

    if not collected:
        return 0

    fresh = pl.concat(collected, how="vertical")
    written = 0
    query = WarehouseQuery(warehouse)

    for year in sorted({d.year for d in fresh["dt"].to_list()}):
        year_start, year_end = date(year, 1, 1), date(year, 12, 31)
        incoming = fresh.filter(pl.col("dt").is_between(year_start, year_end))
        # 读自己写的那张表。daily_bars() 换底读付费源之后，用它做合并
        # 会把付费数据灌进冻结的旧表，毁掉回归对账的参照物。
        existing = query.legacy_daily_bars(None, year_start, year_end)
        merged = (
            pl.concat([existing, incoming], how="vertical")
            # 新数据在后，keep="last" 让重采覆盖旧值；maintain_order 保证语义确定
            .unique(subset=["symbol", "dt"], keep="last", maintain_order=True)
            .sort(["symbol", "dt"])
            .select(BAR_COLUMNS)
        )
        warehouse.write("bars_daily", merged.to_arrow().cast(schema.BARS_DAILY), year=year)
        written += incoming.height
        ingest_log.record("bars_daily", year_end, provider.name, incoming.height, True)

    log.info("bars.ingested", provider=provider.name, rows=written, symbols=len(symbols))
    return written
