"""mootdx 日线行情适配器（不复权）。

mootdx 的 `bars(symbol, frequency, start, offset)` 中 `start` 是「距最新第几根」，
`offset` 是本次取多少根，单次上限约 800。计划 1-1 只调一次，历史被截断在约
3.2 年；这里按需翻页直到覆盖请求起点（实测分页间无重叠，可回溯至 2018 年）。

翻满 `max_pages` 仍未覆盖请求起点才算真截断，必须显式告警——静默截断会让回测
在不知情的情况下少掉早期数据。翻到空页则是标的上市不足请求区间，不是截断。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import structlog

from qsys.data.providers.base import BarsProvider

log = structlog.get_logger(__name__)

COLUMNS = ["dt", "open", "high", "low", "close", "volume", "amount"]

_DAILY_FREQUENCY = 9  # mootdx 频率码：日线
_DEFAULT_PAGE_SIZE = 800  # 单次调用上限
_DEFAULT_MAX_PAGES = 20  # 20 × 800 ≈ 64 年，足够覆盖任何 A 股标的


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

    def __init__(
        self,
        client=None,
        *,
        page_size: int = _DEFAULT_PAGE_SIZE,
        max_pages: int = _DEFAULT_MAX_PAGES,
    ) -> None:
        if client is None:
            from mootdx.quotes import Quotes

            client = Quotes.factory(market="std")
        self._client = client
        self._page_size = page_size
        self._max_pages = max_pages
        # 翻满 max_pages 仍未覆盖请求起点时记录：(symbol, 请求起点, 实际最早)
        self.truncated: list[tuple[str, date, date]] = []

    def fetch_daily_bars(self, symbol: str, start: date, end: date) -> pl.DataFrame:
        """按需翻页直到覆盖 start。"""
        code = symbol.split(".")[0]
        pages: list[pl.DataFrame] = []
        earliest: date | None = None

        for page in range(self._max_pages):
            raw = self._client.bars(
                symbol=code,
                frequency=_DAILY_FREQUENCY,
                start=page * self._page_size,
                offset=self._page_size,
            )
            if raw is None or len(raw) == 0:
                break  # 数据耗尽（标的上市不足请求区间）

            parsed = self._parse(raw)
            if parsed.height == 0:
                break
            pages.append(parsed)
            earliest = parsed["dt"].min()
            if earliest is not None and earliest <= start:
                break
        else:
            if earliest is not None and earliest > start:
                self.truncated.append((symbol, start, earliest))
                log.warning(
                    "mootdx.history_truncated",
                    symbol=symbol,
                    requested_start=str(start),
                    earliest_available=str(earliest),
                    pages=self._max_pages,
                    hint="翻满分页上限仍未覆盖请求起点，早期数据缺失",
                )

        if not pages:
            return _empty()

        return (
            pl.concat(pages, how="vertical")
            .unique(subset=["dt"], keep="first", maintain_order=True)
            .sort("dt")
            .filter(pl.col("dt").is_between(start, end))
            .select(COLUMNS)
        )

    def _parse(self, raw) -> pl.DataFrame:
        df = pl.from_pandas(raw.reset_index(drop=True))
        return df.select(
            pl.col("datetime").cast(pl.Utf8).str.slice(0, 10).str.to_date().alias("dt"),
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("vol").cast(pl.Int64).alias("volume"),
            pl.col("amount").cast(pl.Float64),
        ).sort("dt")
