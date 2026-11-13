"""打板看板只读视图（#648 P3）。

只用 SQLite ``mode=ro`` 读取采集器 ``limit-up-board`` 的状态目录（``board.sqlite3`` 与
当日临时全量 ``raw/YYYYMMDD.sqlite3``），每次请求新开连接并确定性关闭（#529 / #539）。

回放到任意快照时刻 T 只用 T 及之前的已存记录：情绪栏取 T 时的快照聚合；入选、首封、
开板、回封取 T 及之前的事件；归列取当前归列再按 T 之后的改列事件倒推；状态取 T 时的
报价按同一封板口径判定。任何拿不到的值显示未知，不补值。
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing, contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel

from qsys.data.board.assign import OTHER, concept_counts
from qsys.data.board.concepts import ConceptSnapshot
from qsys.data.board.ladder import today_height
from qsys.data.board.live import SEALED, UNKNOWN, limit_down_price, limit_up_price, seal_state
from qsys.live.messages import MarketQuote

log = structlog.get_logger(__name__)

Mode = Literal["live", "final", "history"]
CardStatus = Literal["sealed", "unknown", "broken", "touched", "rush"]
EVENT_LIMIT = 300
QUOTE_FIELDS = ("symbol", "published_at", "at", "price", "high", "prev_close", "volume",
                "bid1", "bid1_volume", "ask1_volume")


class Sentiment(BaseModel):
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


class Card(BaseModel):
    symbol: str
    name: str
    status: CardStatus
    column: str | None
    height: int | None  # 今日高度：封板时 = 昨日连板数 + 1；未知为 None
    prev_height: int | None
    first_seal: datetime | None
    open_times: int
    resealed: bool
    pct: float | None
    is_st: bool | None  # 当日 ST 名单未发布时 None
    is_bj: bool
    entered_at: datetime
    reason: str
    diffs: dict[str, str] = {}  # 字段 → 直取值（盘后对账差异）


class Column(BaseModel):
    code: str
    name: str
    sealed_count: int | None  # 当日截至 T 封过板的家数（只增不减）；"其他"/"全部"为 None
    first_seal: datetime | None
    gradient: dict[str, int]  # 高度 → 家数（列内当前封板票）


class LadderRow(BaseModel):
    key: str  # "5" … "1" | "unknown" | "other"
    label: str
    yesterday: int | None  # 昨日 N-1 板家数（N≥2 的行）
    cells: dict[str, list[str]]  # 列 code → 卡片 symbol


class CompareItem(BaseModel):
    symbol: str
    name: str
    pct: float | None
    column: str | None
    status: Literal["promoted", "broken", "rush", "limit_down", "unknown", "missed"]
    first_seal: datetime | None
    open_times: int | None
    prediction: float | None  # AI 影子 p_close_limit；无预测 None


class CompareGroup(BaseModel):
    level: int
    items: list[CompareItem]
    promoted: int


class ShadowInfo(BaseModel):
    state: str
    message: str
    prediction_date: date | None = None
    model_version: str | None = None
    precision_at_20: float | None = None
    scored: int = 0
    hits: int | None = None  # 昨日有预测的连板票中今日已晋级的家数（盘中为截至 T）


class TimelineEvent(BaseModel):
    at: datetime
    symbol: str
    name: str
    kind: str
    detail: str


class ConceptCurve(BaseModel):
    code: str
    name: str
    points: list[tuple[datetime, int]]


class BoardView(BaseModel):
    available: bool
    message: str
    day: date | None = None
    days: list[date] = []
    mode: Mode | None = None
    at: datetime | None = None
    times: list[datetime] = []
    status: dict = {}
    meta: dict = {}
    sentiment: Sentiment | None = None
    columns: list[Column] = []
    rows: list[LadderRow] = []
    cards: dict[str, Card] = {}
    compare: list[CompareGroup] = []
    compare_unknown: int = 0
    shadow: ShadowInfo | None = None
    timeline: list[TimelineEvent] = []
    curves: list[ConceptCurve] = []
    mismatch_count: int = 0


class SeriesPoint(BaseModel):
    at: datetime
    price: float
    volume: int | None  # 相邻快照累计成交量之差；首点或回退为 None


class SeriesView(BaseModel):
    available: bool
    message: str
    symbol: str
    name: str = ""
    limit_up: float | None = None
    prev_close: float | None = None
    first_seal: datetime | None = None
    points: list[SeriesPoint] = []


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class LimitUpBoardReader:
    def __init__(self, root: Path, *, names: Callable[[str, date], str] | None = None,
                 shadow=None, today: Callable[[], date] = date.today) -> None:
        self.root = Path(root)
        self.path = self.root / "board.sqlite3"
        self.names = names
        self.shadow = shadow
        self.today = today

    # ---- 连接 ----
    @contextmanager
    def _ro(self, path: Path) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)) as con:
            con.execute("PRAGMA query_only=ON")
            yield con

    def _raw_path(self, day: date) -> Path:
        return self.root / "raw" / f"{day:%Y%m%d}.sqlite3"

    def _name(self, symbol: str, day: date) -> str:
        if self.names is None:
            return ""
        try:
            return self.names(symbol, day)
        except Exception as exc:  # 名称只是展示辅助，取不到就留空
            log.info("limit_up_board.name_unavailable", error_type=type(exc).__name__)
            return ""

    def _status(self) -> dict:
        try:
            return json.loads((self.root / "status.json").read_text())
        except (OSError, ValueError):
            return {}

    # ---- 主视图 ----
    def view(self, day: date | None = None, at: datetime | None = None) -> BoardView:
        if not self.path.exists():
            return BoardView(available=False, message="打板看板采集器尚未产生数据",
                             status=self._status())
        try:
            with self._ro(self.path) as con:
                return self._view(con, day, at)
        except (OSError, sqlite3.Error, ValueError) as exc:
            log.warning("limit_up_board.read_failed", error_type=type(exc).__name__,
                        error=str(exc), exc_info=True)
            return BoardView(available=False, message=f"打板看板不可用：{type(exc).__name__}",
                             status=self._status())

    def _view(self, con: sqlite3.Connection, day: date | None,
              at: datetime | None) -> BoardView:
        days = [date.fromisoformat(r[0]) for r in
                con.execute("SELECT day FROM day_meta ORDER BY day DESC")]
        status = self._status()
        if not days:
            return BoardView(available=False, message="尚无任何交易日的看板记录", status=status)
        day = day or days[0]
        if day not in days:
            return BoardView(available=False, day=day, days=days, status=status,
                             message="该交易日无看板留存（上线前的历史日不分题材，见 P1 汇总）")
        d = day.isoformat()
        meta_row = con.execute(
            "SELECT prev_day, concept_fetched_at, concept_sha, concept_error, st_published, "
            "heights_error, compacted_at, compact_error, reconciled_at, reconcile_error "
            "FROM day_meta WHERE day=?", (d,)).fetchone()
        meta = dict(zip(("prev_day", "concept_fetched_at", "concept_sha", "concept_error",
                         "st_published", "heights_error", "compacted_at", "compact_error",
                         "reconciled_at", "reconcile_error"), meta_row, strict=True))
        meta["has_raw"] = self._raw_path(day).exists()
        reconciled = bool(meta["reconciled_at"]) and meta["reconcile_error"] is None
        mode: Mode = "history" if day < self.today() else ("final" if reconciled else "live")

        times = [datetime.fromisoformat(r[0]) for r in con.execute(
            "SELECT published_at FROM snapshots WHERE day=? ORDER BY published_at", (d,))]
        base = BoardView(available=True, message="", day=day, days=days, mode=mode,
                         times=times, status=status, meta=meta)
        if not times:
            base.message = "盘前已准备，等待首份盘中快照"
            return base
        chosen = max((t for t in times if at is None or t <= at), default=times[0])
        base.at = chosen
        c = chosen.isoformat()
        srow = con.execute("SELECT * FROM snapshots WHERE day=? AND published_at=?",
                           (d, c)).fetchone()
        base.sentiment = Sentiment(
            published_at=chosen, quotes=srow[2], sealed=srow[3], limit_down=srow[4],
            touched=srow[5], broken=srow[6], seal_rate=srow[7], max_height=srow[8],
            promote_1_2=(srow[9], srow[10]), promote_2_3=(srow[11], srow[12]),
            promote_3_up=(srow[13], srow[14]), rushing=srow[15], missing_limit=srow[16],
            seal_unknown=srow[17])

        # 当日输入
        heights_ok = meta["heights_error"] is None
        prev = {s: h for s, h in con.execute(
            "SELECT symbol, height FROM prev_heights WHERE day=?", (d,))} if heights_ok else {}
        st = ({r[0] for r in con.execute("SELECT symbol FROM st_symbols WHERE day=?", (d,))}
              if meta["st_published"] else None)

        events = con.execute(
            "SELECT published_at, at, symbol, kind, detail FROM events WHERE day=? "
            "AND published_at<=? ORDER BY seq", (d, c)).fetchall()
        entered: dict[str, tuple[datetime, str]] = {}
        first_seal: dict[str, datetime] = {}
        opens: Counter[str] = Counter()
        resealed: set[str] = set()
        for _, ev_at, symbol, kind, detail in events:
            if kind == "enter":
                entered[symbol] = (datetime.fromisoformat(ev_at), detail)
            elif kind == "seal":
                first_seal[symbol] = datetime.fromisoformat(ev_at)
            elif kind == "open":
                opens[symbol] += 1
            elif kind == "reseal":
                resealed.add(symbol)

        yesterday = {s: h for s, h in prev.items() if h is not None and h >= 1}
        need = sorted(set(entered) | set(yesterday))
        limits = self._limits(con, d, need)
        quotes = self._quotes(con, day, chosen, need)

        concepts = self._concepts(con, d, day, meta, sorted(entered))
        names = {s: self._name(s, day) for s in need}
        columns_at = self._columns_at(con, d, c, sorted(entered), concepts)

        cards: dict[str, Card] = {}
        for symbol, (entered_at, reason) in entered.items():
            quote = quotes.get(symbol)
            up = limit_up_price(limits, symbol)
            state = seal_state(quote, up) if quote is not None else None
            sealed = state == SEALED
            if sealed:
                status: CardStatus = "sealed"
            elif state == UNKNOWN:
                status = "unknown"
            elif symbol in first_seal:
                status = "broken"
            elif quote is not None and up is not None and quote.high >= up - 1e-6:
                status = "touched"
            else:
                status = "rush"
            prev_h = prev.get(symbol) if heights_ok else None
            cards[symbol] = Card(
                symbol=symbol, name=names.get(symbol, ""), status=status,
                column=columns_at.get(symbol),
                height=(today_height(prev_h, True) if heights_ok else None) if sealed else None,
                prev_height=prev_h, first_seal=first_seal.get(symbol),
                open_times=opens[symbol], resealed=symbol in resealed,
                pct=_pct(quote), is_st=None if st is None else symbol in st,
                is_bj=symbol.endswith(".BJ"), entered_at=entered_at, reason=reason)

        if reconciled:
            base.mismatch_count = self._apply_mismatches(con, d, cards)
        base.cards = cards
        base.columns = self._columns(cards, concepts, first_seal, meta)
        base.rows = self._rows(cards, base.columns, prev, heights_ok)
        base.compare, base.compare_unknown = self._compare(
            yesterday, prev, heights_ok, cards, quotes, limits, names, first_seal, opens)
        base.shadow = self._shadow(day, meta, mode, base.compare)
        base.timeline = [
            TimelineEvent(at=datetime.fromisoformat(ev_at), symbol=symbol,
                          name=names.get(symbol, "") if symbol else "", kind=kind,
                          detail=self._detail(kind, detail, concepts))
            for _, ev_at, symbol, kind, detail in reversed(events)
            if kind != "enter"][:EVENT_LIMIT]
        base.curves = self._curves(events, concepts, base.columns)
        base.message = {"live": "盘中观测（≈ 为快照观测值）",
                        "final": "盘后定版：已与直取 limit_list_d 对账",
                        "history": "历史日期"}[mode]
        return base

    # ---- 读取辅助 ----
    @staticmethod
    def _in(symbols: list[str]) -> str:
        return ",".join("?" * len(symbols))

    def _limits(self, con, d: str, symbols: list[str]) -> dict[str, tuple[float, float]]:
        if not symbols:
            return {}
        return {s: (up, down) for s, up, down in con.execute(
            f"SELECT symbol, up, down FROM limits WHERE day=? AND symbol IN ({self._in(symbols)})",
            (d, *symbols))}

    def _quotes(self, con, day: date, chosen: datetime,
                symbols: list[str]) -> dict[str, MarketQuote]:
        """T 时刻（≤T 的最近一份）报价：有临时全量读全量，否则读压缩后的序列。"""
        if not symbols:
            return {}
        c = chosen.isoformat()
        cols = ", ".join(QUOTE_FIELDS)
        raw = self._raw_path(day)
        if raw.exists():
            sql = (f"SELECT {cols} FROM raw r WHERE symbol IN ({self._in(symbols)}) AND "
                   "published_at=(SELECT MAX(published_at) FROM raw WHERE symbol=r.symbol "
                   "AND published_at<=?)")
            with self._ro(raw) as src:
                rows = src.execute(sql, (*symbols, c)).fetchall()
        else:
            sql = (f"SELECT {cols} FROM series s WHERE day=? AND symbol IN "
                   f"({self._in(symbols)}) AND published_at=(SELECT MAX(published_at) FROM "
                   "series WHERE day=s.day AND symbol=s.symbol AND published_at<=?)")
            rows = con.execute(sql, (day.isoformat(), *symbols, c)).fetchall()
        out = {}
        for r in rows:
            data = dict(zip(QUOTE_FIELDS, r, strict=True))
            if datetime.fromisoformat(data["at"]).date() != day:
                continue  # 非当日行情不参与（与采集器一致）
            out[data["symbol"]] = MarketQuote(
                symbol=data["symbol"], at=data["at"], price=data["price"], open=0.0,
                high=data["high"], low=0.0, prev_close=data["prev_close"],
                volume=data["volume"], bid1=data["bid1"], bid1_volume=data["bid1_volume"],
                ask1_volume=data["ask1_volume"])
        return out

    def _concepts(self, con, d: str, day: date, meta: Mapping,
                  symbols: list[str]) -> tuple[ConceptSnapshot, dict[str, str]] | None:
        if meta["concept_sha"] is None:
            return None
        names = {code: name or code for code, name in
                 con.execute("SELECT code, name FROM concepts WHERE day=?", (d,))}
        members: dict[str, set[str]] = defaultdict(set)
        if symbols:
            for code, symbol in con.execute(
                    "SELECT code, symbol FROM concept_members WHERE day=? AND symbol IN "
                    f"({self._in(symbols)})", (d, *symbols)):
                members[code].add(symbol)
        snap = ConceptSnapshot(day, datetime.fromisoformat(meta["concept_fetched_at"]),
                               {code: frozenset(m) for code, m in members.items()}, {},
                               meta["concept_sha"])
        return snap, names

    def _columns_at(self, con, d: str, c: str, symbols: list[str],
                    concepts) -> dict[str, str | None]:
        if concepts is None:
            return dict.fromkeys(symbols)
        current = {s: col for s, col in con.execute(
            "SELECT symbol, concept FROM board WHERE day=?", (d,))}
        out = {s: current.get(s) for s in symbols}
        # T 之后的改列按时间倒序回退："旧→新" 取旧。
        later = con.execute(
            "SELECT symbol, detail FROM events WHERE day=? AND kind='reassign' "
            "AND published_at>? ORDER BY seq DESC", (d, c)).fetchall()
        for symbol, detail in later:
            if symbol in out and "→" in detail:
                out[symbol] = detail.split("→", 1)[0]
        return out

    @staticmethod
    def _apply_mismatches(con, d: str, cards: dict[str, Card]) -> int:
        rows = con.execute("SELECT symbol, field, direct FROM mismatches WHERE day=?",
                           (d,)).fetchall()
        for symbol, field, direct in rows:
            card = cards.get(symbol)
            if card is not None:
                card.diffs[field] = json.loads(direct) if direct else "null"
        return len(rows)

    @staticmethod
    def _columns(cards: Mapping[str, Card], concepts, first_seal: Mapping[str, datetime],
                 meta: Mapping) -> list[Column]:
        if concepts is None:
            label = "全部" if meta["concept_error"] is None else "全部（题材不可用）"
            return [Column(code="", name=label, sealed_count=None,
                           first_seal=min(first_seal.values(), default=None),
                           gradient=_gradient(cards.values()))]
        snap, names = concepts
        counts = concept_counts(set(first_seal), snap)
        used = {c.column for c in cards.values() if c.column}
        cols = []
        for code in used:
            members = [c for c in cards.values() if c.column == code]
            seals = [first_seal[c.symbol] for c in members if c.symbol in first_seal]
            cols.append(Column(
                code=code, name=OTHER if code == OTHER else names.get(code, code),
                sealed_count=None if code == OTHER else counts.get(code, 0),
                first_seal=min(seals, default=None), gradient=_gradient(members)))
        return sorted(cols, key=lambda col: (col.code == OTHER, -(col.sealed_count or 0),
                                             col.code))

    @staticmethod
    def _rows(cards: Mapping[str, Card], columns: list[Column], prev: Mapping,
              heights_ok: bool) -> list[LadderRow]:
        sealed = [c for c in cards.values() if c.status == "sealed"]
        top = max((c.height for c in sealed if c.height is not None), default=1)
        rows: list[LadderRow] = []

        def cells(pick) -> dict[str, list[str]]:
            out: dict[str, list[str]] = {col.code: [] for col in columns}
            for card in sorted(cards.values(), key=_card_order):
                if pick(card):
                    out.setdefault(card.column or "", []).append(card.symbol)
            return out

        for level in range(max(top, 1), 0, -1):
            yesterday = (sum(1 for h in prev.values() if h == level - 1)
                         if heights_ok and level >= 2 else None)
            rows.append(LadderRow(key=str(level), label="首板" if level == 1 else f"{level} 板",
                                  yesterday=yesterday,
                                  cells=cells(lambda c, lv=level: c.status == "sealed"
                                              and c.height == lv)))
        if any(c.height is None for c in sealed):
            rows.append(LadderRow(key="unknown", label="高度未知", yesterday=None,
                                  cells=cells(lambda c: c.status == "sealed"
                                              and c.height is None)))
        rows.append(LadderRow(key="other", label="炸板/冲板", yesterday=None,
                              cells=cells(lambda c: c.status != "sealed")))
        return rows

    @staticmethod
    def _compare(yesterday: Mapping[str, int], prev: Mapping, heights_ok: bool,
                 cards: Mapping[str, Card], quotes: Mapping[str, MarketQuote], limits,
                 names: Mapping[str, str], first_seal, opens) -> tuple[list[CompareGroup], int]:
        if not heights_ok:
            return [], 0
        groups: dict[int, list[CompareItem]] = defaultdict(list)
        for symbol, level in yesterday.items():
            card = cards.get(symbol)
            quote = quotes.get(symbol)
            down = limit_down_price(limits, symbol)
            if card is not None and card.status == "sealed":
                status = "promoted"
            elif card is not None and card.status == "unknown":
                status = "unknown"
            elif card is not None and card.status == "broken":
                status = "broken"
            elif quote is not None and down is not None and abs(quote.price - down) <= 1e-6:
                status = "limit_down"
            elif card is not None:
                status = "rush"
            else:
                status = "missed"
            groups[level].append(CompareItem(
                symbol=symbol, name=names.get(symbol, ""), pct=_pct(quote),
                column=card.column if card else None, status=status,
                first_seal=first_seal.get(symbol), open_times=opens[symbol] if card else None,
                prediction=None))
        order = {"promoted": 0, "unknown": 1, "rush": 2, "broken": 3, "limit_down": 4,
                 "missed": 5}
        out = [CompareGroup(level=level, promoted=sum(i.status == "promoted" for i in items),
                            items=sorted(items, key=lambda i: (order[i.status],
                                                               -(i.pct or -9), i.symbol)))
               for level, items in sorted(groups.items(), reverse=True)]
        unknown = sum(1 for h in prev.values() if h is None)
        return out, unknown

    def _shadow(self, day: date, meta: Mapping, mode: Mode,
                groups: list[CompareGroup]) -> ShadowInfo | None:
        """盘中：昨日对今日的预测；盘后/历史：今日对明日的预测。只读影子榜单，不计算。"""
        if self.shadow is None:
            return None
        prediction_date = (date.fromisoformat(meta["prev_day"]) if meta["prev_day"] else None) \
            if mode == "live" else day
        if prediction_date is None:
            return ShadowInfo(state="unavailable", message="缺前一交易日")
        try:
            view = self.shadow.view(prediction_date)
        except Exception as exc:
            return ShadowInfo(state="unavailable", message=f"影子榜单不可用：{type(exc).__name__}")
        snap = view.snapshot
        if snap is None or view.state not in ("ready", "stale"):
            return ShadowInfo(state=view.state, message=view.message,
                              prediction_date=prediction_date)
        scores = {s.symbol: s.p_close_limit for s in snap.limit_up_scores}
        hits = 0
        for group in groups:
            for item in group.items:
                if mode == "live":
                    item.prediction = scores.get(item.symbol)
                    if item.prediction is not None and item.status == "promoted":
                        hits += 1
                elif item.status == "promoted":
                    item.prediction = scores.get(item.symbol)
        return ShadowInfo(state="ready", message=view.message, prediction_date=prediction_date,
                          model_version=snap.model_version,
                          precision_at_20=snap.rolling_precision_at_20, scored=len(scores),
                          hits=hits if mode == "live" else None)

    @staticmethod
    def _detail(kind: str, detail: str, concepts) -> str:
        if kind == "reassign" and concepts is not None and "→" in detail:
            names = concepts[1]
            old, new = detail.split("→", 1)
            return f"{names.get(old, old)}→{names.get(new, new)}"
        return detail

    @staticmethod
    def _curves(events, concepts, columns: list[Column], top: int = 4) -> list[ConceptCurve]:
        if concepts is None:
            return []
        snap, names = concepts
        picks = [c.code for c in columns if c.code != OTHER][:top]
        counts: Counter[str] = Counter()
        points: dict[str, list[tuple[datetime, int]]] = {code: [] for code in picks}
        for _, ev_at, symbol, kind, _ in events:
            if kind != "seal" or symbol.endswith(".BJ"):
                continue
            at = datetime.fromisoformat(ev_at)
            for code in snap.concepts_of(symbol):
                counts[code] += 1
                if code in points:
                    points[code].append((at, counts[code]))
        return [ConceptCurve(code=code, name=names.get(code, code), points=points[code])
                for code in picks]

    # ---- 分时 ----
    def series(self, day: date, symbol: str) -> SeriesView:
        try:
            with self._ro(self.path) as con:
                d = day.isoformat()
                limit = con.execute("SELECT up, down FROM limits WHERE day=? AND symbol=?",
                                    (d, symbol)).fetchone()
                seal = con.execute("SELECT at FROM events WHERE day=? AND symbol=? AND "
                                   "kind='seal'", (d, symbol)).fetchone()
                raw = self._raw_path(day)
                cols = "published_at, at, price, prev_close, volume"
                if raw.exists():
                    with self._ro(raw) as src:
                        rows = src.execute(f"SELECT {cols} FROM raw WHERE symbol=? "
                                           "ORDER BY published_at", (symbol,)).fetchall()
                else:
                    rows = con.execute(f"SELECT {cols} FROM series WHERE day=? AND symbol=? "
                                       "ORDER BY published_at", (d, symbol)).fetchall()
        except (OSError, sqlite3.Error) as exc:
            return SeriesView(available=False, symbol=symbol,
                              message=f"分时不可用：{type(exc).__name__}")
        rows = [r for r in rows if datetime.fromisoformat(r[1]).date() == day]
        if not rows:
            return SeriesView(available=False, symbol=symbol, name=self._name(symbol, day),
                              message="该票当日无留存分时（未入选且不在昨日涨停名单）")
        points, last = [], None
        for _, at, price, _, volume in rows:
            delta = volume - last if last is not None and volume >= last else None
            points.append(SeriesPoint(at=datetime.fromisoformat(at), price=price, volume=delta))
            last = volume
        up = limit_up_price({symbol: limit}, symbol) if limit else None
        return SeriesView(available=True, message="", symbol=symbol,
                          name=self._name(symbol, day), limit_up=up,
                          prev_close=rows[-1][3] or None,
                          first_seal=_parse(seal[0]) if seal else None, points=points)


def _pct(quote: MarketQuote | None) -> float | None:
    if quote is None or quote.prev_close <= 0:
        return None
    return quote.price / quote.prev_close - 1


def _gradient(cards) -> dict[str, int]:
    counter = Counter("?" if c.height is None else str(c.height)
                      for c in cards if c.status == "sealed")
    return dict(sorted(counter.items(), key=lambda kv: (kv[0] == "?", -int(kv[0])
                                                        if kv[0] != "?" else 0)))


def _card_order(card: Card) -> tuple:
    return (card.first_seal is None, card.first_seal or card.entered_at, card.symbol)
