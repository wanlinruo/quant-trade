"""连板高度、晋级与今/昨对比（#648）。

非 ST 的昨日连板数取 limit_list_d 直取 limit_times；limit_list_d 不含 ST，ST 用
daily_flags.is_limit_up 按交易日连续计数。任何一步缺数据都返回 None（未知），
不把未知当首板、不跳过缺口。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import date

import polars as pl


def previous_heights(prev_day: date, limit_list: pl.DataFrame, flags: pl.DataFrame,
                     sessions: Sequence[date]) -> dict[str, int | None]:
    day_flags = flags.filter(pl.col("dt") == prev_day)
    day_list = limit_list.filter((pl.col("dt") == prev_day) & (pl.col("limit_type") == "U"))
    list_missing = limit_list.filter(pl.col("dt") == prev_day).height == 0
    times = dict(zip(day_list["symbol"].to_list(), day_list["limit_times"].to_list(), strict=True))
    by_key = {(r["symbol"], r["dt"]): r["is_limit_up"] for r in flags.iter_rows(named=True)}
    history = sorted(d for d in sessions if d <= prev_day)
    out: dict[str, int | None] = {}
    for row in day_flags.iter_rows(named=True):
        symbol = row["symbol"]
        if row["is_st"] is True:
            out[symbol] = _st_streak(symbol, history, by_key)
        elif row["is_st"] is None:
            out[symbol] = None
        elif list_missing:
            out[symbol] = None
        else:
            value = times.get(symbol)
            out[symbol] = int(value) if value is not None else (0 if row["is_limit_up"] is False else None)
    return out


def _st_streak(symbol: str, history: Sequence[date], by_key: Mapping) -> int | None:
    streak = 0
    for day in reversed(history):
        value = by_key.get((symbol, day))
        if value is None:
            return None
        if value is False:
            return streak
        streak += 1
    return None  # 序列起点之前仍在涨停：无法确定起点


def today_height(prev: int | None, sealed: bool) -> int | None:
    if not sealed:
        return 0
    return None if prev is None else prev + 1


@dataclass(frozen=True)
class PromotionRow:
    level: int
    yesterday: tuple[str, ...]
    promoted: tuple[str, ...]


def promotions(prev: Mapping[str, int | None], sealed_today: Set[str]) -> list[PromotionRow]:
    levels = sorted({h for h in prev.values() if h is not None and h >= 1}, reverse=True)
    rows = []
    for level in levels:
        group = tuple(sorted(s for s, h in prev.items() if h == level))
        rows.append(PromotionRow(level, group, tuple(s for s in group if s in sealed_today)))
    return rows
