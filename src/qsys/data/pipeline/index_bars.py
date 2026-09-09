"""指数日线采集。

沪深 300 是 S6 Gate 的输入（`index_ma20_slope_5d`），而个股接口取不到指数——
必须走 mootdx 的 `index()`。指数数量少（个位数），不需要分批落盘。

**与个股日线分表**：候选池扫描会遍历 `bars_daily` 的全部标的，指数混进去
会被当成股票。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.pipeline.bars import BAR_COLUMNS
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

_DATASET = "index_bars"

CSI300 = "000300.SH"


def ingest_index_bars(
    provider,
    warehouse: Warehouse,
    symbols: Sequence[str],
    start: date,
    end: date,
    *,
    complete_through: date,
) -> int:
    """采集指数日线。返回写入行数。

    `complete_through` 与个股日线同一条纪律：未收盘的当日 bar 不落盘。
    """
    collected: list[pl.DataFrame] = []
    for symbol in symbols:
        try:
            frame = provider.fetch_index_bars(symbol, start, end)
        except Exception as exc:  # 单个指数失败不中断
            log.warning("index_bars.fetch_failed", symbol=symbol, error=str(exc))
            continue
        frame = frame.filter(pl.col("dt") <= complete_through)
        if frame.height:
            collected.append(
                frame.with_columns(pl.lit(symbol).alias("symbol")).select(BAR_COLUMNS)
            )

    if not collected:
        return 0

    fresh = pl.concat(collected, how="vertical")
    query = WarehouseQuery(warehouse)
    written = 0
    for year in sorted({d.year for d in fresh["dt"].to_list()}):
        lo, hi = date(year, 1, 1), date(year, 12, 31)
        incoming = fresh.filter(pl.col("dt").is_between(lo, hi))
        merged = (
            pl.concat([query.index_bars(None, lo, hi), incoming], how="vertical")
            .unique(subset=["symbol", "dt"], keep="last", maintain_order=True)
            .sort(["symbol", "dt"])
            .select(BAR_COLUMNS)
        )
        warehouse.write(_DATASET, merged.to_arrow().cast(schema.INDEX_BARS), year=year)
        written += incoming.height

    log.info("index_bars.ingested", rows=written, symbols=len(symbols))
    return written
