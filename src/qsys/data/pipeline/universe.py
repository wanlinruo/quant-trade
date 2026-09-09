"""全市场批量采集。

内存是硬约束：Linux 核心节点可用约 870 MB，5400 只 × 多年 ≈ 千万行日线，
全量装进 DataFrame 必 OOM。因此**分批落盘**不是优化，是可行性前提。

采集会跑很久，进程可能被杀。已完成的标的记入 ingest_log，重跑时跳过——
失败的标的不记入，下次自动重试。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.pipeline.bars import BAR_COLUMNS
from qsys.data.pipeline.ingest_log import IngestLog
from qsys.data.providers.base import BarsProvider
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

_DATASET = "bars_daily"


@dataclass(frozen=True, slots=True)
class IngestProgress:
    total: int
    done: int
    failed: int
    current: str = ""

    @property
    def remaining(self) -> int:
        return self.total - self.done - self.failed


def _log_key(start: date, end: date) -> str:
    """断点续传的记账键。

    ingest_log 的 (dataset, dt, provider) 三元组被复用为
    (区间键, 区间末日, 标的)——续传要按「标的 × 区间」记账，而 1-1 的日志
    表本就是按这三元组去重的，不必为此另开一张表。
    """
    return f"{_DATASET}:{start.isoformat()}:{end.isoformat()}"


def completed_symbols(warehouse: Warehouse, start: date, end: date) -> set[str]:
    """已成功采集过该区间的标的。"""
    key = _log_key(start, end)
    ingest_log = IngestLog(warehouse)
    done: set[str] = set()
    for year in range(start.year, end.year + 1):
        entries = ingest_log.entries(year)
        if entries.height == 0:
            continue
        hit = entries.filter((pl.col("dataset") == key) & pl.col("ok"))
        done.update(hit["provider"].to_list())
    return done


def ingest_universe(
    provider: BarsProvider,
    warehouse: Warehouse,
    symbols: Sequence[str],
    start: date,
    end: date,
    *,
    complete_through: date,
    batch_size: int = 200,
    on_progress: Callable[[IngestProgress], None] | None = None,
    skip_done: bool = True,
) -> IngestProgress:
    """分批采集全市场日线。返回最终进度。

    `complete_through` 是**已收盘**的最后一个交易日，晚于它的 bar 一律丢弃。
    这个参数没有默认值，是故意的：盘中采到的「今日」只有半天成交量，收盘价
    其实是采集那一刻的价格，落盘后会让涨停判定与生态因子一路错下去且不报错。
    调用方必须明确表态截止到哪一天，不能忘。
    """
    targets = list(symbols)
    if skip_done:
        already = completed_symbols(warehouse, start, end)
        targets = [s for s in targets if s not in already]

    total = len(symbols)
    done = len(symbols) - len(targets)
    failed = 0
    ingest_log = IngestLog(warehouse)
    key = _log_key(start, end)

    for offset in range(0, len(targets), batch_size):
        batch = targets[offset : offset + batch_size]
        collected: list[pl.DataFrame] = []
        succeeded: list[str] = []

        for symbol in batch:
            try:
                frame = provider.fetch_daily_bars(symbol, start, end)
            except Exception as exc:  # 单只失败不中断整轮
                failed += 1
                log.warning("universe.fetch_failed", symbol=symbol, error=str(exc))
                continue
            succeeded.append(symbol)
            frame = frame.filter(pl.col("dt") <= complete_through)
            if frame.height:
                collected.append(
                    frame.with_columns(pl.lit(symbol).alias("symbol")).select(BAR_COLUMNS)
                )

        if collected:
            _write_batch(warehouse, pl.concat(collected, how="vertical"))
        # 空结果（停牌整段 / 未上市）算完成，不算失败
        for symbol in succeeded:
            ingest_log.record(key, end, symbol, 0, True)
        done += len(succeeded)

        progress = IngestProgress(total, done, failed, batch[-1] if batch else "")
        log.info("universe.progress", total=total, done=done, failed=failed)
        if on_progress is not None:
            on_progress(progress)

        del collected  # 尽早释放，内存紧张

    return IngestProgress(total, done, failed)


def _write_batch(warehouse: Warehouse, fresh: pl.DataFrame) -> None:
    """按年合并写入。每批单独走一次，避免全量驻留内存。

    WarehouseQuery 在函数内构造：本批刚写过的分区下一批还要再读，
    每次开新连接可确保读到的是磁盘上的当前状态，不吃任何缓存。
    """
    from qsys.data.query import WarehouseQuery

    query = WarehouseQuery(warehouse)
    for year in sorted({d.year for d in fresh["dt"].to_list()}):
        lo, hi = date(year, 1, 1), date(year, 12, 31)
        incoming = fresh.filter(pl.col("dt").is_between(lo, hi))
        merged = (
            pl.concat([query.daily_bars(None, lo, hi), incoming], how="vertical")
            .unique(subset=["symbol", "dt"], keep="last", maintain_order=True)
            .sort(["symbol", "dt"])
            .select(BAR_COLUMNS)
        )
        warehouse.write(_DATASET, merged.to_arrow().cast(schema.BARS_DAILY), year=year)
