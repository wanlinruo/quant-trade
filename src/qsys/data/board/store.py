"""打板看板存储（#648 P2）。

- 看板库 ``board.sqlite3``：按日留存的派生结果（情绪聚合、事件、入选票、概念快照、对账差异），
  长期保留，供盘中/盘后/历史回放读取。
- 当日临时全量 ``raw/YYYYMMDD.sqlite3``：盘中每份快照的全市场原始报价，保证入选前分时完整。
  收盘后压缩为"入选票全日序列"写入看板库，校验（行数、逐值相等、情绪聚合由全量重放一致）
  通过才删除；失败保留全量并在 meta 记录原因（设计 §6，维护者 2026-11-11 授权，仅限本组件）。

两库均用 ``journal_mode=DELETE``，Web 以 ``mode=ro`` 只读打开（同影子榜单，#529）。
每次操作新开连接并确定性关闭（#539）。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import astuple
from datetime import date, datetime
from pathlib import Path

from qsys.data.board.concepts import ConceptSnapshot
from qsys.data.board.live import BoardDay, Event, Sentiment, Tracked
from qsys.live.messages import MarketQuote

RAW_FIELDS = ("symbol", "at", "price", "open", "high", "low", "prev_close", "volume",
              "amount", "bid1", "ask1", "bid1_volume", "ask1_volume", "suspended")

BOARD_SCHEMA = """
CREATE TABLE IF NOT EXISTS day_meta (
  day TEXT PRIMARY KEY, prepared_at TEXT NOT NULL, prev_day TEXT,
  concept_fetched_at TEXT, concept_sha TEXT, concept_error TEXT,
  st_published INTEGER NOT NULL, heights_error TEXT,
  compacted_at TEXT, compact_error TEXT, reconciled_at TEXT, reconcile_error TEXT);
CREATE TABLE IF NOT EXISTS limits (
  day TEXT NOT NULL, symbol TEXT NOT NULL, up REAL, down REAL, PRIMARY KEY (day, symbol));
CREATE TABLE IF NOT EXISTS prev_heights (
  day TEXT NOT NULL, symbol TEXT NOT NULL, height INTEGER, PRIMARY KEY (day, symbol));
CREATE TABLE IF NOT EXISTS st_symbols (
  day TEXT NOT NULL, symbol TEXT NOT NULL, PRIMARY KEY (day, symbol));
CREATE TABLE IF NOT EXISTS concepts (
  day TEXT NOT NULL, code TEXT NOT NULL, name TEXT, index_count INTEGER,
  member_rows INTEGER NOT NULL, PRIMARY KEY (day, code));
CREATE TABLE IF NOT EXISTS concept_members (
  day TEXT NOT NULL, code TEXT NOT NULL, symbol TEXT NOT NULL,
  PRIMARY KEY (day, code, symbol));
CREATE TABLE IF NOT EXISTS snapshots (
  day TEXT NOT NULL, published_at TEXT NOT NULL, quotes INTEGER NOT NULL,
  sealed INTEGER NOT NULL, limit_down INTEGER NOT NULL, touched INTEGER NOT NULL,
  broken INTEGER NOT NULL, seal_rate REAL, max_height INTEGER,
  p12_from INTEGER, p12_to INTEGER, p23_from INTEGER, p23_to INTEGER,
  p3_from INTEGER, p3_to INTEGER, rushing INTEGER NOT NULL,
  missing_limit INTEGER NOT NULL, seal_unknown INTEGER NOT NULL,
  PRIMARY KEY (day, published_at));
CREATE TABLE IF NOT EXISTS events (
  day TEXT NOT NULL, seq INTEGER NOT NULL, published_at TEXT NOT NULL, at TEXT NOT NULL,
  symbol TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL, PRIMARY KEY (day, seq));
CREATE TABLE IF NOT EXISTS board (
  day TEXT NOT NULL, symbol TEXT NOT NULL, entered_at TEXT NOT NULL, reason TEXT NOT NULL,
  first_seal TEXT, open_times INTEGER NOT NULL, state TEXT NOT NULL, concept TEXT,
  ever_sealed INTEGER NOT NULL, touched INTEGER NOT NULL, PRIMARY KEY (day, symbol));
