"""候选池：昨日首板与昨日炸板。

对应原策略 `prepare_stock_list` / `filter_*`（原文 L1349-L1403）。

原策略用 `get_hl_stock(initial, yesterday, 1)` 取昨日涨停，再剔除前日也
涨停的——那正是 `daily_flags.is_first_board` 的定义，2-1a 已实测一致。
炸板对应 `get_ever_hl_stock2`（`close != high_limit and high == high_limit`），
即 `is_broken_board`。

**ST 只认直取名单**：`daily_flags.is_st` 来自 Tushare `stock_st`，是逐日
布尔真值，对应原策略的 `filter_st_paused_stock`（原文 L1397）。

这里曾经用 `limit_pct == 0.05` 反推 ST。**那条路已经彻底断了**：
2026-07-06 起主板 ST 的涨跌幅上限由 5% 改为 10%，与普通股完全相同，
价格档位再也区分不出 ST；何况创业板/科创板 ST 一直是 20%，反推从来
就漏掉了它们。用价格判 ST 是错的，不是「够用的代理」。

注意生态因子池**不**排 ST（原文 `_s5_relevant_universe` 只排科创与北交），
两处口径不同，别混用。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

import polars as pl

from qsys.strategy.structural.params import (
    CHINEXT_PREFIXES,
    EXCLUDED_PREFIX_2,
    EXCLUDED_PREFIXES,
    NEW_STOCK_DAYS,
)


@dataclass(frozen=True, slots=True)
class PoolResult:
    first_boards: list[str]
    broken_boards: list[str]


def board_of(symbol: str) -> str:
    """显式板块制度标签（原文 L576）。"""
    return "CHINEXT" if symbol.startswith(CHINEXT_PREFIXES) else "MAIN"


def strategy_tag(name: str, symbol: str) -> str:
    """原文 L581：'%s_%s' % (strategy_name, classify_board(stock))"""
    return f"{name}_{board_of(symbol)}"


def _tradable(symbol: str) -> bool:
    """filter_kcbj_stock：排除北交所与科创板（原文 L1401）。"""
    code = symbol.split(".")[0]
    return not (
        code.startswith(EXCLUDED_PREFIXES) or code.startswith(EXCLUDED_PREFIX_2)
    )


def candidate_pool(
    flags: pl.DataFrame,
    listed_since: Mapping[str, date],
    day: date,
    previous: date,
) -> PoolResult:
    """昨日首板与昨日炸板，已施加板块与新股过滤。"""
    if flags.height == 0:
        return PoolResult([], [])

    # 原文 filter_new_stock：start_date < date - timedelta(days=50)，
    # 恰好 50 天不算数，故用严格小于
    cutoff = day - timedelta(days=NEW_STOCK_DAYS)

    def keep(symbol: str) -> bool:
        if not _tradable(symbol):
            return False
        listed = listed_since.get(symbol)
        # 不知道上市日就不放行——宁可漏，不可错
        return listed is not None and listed < cutoff

    yesterday = flags.filter(pl.col("dt") == previous)
    # filter_st_paused_stock：ST 读 daily_flags.is_st 这个直取真值。
    # 不知道是不是 ST 就不放行——与下面 keep() 里「不知道上市日就不放行」
    # 同一条规矩。
    if "is_st" in yesterday.columns:
        yesterday = yesterday.filter(~pl.col("is_st").fill_null(True))
    return PoolResult(
        sorted(
            s
            for s in yesterday.filter(pl.col("is_first_board"))["symbol"].to_list()
            if keep(s)
        ),
        sorted(
            s
            for s in yesterday.filter(pl.col("is_broken_board"))["symbol"].to_list()
            if keep(s)
        ),
    )
