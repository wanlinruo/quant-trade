"""T+1 涨停概率影子榜单的独立快照契约与只读查询。

监控 Web 只实例化 :class:`LimitUpShadowReader`，它用 SQLite ``mode=ro``
打开文件；离线批任务通过 :class:`LimitUpShadowPublisher` 发布快照。这个边界刻意
不引用策略、账户、订单或风控模块。
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RunStatus = Literal["waiting", "generating", "ready", "failed"]
ViewState = Literal["unavailable", "waiting", "generating", "ready", "stale", "failed"]


class LimitUpShadowEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    rank: int = Field(ge=1, le=20)
    symbol: str = Field(min_length=1)
    name: str = ""
    board: str = ""
    market_state: str = ""
    p_touch: float = Field(ge=0, le=1)
    p_seal_given_touch: float = Field(ge=0, le=1)
    p_close_limit: float = Field(ge=0, le=1)
    actual_touched: bool | None = None
    actual_closed_limit: bool | None = None
    opened_at_limit: bool | None = None
    one_price_limit: bool | None = None

    @model_validator(mode="after")
    def probabilities_are_coherent(self):
        expected = self.p_touch * self.p_seal_given_touch
        if abs(self.p_close_limit - expected) > 1e-6:
            raise ValueError("p_close_limit must equal p_touch * p_seal_given_touch")
        if self.p_close_limit > self.p_touch + 1e-12:
            raise ValueError("p_close_limit cannot exceed p_touch")
        if self.actual_closed_limit is True and self.actual_touched is not True:
            raise ValueError("actual close limit implies actual touch")
        if self.opened_at_limit is True and self.actual_touched is not True:
            raise ValueError("limit-up open implies actual touch")
        if self.one_price_limit is True and not (
            self.actual_touched is True
            and self.opened_at_limit is True
            and self.actual_closed_limit is True
        ):
            raise ValueError("one-price limit requires touch, limit-up open and close")
        return self


class LimitUpShadowSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    prediction_date: date
    target_session: date
    generated_at: datetime
    status: RunStatus = "ready"
    model_version: str = Field(min_length=1)
    model_sha: str = Field(min_length=7)
    data_version: str = Field(min_length=1)
    data_sha: str = Field(min_length=7)
    eligible_count: int = Field(ge=0)
    rolling_precision_at_20: float | None = Field(default=None, ge=0, le=1)
    calibration_error: float | None = Field(default=None, ge=0)
    drift_score: float | None = Field(default=None, ge=0)
    error: str = ""
    entries: tuple[LimitUpShadowEntry, ...] = ()

    @model_validator(mode="after")
    def snapshot_is_coherent(self):
        if self.target_session <= self.prediction_date:
            raise ValueError("target_session must be after prediction_date")
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must include a timezone")
        if self.status == "ready" and not self.entries:
            raise ValueError("ready snapshot must contain entries")
        if self.status != "ready" and self.entries:
            raise ValueError("only ready snapshots may contain entries")
        if self.status == "failed" and not self.error.strip():
            raise ValueError("failed snapshot must include an error")
        ranks = [entry.rank for entry in self.entries]
        if ranks and ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("entry ranks must be contiguous and ordered from 1")
        close_probabilities = [entry.p_close_limit for entry in self.entries]
        if close_probabilities != sorted(close_probabilities, reverse=True):
            raise ValueError("entries must be ranked by p_close_limit descending")
        symbols = [entry.symbol for entry in self.entries]
        if len(symbols) != len(set(symbols)):
            raise ValueError("entry symbols must be unique")
        if self.eligible_count < len(self.entries):
            raise ValueError("eligible_count cannot be smaller than entries")
        return self


class LimitUpShadowHistory(BaseModel):
    prediction_date: date
    target_session: date
    generated_at: datetime
    status: RunStatus
    model_version: str
    entry_count: int
    precision_at_20: float | None = None


class LimitUpShadowView(BaseModel):
    state: ViewState
    available: bool
    historical: bool = False
    message: str
    snapshot: LimitUpShadowSnapshot | None = None
    history: tuple[LimitUpShadowHistory, ...] = ()


class LimitUpShadowOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    actual_touched: bool
    actual_closed_limit: bool
    opened_at_limit: bool
    one_price_limit: bool

    @model_validator(mode="after")
    def outcome_is_coherent(self):
        if self.actual_closed_limit and not self.actual_touched:
            raise ValueError("actual close limit implies actual touch")
        if self.opened_at_limit and not self.actual_touched:
            raise ValueError("limit-up open implies actual touch")
        if self.one_price_limit and not (
            self.actual_touched and self.actual_closed_limit and self.opened_at_limit
        ):
            raise ValueError("one-price limit requires touch, limit-up open and close")
        return self


_SCHEMA = """
CREATE TABLE IF NOT EXISTS limit_up_shadow_runs (
    prediction_date TEXT PRIMARY KEY,
    target_session TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('waiting','generating','ready','failed')),
    model_version TEXT NOT NULL,
    model_sha TEXT NOT NULL,
    data_version TEXT NOT NULL,
    data_sha TEXT NOT NULL,
    eligible_count INTEGER NOT NULL,
    rolling_precision_at_20 REAL,
    calibration_error REAL,
    drift_score REAL,
    error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS limit_up_shadow_entries (
    prediction_date TEXT NOT NULL,
    rank INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    board TEXT NOT NULL,
    market_state TEXT NOT NULL,
    p_touch REAL NOT NULL,
    p_seal_given_touch REAL NOT NULL,
    p_close_limit REAL NOT NULL,
    actual_touched INTEGER,
    actual_closed_limit INTEGER,
    opened_at_limit INTEGER,
    one_price_limit INTEGER,
    PRIMARY KEY (prediction_date, rank),
    UNIQUE (prediction_date, symbol),
    FOREIGN KEY (prediction_date) REFERENCES limit_up_shadow_runs(prediction_date)
);
CREATE TABLE IF NOT EXISTS limit_up_shadow_outcomes (
    prediction_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source_data_sha TEXT NOT NULL,
    actual_touched INTEGER NOT NULL,
    actual_closed_limit INTEGER NOT NULL,
    opened_at_limit INTEGER NOT NULL,
    one_price_limit INTEGER NOT NULL,
    PRIMARY KEY (prediction_date, symbol),
    FOREIGN KEY (prediction_date, symbol)
      REFERENCES limit_up_shadow_entries(prediction_date, symbol)
);
"""


def _bool_or_none(value: int | None) -> bool | None:
    return None if value is None else bool(value)


class LimitUpShadowReader:
    """Fail-closed read model used by the monitor process."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=2)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA busy_timeout=2000")
        return con

    def view(
        self, prediction_date: date | None = None, *, today: date | None = None,
        history_limit: int = 60,
    ) -> LimitUpShadowView:
        try:
            with self._connect() as con:
                has_outcomes = self._has_outcomes(con)
                history = self._history(con, history_limit, has_outcomes=has_outcomes)
                run = con.execute(
                    "SELECT * FROM limit_up_shadow_runs WHERE prediction_date = ?"
                    if prediction_date else
                    "SELECT * FROM limit_up_shadow_runs ORDER BY prediction_date DESC LIMIT 1",
                    (prediction_date.isoformat(),) if prediction_date else (),
                ).fetchone()
                if run is None:
                    return LimitUpShadowView(
                        state="waiting" if history == () else "unavailable",
                        available=False,
                        historical=prediction_date is not None,
                        message="该日期尚无影子榜单" if prediction_date else "等待首份影子榜单",
                        history=history,
                    )
                rows = con.execute(
                    ("SELECT e.*, COALESCE(o.actual_touched, e.actual_touched) "
                     "AS resolved_touched, COALESCE(o.actual_closed_limit, "
                     "e.actual_closed_limit) AS resolved_closed, "
                     "COALESCE(o.opened_at_limit, e.opened_at_limit) AS resolved_open, "
                     "COALESCE(o.one_price_limit, e.one_price_limit) AS resolved_one "
                     "FROM limit_up_shadow_entries e LEFT JOIN limit_up_shadow_outcomes o "
                     "ON o.prediction_date=e.prediction_date AND o.symbol=e.symbol "
                     "WHERE e.prediction_date = ? ORDER BY e.rank")
                    if has_outcomes else
                    ("SELECT *, actual_touched AS resolved_touched, "
                     "actual_closed_limit AS resolved_closed, "
                     "opened_at_limit AS resolved_open, "
                     "one_price_limit AS resolved_one FROM limit_up_shadow_entries "
                     "WHERE prediction_date = ? ORDER BY rank"),
                    (run["prediction_date"],),
                ).fetchall()
                metrics = self._rolling_metrics(
                    con, run["prediction_date"], has_outcomes=has_outcomes,
                )
                snapshot = self._snapshot(run, rows, metrics=metrics)
        except (OSError, sqlite3.Error, ValueError) as exc:
            return LimitUpShadowView(
                state="unavailable", available=False,
                historical=prediction_date is not None,
                message=f"影子榜单不可用：{type(exc).__name__}",
            )

        historical = prediction_date is not None
        state: ViewState = snapshot.status
        if (
            state == "ready" and not historical
            and snapshot.target_session < (today or date.today())
        ):
            state = "stale"
        messages = {
            "waiting": "等待离线任务生成",
            "generating": "离线任务生成中",
            "ready": "只读影子结果已就绪",
            "stale": "最新快照已过期，仅供历史查看",
            "failed": snapshot.error or "离线任务失败",
        }
        return LimitUpShadowView(
            state=state,
            available=state == "ready",
            historical=historical,
            message=messages[state],
            snapshot=snapshot,
            history=history,
        )

    def pending_outcomes(self, up_to: date) -> tuple[tuple[date, date], ...]:
        """Return ready prediction/target pairs whose result is not recorded."""
        try:
            with self._connect() as con:
                has_outcomes = self._has_outcomes(con)
                exclusion = (
                    "AND NOT EXISTS (SELECT 1 FROM limit_up_shadow_outcomes o "
                    "WHERE o.prediction_date=r.prediction_date)"
                    if has_outcomes else ""
                )
                rows = con.execute(
                    "SELECT r.prediction_date,r.target_session FROM limit_up_shadow_runs r "
                    "WHERE r.status='ready' AND r.target_session<=? "
                    f"{exclusion} ORDER BY r.prediction_date",
                    (up_to.isoformat(),),
                ).fetchall()
                return tuple((date.fromisoformat(row[0]), date.fromisoformat(row[1]))
                             for row in rows)
        except (OSError, sqlite3.Error):
            return ()

    @staticmethod
    def _has_outcomes(con: sqlite3.Connection) -> bool:
        return con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='limit_up_shadow_outcomes'"
        ).fetchone() is not None

    @staticmethod
    def _history(
        con: sqlite3.Connection, limit: int, *, has_outcomes: bool,
    ) -> tuple[LimitUpShadowHistory, ...]:
        precision = (
            "CASE WHEN COUNT(o.symbol)=COUNT(e.rank) AND COUNT(e.rank)>0 "
            "THEN AVG(COALESCE(o.actual_closed_limit,e.actual_closed_limit)) "
            "ELSE r.rolling_precision_at_20 END"
            if has_outcomes else "r.rolling_precision_at_20"
        )
        outcome_join = (
            "LEFT JOIN limit_up_shadow_outcomes o ON "
            "o.prediction_date=e.prediction_date AND o.symbol=e.symbol"
            if has_outcomes else ""
        )
        rows = con.execute(
            "SELECT r.prediction_date, r.target_session, r.generated_at, r.status, "
            f"r.model_version, {precision} AS precision_at_20, "
            "COUNT(e.rank) AS entry_count "
            "FROM limit_up_shadow_runs r LEFT JOIN limit_up_shadow_entries e "
            "ON e.prediction_date = r.prediction_date "
            f"{outcome_join} "
            "GROUP BY r.prediction_date ORDER BY r.prediction_date DESC LIMIT ?",
            (max(1, min(limit, 366)),),
        ).fetchall()
        return tuple(LimitUpShadowHistory(
            prediction_date=row["prediction_date"], target_session=row["target_session"],
            generated_at=row["generated_at"], status=row["status"],
            model_version=row["model_version"], entry_count=row["entry_count"],
            precision_at_20=row["precision_at_20"],
        ) for row in rows)

    @staticmethod
    def _rolling_metrics(
        con: sqlite3.Connection, prediction_date: str, *, has_outcomes: bool,
    ) -> tuple[float | None, float | None]:
        if not has_outcomes:
            return None, None
        row = con.execute(
            "WITH completed AS ("
            " SELECT e.prediction_date FROM limit_up_shadow_entries e "
            " LEFT JOIN limit_up_shadow_outcomes o ON o.prediction_date=e.prediction_date "
            " AND o.symbol=e.symbol WHERE e.prediction_date<=? "
            " GROUP BY e.prediction_date HAVING COUNT(o.symbol)=COUNT(e.symbol)"
            " ORDER BY e.prediction_date DESC LIMIT 60) "
            "SELECT AVG(o.actual_closed_limit), "
            "AVG((e.p_close_limit-o.actual_closed_limit)*"
            "(e.p_close_limit-o.actual_closed_limit)) "
            "FROM completed c JOIN limit_up_shadow_entries e "
            "ON e.prediction_date=c.prediction_date JOIN limit_up_shadow_outcomes o "
            "ON o.prediction_date=e.prediction_date AND o.symbol=e.symbol",
            (prediction_date,),
        ).fetchone()
        return (row[0], row[1]) if row else (None, None)

    @staticmethod
    def _snapshot(
        run: sqlite3.Row, rows: list[sqlite3.Row], *,
        metrics: tuple[float | None, float | None] = (None, None),
    ) -> LimitUpShadowSnapshot:
        entries = tuple(LimitUpShadowEntry(
            rank=row["rank"], symbol=row["symbol"], name=row["name"],
            board=row["board"], market_state=row["market_state"],
            p_touch=row["p_touch"],
            p_seal_given_touch=row["p_seal_given_touch"],
            p_close_limit=row["p_close_limit"],
            actual_touched=_bool_or_none(row["resolved_touched"]),
            actual_closed_limit=_bool_or_none(row["resolved_closed"]),
            opened_at_limit=_bool_or_none(row["resolved_open"]),
            one_price_limit=_bool_or_none(row["resolved_one"]),
        ) for row in rows)
        return LimitUpShadowSnapshot(
            prediction_date=run["prediction_date"], target_session=run["target_session"],
            generated_at=run["generated_at"], status=run["status"],
            model_version=run["model_version"], model_sha=run["model_sha"],
            data_version=run["data_version"], data_sha=run["data_sha"],
            eligible_count=run["eligible_count"],
            rolling_precision_at_20=(
                metrics[0] if metrics[0] is not None else run["rolling_precision_at_20"]
            ),
            calibration_error=(
                metrics[1] if metrics[1] is not None else run["calibration_error"]
            ),
            drift_score=run["drift_score"], error=run["error"], entries=entries,
        )


