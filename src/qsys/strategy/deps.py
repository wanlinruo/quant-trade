"""标准 A 股回测的装配：把仓库变成一套可用的数据接口。

运行器与测试都从这里拿依赖，避免同一套接线在多处各写一遍——那正是
「多系统拼接脆弱」的老毛病。
"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

from qsys.core.calendar import AShareCalendar
from qsys.core.types import Symbol
from qsys.data.feed import HistoricalFeed
from qsys.data.market_feed import MarketFeed
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.strategy.loader import StrategyDeps

# 与原策略 filter_kcbj_stock 一致：4/8 是北交所，68 是科创板
EXCLUDED_PREFIXES = ("4", "8", "68")


def build_deps(warehouse_root: Path, params: dict | None = None):
    """返回 (deps, query, calendar, sessions, universe, boards)。"""
    warehouse = Warehouse(Path(warehouse_root))
    query = WarehouseQuery(warehouse)

    sessions: list[date] = sorted(query.market_regime()["dt"].to_list())
    calendar = AShareCalendar(sessions)
    feed = HistoricalFeed(query, calendar)
    market = MarketFeed(query, calendar)

    instruments = query.instruments()
    symbols = instruments["symbol"].to_list()
    universe = [
        Symbol.parse(s) for s in symbols
        if not s.split(".")[0].startswith(EXCLUDED_PREFIXES)
    ]
    boards = dict(zip(symbols, instruments["board"].to_list()))

    deps = StrategyDeps(
        feed=feed, market=market, query=query, calendar=calendar,
        params=dict(params or {}),
    )
    return deps, query, calendar, sessions, universe, boards


def intraday_quotes(market: MarketFeed):
    """引擎要的日内报价回调：(session, at, symbols) -> {symbol: (价, 量)}。

    检查点退出靠它。`as_of` 绑到该时刻，只看得到那一刻及更早的分钟线。
    """

    def quotes(session: date, at: time, symbols):
        bound = market.bind(datetime.combine(session, at))
        got = bound.intraday([str(s) for s in symbols], session, at)
        return {
            s: (got[str(s)].price, got[str(s)].volume)
            for s in symbols
            if str(s) in got
        }

    return quotes
