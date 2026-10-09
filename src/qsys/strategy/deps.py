"""标准 A 股回测的装配：把仓库变成一套可用的数据接口。

运行器与测试都从这里拿依赖，避免同一套接线在多处各写一遍——那正是
「多系统拼接脆弱」的老毛病。
"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import structlog

from qsys.core.calendar import AShareCalendar
from qsys.core.types import Symbol
from qsys.data.cutover import CutoverConfig, CutoverMarketData
from qsys.data.feed import HistoricalFeed
from qsys.data.market_feed import MarketFeed
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.strategy.loader import StrategyDeps

# 旧代码号段兜底；北交所 2023 年后的 920 映射必须靠显式 board/exchange 排除。
EXCLUDED_PREFIXES = ("4", "8", "68")
log = structlog.get_logger(__name__)


class MissingIntradayDataError(ValueError):
    """可交易持仓在退出检查点缺少分钟行情。"""


def build_universe(instruments):
    """只把核心类型能够准确表达的 A 股代码交给回测引擎。"""
    universe: list[Symbol] = []
    boards: dict[str, str] = {}
    invalid: list[str] = []
    for row in instruments.iter_rows(named=True):
        text = str(row["symbol"])
        try:
            symbol = Symbol.parse(text)
        except (TypeError, ValueError):
            invalid.append(text)
            continue
        board = str(row.get("board") or "").lower()
        if board in {"bse", "star"} or symbol.market == "BJ" \
                or symbol.code.startswith(EXCLUDED_PREFIXES):
            continue
        universe.append(symbol)
        boards[text] = row["board"]
    if invalid:
        log.warning("strategy.invalid_instruments_skipped", count=len(invalid),
                    samples=invalid[:10])
    return universe, boards


def build_deps(warehouse_root: Path, params: dict | None = None):
    """返回 (deps, query, calendar, sessions, universe, boards)。"""
    warehouse = Warehouse(Path(warehouse_root))
    query = WarehouseQuery(warehouse)
    market_data = CutoverMarketData(query, CutoverConfig.from_env())

    sessions: list[date] = sorted(market_data.market_regime()["dt"].to_list())
    calendar = AShareCalendar(sessions)
    feed = HistoricalFeed(query, calendar, market_data)
    market = MarketFeed(query, calendar, market_data)

    instruments = market_data.instruments()
    universe, boards = build_universe(instruments)

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
        requested = {str(s) for s in symbols}
        missing = requested - set(got)
        if missing:
            # 没有日线是停牌/尚未上市/已经退市，当时确实无可成交报价；有日线
            # 却没有分钟线则是数据缺口。后者不能继续跑出一条“成功”的净值线，
            # 否则持仓会永久卖不出（3a3d2f19c66c45d1 的实际事故）。
            tradable = set(bound.session_open(sorted(missing), session))
            broken = sorted(missing & tradable)
            if broken:
                sample = ", ".join(broken[:10])
                raise MissingIntradayDataError(
                    f"{session} {at:%H:%M} 可交易持仓缺少分钟行情：{sample}"
                    + (f" 等 {len(broken)} 只" if len(broken) > 10 else "")
                    + "；回测已停止，避免持仓因数据缺口永久无法卖出"
                )
        return {
            s: (got[str(s)].price, got[str(s)].volume)
            for s in symbols
            if str(s) in got
        }

    return quotes