class LimitUpShadowPublisher:
    """Offline-only publisher; final snapshots are immutable."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def publish(self, snapshot: LimitUpShadowSnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=10) as con:
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(_SCHEMA)
            existing = con.execute(
                "SELECT status FROM limit_up_shadow_runs WHERE prediction_date = ?",
                (snapshot.prediction_date.isoformat(),),
            ).fetchone()
            if existing and existing[0] in ("ready", "failed"):
                raise ValueError("final shadow snapshot is immutable")
            con.execute("DELETE FROM limit_up_shadow_entries WHERE prediction_date = ?",
                        (snapshot.prediction_date.isoformat(),))
            con.execute(
                "INSERT OR REPLACE INTO limit_up_shadow_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    snapshot.prediction_date.isoformat(), snapshot.target_session.isoformat(),
                    snapshot.generated_at.isoformat(), snapshot.status,
                    snapshot.model_version, snapshot.model_sha, snapshot.data_version,
                    snapshot.data_sha, snapshot.eligible_count,
                    snapshot.rolling_precision_at_20, snapshot.calibration_error,
                    snapshot.drift_score, snapshot.error,
                ),
            )
            con.executemany(
                "INSERT INTO limit_up_shadow_entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(
                    snapshot.prediction_date.isoformat(), row.rank, row.symbol, row.name,
                    row.board, row.market_state, row.p_touch, row.p_seal_given_touch,
                    row.p_close_limit, row.actual_touched, row.actual_closed_limit,
                    row.opened_at_limit, row.one_price_limit,
                ) for row in snapshot.entries],
            )

    def record_outcomes(
        self, prediction_date: date, outcomes: tuple[LimitUpShadowOutcome, ...], *,
        observed_at: datetime, source_data_sha: str,
    ) -> None:
        if observed_at.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        if len(source_data_sha) < 7:
            raise ValueError("source_data_sha is required")
        with sqlite3.connect(self.path, timeout=10) as con:
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(_SCHEMA)
            expected = {row[0] for row in con.execute(
                "SELECT symbol FROM limit_up_shadow_entries WHERE prediction_date=?",
                (prediction_date.isoformat(),),
            )}
            provided = {outcome.symbol for outcome in outcomes}
            if not expected or provided != expected or len(provided) != len(outcomes):
                raise ValueError("outcomes must exactly cover the published ranking")
            existing = con.execute(
                "SELECT COUNT(*) FROM limit_up_shadow_outcomes WHERE prediction_date=?",
                (prediction_date.isoformat(),),
            ).fetchone()[0]
            if existing:
                rows = con.execute(
                    "SELECT symbol,actual_touched,actual_closed_limit,opened_at_limit,"
                    "one_price_limit,source_data_sha FROM limit_up_shadow_outcomes "
                    "WHERE prediction_date=? ORDER BY symbol",
                    (prediction_date.isoformat(),),
                ).fetchall()
                old = [(row[0], bool(row[1]), bool(row[2]), bool(row[3]), bool(row[4]))
                       for row in rows]
                new = sorted((row.symbol, row.actual_touched, row.actual_closed_limit,
                              row.opened_at_limit, row.one_price_limit)
                             for row in outcomes)
                if old == new and all(row[5] == source_data_sha for row in rows):
                    return
                raise ValueError("published outcomes are immutable")
            con.executemany(
                "INSERT INTO limit_up_shadow_outcomes VALUES (?,?,?,?,?,?,?,?)",
                [(
                    prediction_date.isoformat(), outcome.symbol,
                    observed_at.isoformat(), source_data_sha,
                    outcome.actual_touched, outcome.actual_closed_limit,
                    outcome.opened_at_limit, outcome.one_price_limit,
                ) for outcome in outcomes],
            )
