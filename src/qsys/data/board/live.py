"""打板看板盘中状态机（#648 P2）：逐份 15 秒全市场快照派生封板/开板/题材事件。

口径（设计 §4.3）：
- 涨停价只认直取 stk_limit；无直取价或为"无涨跌幅限制"哨兵值时不判封板，计入"缺涨停价"。
- 封板 = 现价等于涨停价、卖一量为 0、买一价等于涨停价且买一量 > 0。买一量为 0 时无法
  区分"字段缺失"与"真实为 0"（#647），记"封板未知"，不计入封板、首封、晋级。
- 首封时刻取该快照的行情时间 ``MarketQuote.at``（观测值，页面标"≈"）；缺口内不插值。
- 开板次数 = 观测序列中"封 → 非封"次数；"封板未知"不算开板，也不算回封。
- 涨停家数 = 当日截至此刻封过板的家数，只增不减（ST 计入，北交所不计入题材家数）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from qsys.data.board.assign import OTHER, assign, concept_counts
from qsys.data.board.concepts import ConceptSnapshot
from qsys.data.board.ladder import today_height
from qsys.live.messages import MarketQuote

RUSH_PCT = 0.07
GAP_SECONDS = 45.0
_EPS = 1e-6
# Tushare stk_limit 对无涨跌幅限制标的的哨兵值（与 paper/quotes.py 的核对一致）。
_UNBOUNDED_UP = (1_000_000.0, 999_999.999)

SEALED, OPEN, UNKNOWN, BELOW, NO_LIMIT = "sealed", "open", "unknown", "below", "no_limit"


def limit_up_price(limits: Mapping[str, tuple[float, float]], symbol: str) -> float | None:
    pair = limits.get(symbol)
    if pair is None or pair[0] is None:
        return None
    up = float(pair[0])
    if any(abs(up - s) < 1e-3 for s in _UNBOUNDED_UP) or up <= 0:
        return None
    return up


def limit_down_price(limits: Mapping[str, tuple[float, float]], symbol: str) -> float | None:
    pair = limits.get(symbol)
    if pair is None or pair[1] is None or limit_up_price(limits, symbol) is None:
        return None
    down = float(pair[1])
    return down if down > 0.01 + _EPS else None


def seal_state(quote: MarketQuote, up: float | None) -> str:
    if up is None:
        return NO_LIMIT
    if quote.suspended or abs(quote.price - up) > _EPS:
        return BELOW
    if quote.ask1_volume > 0:
        return OPEN
    if quote.bid1_volume > 0 and abs(quote.bid1 - up) <= _EPS:
        return SEALED
    return UNKNOWN


@dataclass
class Tracked:
    symbol: str
    entered_at: datetime
    reason: str  # touch | rush
    first_seal: datetime | None = None
    open_times: int = 0
    state: str = BELOW
    column: str | None = None
    ever_sealed: bool = False
    touched: bool = False


@dataclass(frozen=True)
class Event:
    at: datetime
    symbol: str
    kind: str  # enter | seal | open | reseal | reassign | gap
    detail: str = ""


@dataclass(frozen=True)
class Sentiment:
    published_at: datetime
    quotes: int
    sealed: int
    limit_down: int
    touched: int
    broken: int
    seal_rate: float | None
    max_height: int | None
    promote_1_2: tuple[int | None, int | None]
    promote_2_3: tuple[int | None, int | None]
    promote_3_up: tuple[int | None, int | None]
    rushing: int
    missing_limit: int
    seal_unknown: int


@dataclass
class BoardDay:
    day: date
    limits: Mapping[str, tuple[float, float]]
    # None = 昨日连板数据整体不可用（limit_list_d 未入库等）：全体高度与晋级未知。
    prev_heights: Mapping[str, int | None] | None
    concepts: ConceptSnapshot | None
    tracked: dict[str, Tracked] = field(default_factory=dict)
    last_published: datetime | None = None
    ever_sealed: set[str] = field(default_factory=set)

    def ingest(self, published_at: datetime,
               quotes: Mapping[str, MarketQuote]) -> tuple[list[Event], Sentiment] | None:
        """处理一份快照；重复或更旧的 ``published_at`` 返回 None（按发布时间去重）。"""
        if published_at.date() != self.day:
            return None
        if self.last_published is not None and published_at <= self.last_published:
            return None
        events: list[Event] = []
        if (self.last_published is not None
                and published_at - self.last_published > timedelta(seconds=GAP_SECONDS)):
            events.append(Event(published_at, "", "gap",
                                f"{self.last_published:%H:%M:%S}→{published_at:%H:%M:%S}"))
        self.last_published = published_at
        states: dict[str, str] = {}
        rushing = missing = limit_down = 0
        for symbol, quote in quotes.items():
            if quote.at.date() != self.day:
                continue  # 非当日行情（停牌残留等）不参与
            up = limit_up_price(self.limits, symbol)
            state = seal_state(quote, up)
            states[symbol] = state
            if state == NO_LIMIT:
                missing += 1
                continue
            down = limit_down_price(self.limits, symbol)
            if down is not None and abs(quote.price - down) <= _EPS and not quote.suspended:
                limit_down += 1
            touched = quote.high >= up - _EPS
            pct = (quote.price / quote.prev_close - 1) if quote.prev_close > 0 else None
            item = self.tracked.get(symbol)
            if item is None and (touched or (pct is not None and pct >= RUSH_PCT)):
                item = Tracked(symbol, quote.at, "touch" if touched else "rush")
                self.tracked[symbol] = item
                events.append(Event(quote.at, symbol, "enter", item.reason))
            if item is None:
                continue
            item.touched = item.touched or touched
            if not touched and state == BELOW and pct is not None and pct >= RUSH_PCT:
                rushing += 1
            events.extend(self._transition(item, state, quote.at))
        events.extend(self._assign_columns(published_at))
        return events, self._sentiment(published_at, len(states), states,
                                       rushing, missing, limit_down)

    def _transition(self, item: Tracked, state: str, at: datetime) -> list[Event]:
        out: list[Event] = []
        if state == SEALED and item.state != SEALED:
            if item.first_seal is None:
                item.first_seal = at
                self.ever_sealed.add(item.symbol)
                item.ever_sealed = True
                nth = sum(1 for s in self.ever_sealed if not s.endswith(".BJ"))
                out.append(Event(at, item.symbol, "seal", f"第{nth}家"))
            else:
                out.append(Event(at, item.symbol, "reseal"))
        elif item.state == SEALED and state in (OPEN, BELOW):
            item.open_times += 1
            out.append(Event(at, item.symbol, "open"))
        # 封板未知不改变"封"状态：既不算开板，也避免随后被当作回封。
        if not (state == UNKNOWN and item.state == SEALED):
            item.state = state
        return out

    def _assign_columns(self, at: datetime) -> list[Event]:
        if self.concepts is None:
            return []
        counts = concept_counts(self.ever_sealed, self.concepts)
        out = []
        for item in self.tracked.values():
            if item.symbol.endswith(".BJ"):
                item.column = OTHER if item.column is None else item.column
                continue
            column = assign(item.symbol, counts, self.concepts, item.column)
            if item.column is not None and column != item.column:
                out.append(Event(at, item.symbol, "reassign", f"{item.column}→{column}"))
            item.column = column
        return out

    def height(self, symbol: str) -> int | None:
        item = self.tracked.get(symbol)
        sealed = item is not None and item.state == SEALED
        prev = None if self.prev_heights is None else self.prev_heights.get(symbol)
        return today_height(prev, sealed)

    def _sentiment(self, at: datetime, quotes: int, states: Mapping[str, str],
                   rushing: int, missing: int, limit_down: int) -> Sentiment:
        sealed_now = {s for s, st in states.items() if st == SEALED}
        touched = sum(1 for t in self.tracked.values() if t.touched)
        broken = sum(1 for t in self.tracked.values()
                     if t.ever_sealed and states.get(t.symbol) in (OPEN, BELOW))
        heights = [h for s in sealed_now if (h := self.height(s)) is not None]

        def promote(levels: range) -> tuple[int | None, int | None]:
            if self.prev_heights is None:
                return None, None
            group = [s for s, h in self.prev_heights.items() if h is not None and h in levels]
            return len(group), sum(1 for s in group if s in sealed_now)

        return Sentiment(
            published_at=at, quotes=quotes, sealed=len(sealed_now), limit_down=limit_down,
            touched=touched, broken=broken,
            seal_rate=(len(sealed_now) / touched) if touched else None,
            max_height=max(heights) if heights else None,
            promote_1_2=promote(range(1, 2)), promote_2_3=promote(range(2, 3)),
            promote_3_up=promote(range(3, 1000)),
            rushing=rushing, missing_limit=missing,
            seal_unknown=sum(1 for st in states.values() if st == UNKNOWN),
        )
