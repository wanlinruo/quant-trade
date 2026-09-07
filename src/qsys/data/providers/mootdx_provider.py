"""mootdx 日线行情适配器（不复权）。

注意 mootdx 的 offset 语义是"最近 N 根 bar"，不是起始位置，且单次调用有上限。
若请求区间早于返回的最早一根 bar，说明历史被截断——必须显式告警，
静默截断会让回测在不知情的情况下少掉早期数据。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import structlog

from qsys.data.providers.base import BarsProvider

log = structlog.get_logger(__name__)

COLUMNS = ["dt", "open", "high", "low", "close", "volume", "amount"]

_DAILY_FREQUENCY = 9  # mootdx 频率码：日线
_MAX_OFFSET = 800  # 单次调用上限，约回溯 3.2 年


def _empty() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "dt": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Int64,
            "amount": pl.Float64,
        }
    )


class MootdxProvider(BarsProvider):
    name = "mootdx"

    def __init__(self, client=None) -> None:
        if client is None:
            from mootdx.quotes import Quotes

            client = Quotes.factory(market="std")
        self._client = client
        # 记录被 offset 上限截断的请求：(symbol, 请求起始日, 实际最早日)
        self.truncated: list[tuple[str, date, date]] = []

    def fetch_daily_bars(self, symbol: str, start: date, end: date) -> pl.DataFrame:
        code = symbol.split(".")[0]
        raw = self._client.bars(
            symbol=code, frequency=_DAILY_FREQUENCY, offset=_MAX_OFFSET
        )
        if raw is None or len(raw) == 0:
            return _empty()

        df = pl.from_pandas(raw.reset_index(drop=True))
        parsed = df.select(
            pl.col("datetime").cast(pl.Utf8).str.slice(0, 10).str.to_date().alias("dt"),
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("vol").cast(pl.Int64).alias("volume"),
            pl.col("amount").cast(pl.Float64),
        ).sort("dt")

        earliest = parsed["dt"].min()
        if earliest is not None and earliest > start:
            self.truncated.append((symbol, start, earliest))
            log.warning(
                "mootdx.history_truncated",
                symbol=symbol,
                requested_start=str(start),
                earliest_available=str(earliest),
                hint="offset 上限导致回溯不足，早期数据缺失",
            )

        return parsed.filter(pl.col("dt").is_between(start, end)).select(COLUMNS)
