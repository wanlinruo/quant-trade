"""PIT 数据访问契约。

日线 bar 的可见时刻 = 该交易日的收盘时刻。`get()` 只返回可见时刻 <= as_of
的数据，使未来函数在 API 层面不可能发生，而非依赖策略作者自觉。

数据层不得再暴露任何无 as_of 的历史数据读取函数。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date, datetime
from enum import StrEnum

import polars as pl

from qsys.core.calendar import TradingCalendar, last_closed_session
from qsys.data.cutover import CutoverConfig, CutoverMarketData
from qsys.data.query import WarehouseQuery

PRICE_COLUMNS = ("open", "high", "low", "close")

# 成交量按**股本比例**复权，不按价格因子——价格因子含现金分红那一项，
# 股数只随送转与配股变化。实测 301379.SZ 的 2026-06-05（10 送 4 派 0.2）：
# 价格因子倒数 1.4075，股本比例 1.4000，差 0.5%，落在阈值上就翻转判定。
#
# `amount`（成交额）**不复权**：那是真实换手的钱，与股数无关。
# 于是 amount / 复权后量 恰好是复权后的均价，与复权价同一量纲。


class Adjustment(StrEnum):
    NONE = "none"
    BACKWARD = "backward"  # 后复权
    FORWARD = "forward"  # 前复权，以 as_of 时可见的最后一根 bar 为基准


class DataFeed(ABC):
    @abstractmethod
    def get(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
        *,
        as_of: datetime,
        adjustment: Adjustment = Adjustment.NONE,
    ) -> pl.DataFrame: ...


class HistoricalFeed(DataFeed):
    def __init__(
        self, query: WarehouseQuery, calendar: TradingCalendar,
        market_data: CutoverMarketData | None = None,
    ) -> None:
        self._query = query
        self._calendar = calendar
        self._market_data = market_data or CutoverMarketData(
            query, CutoverConfig.from_env()
        )

    def _last_visible_session(self, as_of: datetime) -> date | None:
        """as_of 时刻已收盘的最后一个交易日。"""
        return last_closed_session(self._calendar, as_of)

    def get(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
        *,
        as_of: datetime,
        adjustment: Adjustment = Adjustment.NONE,
    ) -> pl.DataFrame:
        visible_through = self._last_visible_session(as_of)
        if visible_through is None:
            return self._market_data.daily_bars(symbols, start, start).clear()

        effective_end = min(end, visible_through)
        if effective_end < start:
            return self._market_data.daily_bars(symbols, start, start).clear()

        bars = self._market_data.daily_bars(symbols, start, effective_end)
        if adjustment is Adjustment.NONE or bars.height == 0:
            return bars

        factors = self._market_data.adjustment_factors(
            symbols, start, effective_end
        )
        # 因子缺失时价格与成交量必须为 null，不能退回原价或填 1。累计因子
        # 带源自身锚点，1.0 并不是一个中性的可替代观测；见 D-9。
        joined = bars.join(factors, on=["symbol", "dt"], how="left").with_columns(
            pl.col("cum_factor"),
            pl.col("cum_share_factor")
            if "cum_share_factor" in factors.columns
            else pl.lit(None, dtype=pl.Float64).alias("cum_share_factor"),
        )

        if adjustment is Adjustment.FORWARD:
            anchor = joined.group_by("symbol").agg(
                pl.col("cum_factor").sort_by("dt").last().alias("anchor"),
                pl.col("cum_share_factor").sort_by("dt").last().alias("share_anchor"),
            )
            joined = joined.join(anchor, on="symbol", how="left").with_columns(
                (pl.col("cum_factor") / pl.col("anchor")).alias("cum_factor"),
                # 前复权量：早于锚点的送转要把历史成交量放大到同一股本口径
                (pl.col("share_anchor") / pl.col("cum_share_factor"))
                .alias("volume_factor"),
            )
        else:
            # 后复权量：以最早的股本为基准，送转之后的量要缩回去
            joined = joined.with_columns(
                (1.0 / pl.col("cum_share_factor")).alias("volume_factor")
            )

        return (
            joined.with_columns(
                *[(pl.col(c) * pl.col("cum_factor")).alias(c) for c in PRICE_COLUMNS],
                (pl.col("volume") * pl.col("volume_factor")).round(0)
                .cast(pl.Int64).alias("volume"),
            )
            .select(bars.columns)
            .sort(["symbol", "dt"])
        )