CREATE TABLE IF NOT EXISTS series (
  day TEXT NOT NULL, symbol TEXT NOT NULL, published_at TEXT NOT NULL, at TEXT NOT NULL,
  price REAL NOT NULL, high REAL NOT NULL, volume INTEGER NOT NULL, amount REAL NOT NULL,
  bid1 REAL NOT NULL, bid1_volume INTEGER NOT NULL, ask1_volume INTEGER NOT NULL,
  PRIMARY KEY (day, symbol, published_at));
CREATE TABLE IF NOT EXISTS mismatches (
  day TEXT NOT NULL, symbol TEXT NOT NULL, field TEXT NOT NULL,
  observed TEXT, direct TEXT, PRIMARY KEY (day, symbol, field));
"""

RAW_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw (
  published_at TEXT NOT NULL, symbol TEXT NOT NULL, at TEXT NOT NULL, price REAL NOT NULL,
  open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, prev_close REAL NOT NULL,
  volume INTEGER NOT NULL, amount REAL NOT NULL, bid1 REAL NOT NULL, ask1 REAL NOT NULL,
  bid1_volume INTEGER NOT NULL, ask1_volume INTEGER NOT NULL, suspended INTEGER NOT NULL,
  PRIMARY KEY (published_at, symbol));
CREATE INDEX IF NOT EXISTS raw_symbol ON raw(symbol, published_at);
"""


@contextmanager
def _connect(path: Path, schema: str) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(path, timeout=10)) as con:
        mode = con.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        if str(mode).lower() != "delete":
            raise RuntimeError(f"无法设置 journal_mode=DELETE：{mode}")
        con.executescript(schema)
        with con:
            yield con


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


class BoardStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "board.sqlite3"
        self.raw_dir = root / "raw"

    def raw_path(self, day: date) -> Path:
        return self.raw_dir / f"{day:%Y%m%d}.sqlite3"

    # ---- 盘前准备 ----
    def prepared(self, day: date) -> bool:
        if not self.path.exists():
            return False
        with _connect(self.path, BOARD_SCHEMA) as con:
            return con.execute("SELECT 1 FROM day_meta WHERE day=?", (day.isoformat(),)
                               ).fetchone() is not None

    def save_preparation(self, day: date, *, prev_day: date | None,
                         limits: Mapping[str, tuple[float | None, float | None]],
                         prev_heights: Mapping[str, int | None] | None, heights_error: str | None,
                         st_symbols: Iterable[str] | None,
                         concepts: ConceptSnapshot | None, concept_names: Mapping[str, str],
                         concept_error: str | None, prepared_at: datetime) -> None:
        d = day.isoformat()
        with _connect(self.path, BOARD_SCHEMA) as con:
            con.execute(
                "INSERT INTO day_meta (day, prepared_at, prev_day, concept_fetched_at, "
                "concept_sha, concept_error, st_published, heights_error) VALUES (?,?,?,?,?,?,?,?)",
                (d, _iso(prepared_at), _iso(prev_day),
                 _iso(concepts.fetched_at) if concepts else None,
                 concepts.source_sha if concepts else None, concept_error,
                 int(st_symbols is not None), heights_error))
            con.executemany("INSERT INTO limits VALUES (?,?,?,?)",
                            [(d, s, up, down) for s, (up, down) in sorted(limits.items())])
            if prev_heights is not None:
                con.executemany("INSERT INTO prev_heights VALUES (?,?,?)",
                                [(d, s, h) for s, h in sorted(prev_heights.items())])
            if st_symbols is not None:
                con.executemany("INSERT INTO st_symbols VALUES (?,?)",
                                [(d, s) for s in sorted(set(st_symbols))])
            if concepts is not None:
                con.executemany(
                    "INSERT INTO concepts VALUES (?,?,?,?,?)",
                    [(d, code, concept_names.get(code), concepts.index_count.get(code), len(m))
                     for code, m in sorted(concepts.members.items())])
                con.executemany(
                    "INSERT INTO concept_members VALUES (?,?,?)",
                    [(d, code, s) for code, m in sorted(concepts.members.items())
                     for s in sorted(m)])

    def load_board_day(self, day: date) -> BoardDay:
        """从看板库还原当日输入（重启续跑与收盘重放都用它，保证同一输入）。"""
        d = day.isoformat()
        with _connect(self.path, BOARD_SCHEMA) as con:
            meta = con.execute("SELECT heights_error, concept_fetched_at, concept_sha "
                               "FROM day_meta WHERE day=?", (d,)).fetchone()
            if meta is None:
                raise LookupError(f"{day} 未完成盘前准备")
            limits = {s: (up, down) for s, up, down in
                      con.execute("SELECT symbol, up, down FROM limits WHERE day=?", (d,))}
            heights = {s: h for s, h in
                       con.execute("SELECT symbol, height FROM prev_heights WHERE day=?", (d,))}
            concepts = None
            if meta[2] is not None:
                members: dict[str, set[str]] = {}
                for code, symbol in con.execute(
                        "SELECT code, symbol FROM concept_members WHERE day=?", (d,)):
                    members.setdefault(code, set()).add(symbol)
                counts = {code: n for code, n in con.execute(
                    "SELECT code, index_count FROM concepts WHERE day=?", (d,))}
                for code in counts:
                    members.setdefault(code, set())
                concepts = ConceptSnapshot(day, datetime.fromisoformat(meta[1]),
                                           {c: frozenset(m) for c, m in members.items()},
                                           counts, meta[2])
        # 昨日高度整体不可用时（limit_list_d 未入库等），全体未知而不是首板。
        return BoardDay(day, limits, heights if meta[0] is None else None, concepts)

    # ---- 盘中 ----
    def append_raw(self, day: date, published_at: datetime,
                   quotes: Mapping[str, MarketQuote]) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        p = _iso(published_at)
        rows = [(p, s, _iso(qt.at), qt.price, qt.open, qt.high, qt.low, qt.prev_close,
                 qt.volume, qt.amount, qt.bid1, qt.ask1, qt.bid1_volume, qt.ask1_volume,
                 int(qt.suspended)) for s, qt in sorted(quotes.items())]
        with _connect(self.raw_path(day), RAW_SCHEMA) as con:
            con.executemany(f"INSERT OR IGNORE INTO raw VALUES ({','.join('?' * 15)})", rows)

    def save_step(self, day: date, published_at: datetime, events: list[Event],
                  sentiment: Sentiment, tracked: Mapping[str, Tracked]) -> None:
        d = day.isoformat()
        with _connect(self.path, BOARD_SCHEMA) as con:
            seq = con.execute("SELECT COALESCE(MAX(seq), 0) FROM events WHERE day=?",
                              (d,)).fetchone()[0]
            con.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?)", [
                (d, seq + i + 1, _iso(published_at), _iso(e.at), e.symbol, e.kind, e.detail)
                for i, e in enumerate(events)])
            con.execute("INSERT OR REPLACE INTO snapshots VALUES "
                        f"({','.join('?' * 18)})", (d, *_sentiment_row(sentiment)))
            con.executemany("INSERT OR REPLACE INTO board VALUES (?,?,?,?,?,?,?,?,?,?)", [
                (d, t.symbol, _iso(t.entered_at), t.reason, _iso(t.first_seal), t.open_times,
                 t.state, t.column, int(t.ever_sealed), int(t.touched))
                for t in tracked.values()])

    def last_published(self, day: date) -> datetime | None:
        if not self.path.exists():
            return None
        with _connect(self.path, BOARD_SCHEMA) as con:
            row = con.execute("SELECT MAX(published_at) FROM snapshots WHERE day=?",
                              (day.isoformat(),)).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def raw_snapshots(self, day: date) -> Iterator[tuple[datetime, dict[str, MarketQuote]]]:
        with closing(sqlite3.connect(f"file:{self.raw_path(day)}?mode=ro", uri=True,
                                     timeout=10)) as con:
            current: str | None = None
            batch: dict[str, MarketQuote] = {}
            for row in con.execute(f"SELECT published_at, {', '.join(RAW_FIELDS)} FROM raw "
                                   "ORDER BY published_at, symbol"):
                if row[0] != current:
                    if current is not None:
                        yield datetime.fromisoformat(current), batch
                    current, batch = row[0], {}
                data = dict(zip(RAW_FIELDS, row[1:], strict=True))
                data["suspended"] = bool(data["suspended"])
                batch[data["symbol"]] = MarketQuote(**data)
            if current is not None:
                yield datetime.fromisoformat(current), batch

    # ---- 收盘压缩 ----
    def compact(self, day: date, now: datetime) -> str | None:
        """把入选票全日序列从临时全量写入看板库；校验通过才删除全量。返回失败原因。"""
        d = day.isoformat()
        raw = self.raw_path(day)
        if not raw.exists():
            return self._mark_compact(day, now, "当日无盘中全量文件")
        try:
            replay = self.load_board_day(day)
            stored = self._stored_sentiments(day)
            replayed = []
            for published_at, quotes in self.raw_snapshots(day):
                got = replay.ingest(published_at, quotes)
                if got is not None:
                    replayed.append(_sentiment_row(got[1]))
            if replayed != stored:
                return self._mark_compact(day, now, "全量重放的情绪聚合与盘中记录不一致，保留全量")
            symbols = sorted(replay.tracked)
            with closing(sqlite3.connect(f"file:{raw}?mode=ro", uri=True)) as src:
                rows = [(d, *r) for s in symbols for r in src.execute(
                    "SELECT symbol, published_at, at, price, high, volume, amount, bid1, "
                    "bid1_volume, ask1_volume FROM raw WHERE symbol=? ORDER BY published_at",
                    (s,))]
            with _connect(self.path, BOARD_SCHEMA) as con:
                con.execute("DELETE FROM series WHERE day=?", (d,))
                con.executemany(f"INSERT INTO series VALUES ({','.join('?' * 11)})", rows)
            with _connect(self.path, BOARD_SCHEMA) as con:
                back = [(d, *r) for r in con.execute(
                    "SELECT symbol, published_at, at, price, high, volume, amount, bid1, "
                    "bid1_volume, ask1_volume FROM series WHERE day=? "
                    "ORDER BY symbol, published_at", (d,))]
            if back != rows:
                return self._mark_compact(day, now, "入选票序列回读与全量不一致，保留全量")
        except Exception as exc:  # 校验失败一律保留全量
            return self._mark_compact(day, now, f"压缩失败：{type(exc).__name__}: {exc}")
        self._mark_compact(day, now, None)
        raw.unlink()
        return None

    def _stored_sentiments(self, day: date) -> list[tuple]:
        with _connect(self.path, BOARD_SCHEMA) as con:
            return [tuple(r[1:]) for r in con.execute(
                "SELECT * FROM snapshots WHERE day=? ORDER BY published_at", (day.isoformat(),))]

    def _mark_compact(self, day: date, now: datetime, error: str | None) -> str | None:
        with _connect(self.path, BOARD_SCHEMA) as con:
            con.execute("UPDATE day_meta SET compacted_at=?, compact_error=? WHERE day=?",
                        (_iso(now), error, day.isoformat()))
        return error

    def compacted(self, day: date) -> bool:
        with _connect(self.path, BOARD_SCHEMA) as con:
            row = con.execute("SELECT compacted_at FROM day_meta WHERE day=?",
                              (day.isoformat(),)).fetchone()
        return bool(row and row[0])

    # ---- 盘后对账 ----
    def board_rows(self, day: date) -> list[tuple]:
        with _connect(self.path, BOARD_SCHEMA) as con:
            return list(con.execute("SELECT symbol, first_seal, open_times, ever_sealed "
                                    "FROM board WHERE day=?", (day.isoformat(),)))

    def save_reconcile(self, day: date, mismatches: Iterable, now: datetime,
                       error: str | None = None) -> None:
        d = day.isoformat()
        with _connect(self.path, BOARD_SCHEMA) as con:
            con.execute("DELETE FROM mismatches WHERE day=?", (d,))
            con.executemany("INSERT INTO mismatches VALUES (?,?,?,?,?)", [
                (d, m.symbol, m.field, json.dumps(m.observed, default=str),
                 json.dumps(m.direct, default=str)) for m in mismatches])
            con.execute("UPDATE day_meta SET reconciled_at=?, reconcile_error=? WHERE day=?",
                        (_iso(now), error, d))

    def reconciled(self, day: date) -> bool:
        with _connect(self.path, BOARD_SCHEMA) as con:
            row = con.execute("SELECT reconciled_at, reconcile_error FROM day_meta WHERE day=?",
                              (day.isoformat(),)).fetchone()
        return bool(row and row[0] and row[1] is None)


def _sentiment_row(s: Sentiment) -> tuple:
    values = astuple(s)
    return (_iso(s.published_at), *values[1:8], *s.promote_1_2, *s.promote_2_3,
            *s.promote_3_up, *values[11:])
