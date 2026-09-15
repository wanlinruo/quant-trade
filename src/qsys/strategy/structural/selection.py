"""选股组装：候选池 -> 入场判定 -> S6 分类 -> S7 Gate。

**路由顺序照抄原文 L1152-L1190**：先判 LOW，命中即 `continue`，**不回落
去判 HIGH**。原作者的注释说得很清楚——两者开盘区间本就互斥，显式
continue 用于固定生命周期语义。`LOW_MAIN` 的竞价量过滤没过时同样不回落。

**WEAK 分支不实现**：V1.2 在 S7 Gate 之前把整个 WEAK 家族删掉了
（原文 L961 `_v12_keep_non_weak`），炸板池因此无人使用。

本模块只产出「哪天选中哪些票」，不下单、不持仓——选股是离散的，
错一只就看得出来，先把这一段对准再谈净值。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time

import polars as pl
import structlog

from qsys.data.feed import Adjustment, HistoricalFeed
from qsys.data.market_feed import MarketFeed
from qsys.data.query import WarehouseQuery
from qsys.strategy.structural.entry import check_high_open, check_low_open
from qsys.strategy.structural.gate import classify, gate_allow
from qsys.strategy.structural.params import (
    LEFT_PRESSURE_HISTORY_DAYS,
    LOW_MAIN_AUCTION_VOL_RATIO_MIN,
)
from qsys.strategy.structural.universe import candidate_pool, strategy_tag

log = structlog.get_logger(__name__)

# 选股发生在开盘时刻：竞价已结算，当日 open 已确定
SELECTION_AT = time(9, 30)

# 取历史时向前多留的日历日。101 个交易日约合 145 个日历日，留足余量。
_HISTORY_CALENDAR_DAYS = 260
_REQUIRED_HISTORY_COLUMNS = ("open", "high", "low", "close", "volume", "amount")


@dataclass(frozen=True, slots=True)
class Selection:
    dt: date
    symbol: str
    tag: str
    state: str
    reason: str
    allow: bool


class StructuralSelection:
    def __init__(
        self,
        feed: HistoricalFeed,
        market: MarketFeed,
        query: WarehouseQuery,
        *,
        require_auction_volume: bool = True,
    ) -> None:
        self._feed = feed
        self._market = market
        self._query = query
        # 忠实版：拿不到竞价量就剔除。近似版：跳过该条过滤。
        self._require_auction_volume = require_auction_volume
        self._listing: dict[str, date] | None = None
        self.decisions: list[Selection] = []

    def _listing_dates(self) -> dict[str, date]:
        if self._listing is None:
            self._listing = self._query.listing_dates()
        return self._listing

    def select(self, day: date, previous: date) -> list[Selection]:
        """当日通过 Gate 的候选。`decisions` 里留有全部判定（含被拒的）。

        `previous` 是上一个交易日。**生态因子取 previous 那天的**——
        原策略在 `before_trading_start` 里算生态因子（原文 L206-L209），
        用的是 T-1 的数据，只是以 T 为键存下来。我们的表按「因子所描述
        的那一天」存，所以在 T 日要读 T-1 的行。
        """
        as_of = datetime.combine(day, SELECTION_AT)
        market = self._market.bind(as_of)

        pool = candidate_pool(
            market.flags(self._all_symbols(), previous, previous),
            self._listing_dates(),
            day=day,
            previous=previous,
        )
        if not pool.first_boards:
            return []

        opens = market.session_open(pool.first_boards, day)
        auction_volumes = market.auction_volume(pool.first_boards, day)
        history = self._history(pool.first_boards, previous, as_of)
        valuations = self._valuations(market, pool.first_boards, previous)

        picks: list[Selection] = []
        for symbol in pool.first_boards:
            bars = history.get(symbol)
            auction_price = opens.get(symbol)
            if bars is None or bars.height == 0 or auction_price is None:
                continue
            missing = {
                column: bars[column].null_count()
                for column in _REQUIRED_HISTORY_COLUMNS
                if bars[column].null_count()
            }
            if missing:
                # D-9 改为 null 传播后，消费方必须显式跳过并留痕。继续做
                # max/除法会崩；drop_nulls 又会悄悄改变回看窗口，两者都不对。
                log.warning(
                    "selection.history_incomplete",
                    symbol=symbol,
                    rows=bars.height,
                    missing=missing,
                )
                continue

            decided = self._route(
                symbol, day, previous, bars, auction_price,
                auction_volumes.get(symbol), valuations.get(symbol), market,
            )
            if decided is None:
                continue
            self.decisions.append(decided)
            if decided.allow:
                picks.append(decided)
        return picks

    def _route(
        self, symbol, day, previous, bars, auction_price, auction_volume,
        valuation, market,
    ) -> Selection | None:
        """LOW 优先，命中即定；不回落去判 HIGH。"""
        if check_low_open(bars, auction_price):
            tag = strategy_tag("LOW", symbol)
            if tag == "LOW_MAIN" and not self._auction_volume_ok(
                bars, auction_volume
            ):
                # 原文 L1174：LOW 已命中时，附加过滤失败也不回落去查 HIGH
                return None
            factors = _stock_factors(bars) if tag == "LOW_MAIN" else {}
            return self._gate(day, previous, symbol, tag, market, factors)

        if check_high_open(
            bars, auction_price, auction_volume, valuation,
            require_auction_volume=self._require_auction_volume,
        ):
            return self._gate(
                day, previous, symbol, strategy_tag("HIGH", symbol), market, {}
            )
        return None

    def _auction_volume_ok(self, bars: pl.DataFrame, auction_volume) -> bool:
        prev_volume = bars["volume"].to_list()[-1]
        if auction_volume is None:
            # 近似版：无分钟线时跳过该条过滤，差异在对账时单独归因
            return not self._require_auction_volume
        if prev_volume <= 0:
            return False
        return auction_volume / prev_volume >= LOW_MAIN_AUCTION_VOL_RATIO_MIN

    def _gate(self, day, previous, symbol, tag, market, factors) -> Selection:
        env = market.regime(previous, previous)
        row = env.row(0, named=True) if env.height else {}
        cls = classify(tag, row, factors)
        return Selection(
            day, symbol, tag, cls.state, cls.reason, gate_allow(tag, cls)
        )

    def _all_symbols(self) -> list[str]:
        return list(self._listing_dates())

    def _history(
        self, symbols: Sequence[str], through: date, as_of: datetime
    ) -> dict[str, pl.DataFrame]:
        """入场判定要的历史日线，**前复权**，锚在 as_of 可见的最后一根。

        入场的每一条都在跨日比价比量：`check_left_pressure` 拿今天的高点
        比 100 根前的高点，`check_low_open` 算 60 日相对位置。中间发生
        送转，不复权就完全不可比。

        实测 301379.SZ @ 2026-07-06：2026-06-05 是 10 送 4 派 0.2，不复权
        时 2026-05-20 的 high 是 42.30、复权后是 30.05。前者高于当日
        prev_high 32.16，后者低于——`matched_i` 从 21 变成 None，
        `zyts` 从 25 变成 105，整个左压窗口都不是同一段。

        前复权而非后复权：锚在最近一根，阈值（成交额 5.5 亿等）才与当下
        的价格量纲一致。
        """
        start = date.fromordinal(max(through.toordinal() - _HISTORY_CALENDAR_DAYS, 1))
        frame = self._feed.get(
            list(symbols), start, through, as_of=as_of,
            adjustment=Adjustment.FORWARD,
        )
        if frame.height == 0:
            return {}
        return {
            key[0] if isinstance(key, tuple) else key: group.sort("dt").tail(
                LEFT_PRESSURE_HISTORY_DAYS
            )
            for key, group in frame.group_by("symbol")
        }

    def _valuations(self, market, symbols, previous) -> dict[str, dict]:
        frame = market.valuation(list(symbols), previous, previous)
        return {
            row["symbol"]: {
                "market_cap": row["market_cap"],
                "circulating_market_cap": row["circulating_market_cap"],
            }
            for row in frame.iter_rows(named=True)
        }


def _stock_factors(bars: pl.DataFrame) -> dict[str, float | None]:
    """LOW_MAIN 的 S6 个股因子（原文 L2508、L2515）。"""
    closes = bars["close"].to_list()
    highs = bars["high"].to_list()
    factors: dict[str, float | None] = {
        "stock_return_10d": None,
        "distance_to_60d_high": None,
    }
    if len(closes) >= 11 and closes[-11]:
        factors["stock_return_10d"] = closes[-1] / closes[-11] - 1.0
    if len(highs) >= 60:
        peak = max(highs[-60:])
        if peak:
            factors["distance_to_60d_high"] = closes[-1] / peak - 1.0
    return factors
