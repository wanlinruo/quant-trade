"""QMT 全市场分钟线回补的纯逻辑（D-20）。

编排在 `scripts/qmt_minute_backfill.py`：Windows 只下载、Linux 入库与逐日校验、
校验后删除 Windows 临时导出与 QMT 分钟缓存。这里只放可测的判断。
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date, datetime, time

import polars as pl

# 交易日的这段时间不跑回补：开盘前 10 分钟停，15:40 例行归档结束后再继续，
# 不与行情网关、影子盘、竞价/分钟归档争用 QMT。
BUSY_START, BUSY_END = time(8, 50), time(15, 50)
# QMT 分钟成交量为手（整数）；Tushare 日线为手但含零股小数，容差 1 手
VOLUME_TOLERANCE_LOTS = 1.0


def in_quiet_hours(now: datetime, sessions: Collection[date]) -> bool:
    if now.date() not in sessions:
        return True
    return not (BUSY_START <= now.time() < BUSY_END)


def verify_day(day: date, bars: pl.DataFrame, daily: pl.DataFrame) -> dict:
    """逐日校验：覆盖（对照当日有成交的日线标的）、每只 241 根、全天成交量与日线一致。

    `bars`: symbol, dt, volume（手）；`daily`: symbol, volume（手）。只报告，不改数据。
    """
    traded = daily.filter(pl.col("volume") > 0)
    per_symbol = bars.group_by("symbol").agg(
        pl.len().alias("bars"), pl.col("volume").sum().alias("minute_volume"))
    missing = sorted(set(traded["symbol"].to_list()) - set(per_symbol["symbol"].to_list()))
    joined = per_symbol.join(traded.select("symbol", pl.col("volume").alias("daily_volume")),
                             on="symbol", how="inner")
    volume_bad = joined.filter(
        (pl.col("minute_volume") - pl.col("daily_volume")).abs() > VOLUME_TOLERANCE_LOTS)
    non_241 = per_symbol.filter(pl.col("bars") != 241)
    return {
        "date": str(day),
        "symbols_expected": traded.height,
        "symbols_with_bars": per_symbol.height,
        "missing_symbols": missing,
        "non_241": non_241.height,
        "non_241_samples": non_241.head(20).rows(),
        "volume_mismatch": volume_bad.height,
        "volume_mismatch_samples": volume_bad.head(20).rows(),
        "rows": bars.height,
        "ok": not missing and non_241.height == 0 and volume_bad.height == 0,
    }
