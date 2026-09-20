"""阶段 8 稳定查询门面：历史与实时默认隔离，合并必须显式授权。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Sequence

import polars as pl

from qsys.data import schema
from qsys.data.pipeline.canonical_reference import Reconciliation, reconcile_frames
from qsys.data.query import WarehouseQuery
from qsys.data.realtime import (
    LatestQuote,
    RealtimeDailyBar,
    RealtimeMarketState,
    RealtimeMinuteBar,
    RealtimeUnavailable,
)


class StableMarketData:
    """目标数据集的唯一新门面；旧 ``WarehouseQuery`` 方法继续兼容但不扩展。"""

    def __init__(
        self, historical: WarehouseQuery, realtime: RealtimeMarketState | None = None
    ) -> None:
        self._historical = historical
        self._realtime = realtime

    def historical_daily_bars(
        self, symbols: Sequence[str] | None, start: date, end: date
    ) -> pl.DataFrame:
        return self._historical.historical_daily_bars(symbols, start, end)

    def historical_minute_bars(
        self, symbols: Sequence[str] | None, start: datetime, end: datetime
    ) -> pl.DataFrame:
        return self._historical.historical_minute_bars(symbols, start, end)

    def _live(self) -> RealtimeMarketState:
        if self._realtime is None:
            raise RealtimeUnavailable("未配置实时行情状态")
        return self._realtime

    def realtime_quotes(
        self, symbols: Sequence[str] | None, *, as_of: datetime,
        max_age: timedelta = timedelta(seconds=5),
    ) -> dict[str, LatestQuote]:
        selected = None if symbols is None else list(symbols)
        return self._live().latest(selected, as_of=as_of, max_age=max_age)

    def realtime_daily_bars(
        self, symbols: Sequence[str] | None, *, as_of: datetime,
        max_age: timedelta = timedelta(seconds=5),
    ) -> list[RealtimeDailyBar]:
        selected = None if symbols is None else list(symbols)
        return self._live().daily_bars(selected, as_of=as_of, max_age=max_age)

    def realtime_minute_bars(
        self, symbols: Sequence[str] | None, *, as_of: datetime,
        include_current: bool = False,
    ) -> list[RealtimeMinuteBar]:
        selected = None if symbols is None else list(symbols)
        return self._live().minute_bars(
            selected, as_of=as_of, include_current=include_current
        )

    def daily_bars(
        self,
        symbols: Sequence[str] | None,
        start: date,
        end: date,
        *,
        include_live: bool = False,
        as_of: datetime | None = None,
        max_age: timedelta = timedelta(seconds=5),
    ) -> pl.DataFrame:
        historical = self.historical_daily_bars(symbols, start, end)
        if not include_live:
            return historical
        if as_of is None:
            raise ValueError("include_live=True 必须显式提供 as_of")
        if not start <= as_of.date() <= end:
            return historical
        live = self.realtime_daily_bars(symbols, as_of=as_of, max_age=max_age)
        if not live:
            return historical
        frame = pl.DataFrame([{
            "symbol": row.symbol, "dt": row.dt, "pre_close": row.prev_close,
            "open": row.open, "high": row.high, "low": row.low, "close": row.close,
            "volume": row.volume, "amount": row.amount,
        } for row in live]).select(schema.BAR_DAILY.names)
        return pl.concat([
            historical.filter(pl.col("dt") != as_of.date()), frame,
        ], how="vertical_relaxed").unique(
            subset=["symbol", "dt"], keep="last"
        ).sort(["symbol", "dt"])

    def minute_bars(
        self,
        symbols: Sequence[str] | None,
        start: datetime,
        end: datetime,
        *,
        include_live: bool = False,
        as_of: datetime | None = None,
        include_current: bool = False,
    ) -> pl.DataFrame:
        historical = self.historical_minute_bars(symbols, start, end)
        if not include_live:
            return historical
        if as_of is None:
            raise ValueError("include_live=True 必须显式提供 as_of")
        extended_history = historical.with_columns(
            pl.lit(True).alias("finalized"),
            pl.lit(False).alias("partial"),
        )
        live = [
            row for row in self.realtime_minute_bars(
                symbols, as_of=as_of, include_current=include_current
            )
            if start <= row.ts <= end and row.visible_at <= as_of
        ]
        if not live:
            return extended_history
        frame = pl.DataFrame([{
            "symbol": row.symbol, "ts": row.ts, "open": row.open,
            "high": row.high, "low": row.low, "close": row.close,
            "volume": row.volume, "amount": row.amount, "source": row.source,
            "visible_at": row.visible_at,
            "finalized": row.finalized, "partial": row.partial,
        } for row in live]).select([
            *schema.BAR_MINUTE_1M.names, "finalized", "partial",
        ])
        return pl.concat([extended_history, frame], how="vertical_relaxed").unique(
            subset=["symbol", "ts"], keep="last"
        ).sort(["symbol", "ts"])


def facade_reconciliation(
    query: WarehouseQuery, start: date, end: date
) -> dict[str, Reconciliation]:
    """迁移证据：把旧生产门面规范化后与阶段 8 历史门面逐键逐值比较。"""
    old_daily = query.daily_bars(None, start, end)
    new_daily = query.bar_daily(None, start, end).select(old_daily.columns)
    lo, hi = datetime.combine(start, datetime.min.time()), datetime.combine(
        end, datetime.max.time()
    )
    old_minute = query.minute_bars(None, lo, hi).with_columns(
        pl.col("volume").cast(pl.Float64)
    )
    new_minute = query.bar_minute_1m(None, lo, hi).rename({"ts": "dt"}).select(
        old_minute.columns
    )
    return {
        "daily": reconcile_frames(old_daily, new_daily, ("symbol", "dt")),
        "minute": reconcile_frames(old_minute, new_minute, ("symbol", "dt")),
    }
