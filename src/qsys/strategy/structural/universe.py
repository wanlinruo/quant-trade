"""候选池：昨日首板与昨日炸板。

对应原策略 `prepare_stock_list` / `filter_*`（原文 L1349-L1403）。

原策略用 `get_hl_stock(initial, yesterday, 1)` 取昨日涨停，再剔除前日也
涨停的——那正是 `daily_flags.is_first_board` 的定义，2-1a 已实测一致。
炸板对应 `get_ever_hl_stock2`。`daily_flags.is_broken_board` 对非 ST 优先采用
`limit_list_d` 的 Z 分类，避免把“收盘价在涨停价但卖一仍有余量”误当成封板；
ST 因该接口不统计，暂用触板后未收在涨停价的价格口径。

**与原文 `filter_st_paused_stock` 的有意偏离（D-11，用户 2026-09-17 定）**：

- **ST 首板进入候选池**；ST 炸板不进。ST 只认 `daily_flags.is_st`——
  Tushare `stock_st` 的逐日直取真值，取首板当日（T-1）那一行：T 日名单
  要到 T 日 17:00 才同步，开盘前拿不到。炸板 ST 状态未知就不放行。
- **退市整理期**由调用方给出（`delisting`），唯一出处是退市整理期池
  `delisting_pool`（派生自 `namechange` 的 `change_reason='退市整理期'` 区间），
  不从名称里找「退」字。原文的
  `'退' in name` 在 2023 年以来的 51 个首板上与该结构化状态逐只一致。
- 停牌由 `selection` 用 `suspend_daily` 直取剔除。

价格档位判不出 ST：2026-07-06 起主板 ST 与普通股同为 10%，创业板/科创板
ST 一直是 20%。

注意生态因子池**不**排 ST（原文 `_s5_relevant_universe` 只排科创与北交），
两处口径不同，别混用。
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
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
    """排除北交所与科创板；交易所身份优先于历史代码前缀。"""
    code, _, exchange = symbol.partition(".")
    return not (
        exchange.upper() == "BJ"
        or code.startswith(EXCLUDED_PREFIXES)
        or code.startswith(EXCLUDED_PREFIX_2)
    )


def candidate_pool(
    flags: pl.DataFrame,
    listed_since: Mapping[str, date],
    day: date,
    previous: date,
    *,
    delisting: Collection[str],
) -> PoolResult:
    """昨日首板与昨日炸板，已施加板块、新股、ST 炸板与退市整理期过滤。

    `delisting` 是 `day` 当天处于退市整理期、且开盘前已公告的标的
    （`WarehouseQuery.delisting_symbols`，读退市整理期池）。必传：漏传会静默放行。
    """
    if flags.height == 0:
        return PoolResult([], [])

    # 原文 filter_new_stock：start_date < date - timedelta(days=50)，
    # 恰好 50 天不算数，故用严格小于
    cutoff = day - timedelta(days=NEW_STOCK_DAYS)
    excluded = set(delisting)

    def keep(symbol: str) -> bool:
        if not _tradable(symbol) or symbol in excluded:
            return False
        listed = listed_since.get(symbol)
        # 不知道上市日就不放行——宁可漏，不可错
        return listed is not None and listed < cutoff

    yesterday = flags.filter(pl.col("dt") == previous)
    broken = yesterday.filter(pl.col("is_broken_board"))
    # ST 炸板不进池。不知道是不是 ST 就不放行；老分区没有这一列同样视为未知。
    broken = (broken.filter(~pl.col("is_st").fill_null(True))
              if "is_st" in broken.columns else broken.clear())
    return PoolResult(
        sorted(
            s
            for s in yesterday.filter(pl.col("is_first_board"))["symbol"].to_list()
            if keep(s)
        ),
        sorted(s for s in broken["symbol"].to_list() if keep(s)),
    )
