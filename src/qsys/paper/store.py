"""SQLite persistence for independent, durable paper-trading accounts."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Mapping, Sequence

from qsys.core.events import Reject
from qsys.core.portfolio import Portfolio, Position
from qsys.core.trading import Fill, Order
from qsys.core.types import Symbol
from qsys.ops.strategy_log import RUN_COLUMNS, RunRecord, decode_run, run_values


ACTIVE = ("scheduled", "running", "paused", "error")


@dataclass(frozen=True, slots=True)
class PaperAccount:
    id: str
    name: str
    backtest_id: str
    strategy_id: str
    strategy_name: str
    version_id: str
    status: str
    initial_cash: float
    cash: float
    activation_date: str
    config: Mapping[str, Any]
    state: Mapping[str, Any]
    error: str
    created_at: str
    updated_at: str
    ended_at: str


class PaperStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def _init(self) -> None:
        with self._connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS paper_accounts (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, backtest_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL, strategy_name TEXT NOT NULL,
                    version_id TEXT NOT NULL, status TEXT NOT NULL,
                    initial_cash REAL NOT NULL, cash REAL NOT NULL,
                    activation_date TEXT NOT NULL, config_json TEXT NOT NULL,
                    state_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS paper_status_created
                    ON paper_accounts(status, created_at);
                CREATE TABLE IF NOT EXISTS paper_positions (
                    account_id TEXT NOT NULL, symbol TEXT NOT NULL,
                    total_qty INTEGER NOT NULL, available_qty INTEGER NOT NULL,
                    cost_basis REAL NOT NULL, last_price REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, symbol),
                    FOREIGN KEY(account_id) REFERENCES paper_accounts(id)
                );
                CREATE TABLE IF NOT EXISTS paper_events (
                    account_id TEXT NOT NULL, event_key TEXT NOT NULL,
                    scheduled_at TEXT NOT NULL, phase TEXT NOT NULL,
                    status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(account_id, event_key)
                );
                CREATE TABLE IF NOT EXISTS paper_orders (
                    account_id TEXT NOT NULL, order_id TEXT NOT NULL,
                    event_key TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
                    qty INTEGER NOT NULL, remaining_qty INTEGER NOT NULL,
                    order_type TEXT NOT NULL, limit_price REAL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, order_id)
                );
                CREATE TABLE IF NOT EXISTS paper_fills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL,
                    order_id TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
                    qty INTEGER NOT NULL, price REAL NOT NULL, fee REAL NOT NULL,
                    filled_at TEXT NOT NULL,
                    UNIQUE(account_id, order_id, filled_at, qty, price)
                );
                CREATE TABLE IF NOT EXISTS paper_rejects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL,
                    order_id TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
                    qty INTEGER NOT NULL, reason TEXT NOT NULL, at TEXT NOT NULL,
                    UNIQUE(account_id, order_id, at, reason)
                );
                CREATE TABLE IF NOT EXISTS paper_equity (
                    account_id TEXT NOT NULL, at TEXT NOT NULL, session TEXT NOT NULL,
                    cash REAL NOT NULL, market_value REAL NOT NULL,
                    total_value REAL NOT NULL, benchmark_value REAL,
                    PRIMARY KEY(account_id, at)
                );
                CREATE TABLE IF NOT EXISTS paper_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL,
                    at TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_strategy_runs (
                    account_id TEXT NOT NULL, event_key TEXT NOT NULL,
                    session TEXT NOT NULL, phase TEXT NOT NULL,
                    started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL, cash REAL NOT NULL,
                    market_value REAL NOT NULL, total_value REAL NOT NULL,
                    position_count INTEGER NOT NULL, candidates INTEGER,
                    passed INTEGER, orders INTEGER NOT NULL, rejects INTEGER NOT NULL,
                    summary TEXT NOT NULL, reasons_json TEXT NOT NULL DEFAULT '{}',
                    note TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(account_id, event_key)
                );
                CREATE INDEX IF NOT EXISTS paper_runs_session
                    ON paper_strategy_runs(account_id, session, started_at);
            """)

    def record_run(self, ident: str, record: RunRecord) -> None:
        columns = ("account_id", *RUN_COLUMNS)
        placeholders = ",".join("?" for _ in columns)
        with self._connect() as con:
            con.execute(
                f"INSERT OR REPLACE INTO paper_strategy_runs "
                f"({','.join(columns)}) VALUES ({placeholders})",
                (ident, *run_values(record)),
            )

    def strategy_runs(
        self, ident: str, session: date
    ) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM paper_strategy_runs "
                "WHERE account_id=? AND session=? ORDER BY started_at",
                (ident, session.isoformat()),
            ).fetchall()
        return [decode_run(row) for row in rows]

    def run_sessions(self, ident: str, limit: int = 60) -> list[date]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT DISTINCT session FROM paper_strategy_runs "
                "WHERE account_id=? ORDER BY session DESC LIMIT ?",
                (ident, limit),
            ).fetchall()
        return [date.fromisoformat(row["session"]) for row in rows]

    def create(self, backtest_id: str, name: str, cash: float, activation: date) -> PaperAccount:
        name = name.strip()
        if not name:
            raise ValueError("模拟账户名称不能为空")
        if cash <= 0:
            raise ValueError("初始资金必须大于 0")
        with self._connect() as con:
            job = con.execute(
                "SELECT * FROM backtest_jobs WHERE id=?", (backtest_id,)
            ).fetchone()
            if job is None or job["status"] != "succeeded":
                raise LookupError("只能从已成功的工作台回测创建模拟交易")
            ident, now = uuid.uuid4().hex[:16], _now()
            config = json.loads(job["config_json"])
            con.execute(
                "INSERT INTO paper_accounts "
                "(id,name,backtest_id,strategy_id,strategy_name,version_id,status,"
                "initial_cash,cash,activation_date,config_json,state_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ident, name, backtest_id, job["strategy_id"], job["strategy_name"],
                 job["version_id"], "scheduled", cash, cash, str(activation),
                 json.dumps(config, ensure_ascii=False), "{}", now, now),
            )
        return self.account(ident)  # type: ignore[return-value]

    def account(self, ident: str) -> PaperAccount | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM paper_accounts WHERE id=?", (ident,)).fetchone()
        return self._account(row) if row else None

    def accounts(self, *, status: str = "") -> list[PaperAccount]:
        with self._connect() as con:
            if status:
                rows = con.execute(
                    "SELECT * FROM paper_accounts WHERE status=? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM paper_accounts ORDER BY created_at DESC"
                ).fetchall()
        return [self._account(row) for row in rows]

    def transition(
        self, ident: str, action: str, *, activation: date | None = None,
    ) -> PaperAccount:
        """`resume` 的生效日由调用方按交易日历算好传入（见 paper.calendar）。"""
        targets = {
            "pause": ({"scheduled", "running", "error"}, "paused"),
            "resume": ({"paused", "error"}, "scheduled"),
            "end": ({"scheduled", "running", "paused", "error"}, "ended"),
        }
        if action not in targets:
            raise ValueError(f"未知操作: {action}")
        allowed, target = targets[action]
        with self._connect() as con:
            row = con.execute("SELECT status FROM paper_accounts WHERE id=?", (ident,)).fetchone()
            if row is None:
                raise LookupError("模拟账户不存在")
            if row["status"] not in allowed:
                raise ValueError(f"当前状态 {row['status']} 不能执行 {action}")
            now = _now()
            if action in ("pause", "end"):
                pending = con.execute(
                    "SELECT * FROM paper_orders WHERE account_id=? AND status='pending'",
                    (ident,),
                ).fetchall()
                reason = "paper account paused" if action == "pause" else "paper account ended"
                con.executemany(
                    "INSERT OR IGNORE INTO paper_rejects "
                    "(account_id,order_id,symbol,side,qty,reason,at) VALUES (?,?,?,?,?,?,?)",
                    [(ident, row["order_id"], row["symbol"], row["side"],
                      row["remaining_qty"], reason, now) for row in pending],
                )
                con.execute(
                    "UPDATE paper_orders SET remaining_qty=0,status='cancelled',updated_at=? "
                    "WHERE account_id=? AND status='pending'",
                    (now, ident),
                )
            if action == "resume":
                if activation is None:
                    raise ValueError("恢复模拟账户需要按交易日历给出生效日")
                con.execute(
                    "UPDATE paper_accounts SET status=?,activation_date=?,error='',"
                    "updated_at=?,ended_at='' WHERE id=?",
                    (target, str(activation), now, ident),
                )
            else:
                con.execute(
                    "UPDATE paper_accounts SET status=?,error='',updated_at=?,ended_at=? WHERE id=?",
                    (target, now, now if target == "ended" else "", ident),
                )
        return self.account(ident)  # type: ignore[return-value]

    def mark_running(self, ident: str) -> bool:
        with self._connect() as con:
            cur = con.execute(
                "UPDATE paper_accounts SET status='running',updated_at=? "
                "WHERE id=? AND status='scheduled' AND activation_date<=?",
                (_now(), ident, str(date.today())),
            )
        return cur.rowcount == 1

    def log(self, ident: str, level: str, message: str) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO paper_logs(account_id,at,level,message) VALUES (?,?,?,?)",
                (ident, _now(), level, message),
            )

    def mark_error(self, ident: str, error: str) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE paper_accounts SET status='error',error=?,updated_at=? WHERE id=?",
                (error, _now(), ident),
            )
            con.execute(
                "INSERT INTO paper_logs(account_id,at,level,message) VALUES (?,?,?,?)",
                (ident, _now(), "error", error),
            )

    def source(self, ident: str) -> str:
        with self._connect() as con:
            row = con.execute(
                "SELECT v.source FROM strategy_versions v JOIN paper_accounts p "
                "ON p.version_id=v.id WHERE p.id=?", (ident,),
            ).fetchone()
        if row is None:
            raise LookupError("模拟账户的策略版本不存在")
        return str(row["source"])

    def portfolio(self, ident: str) -> Portfolio:
        account = self.account(ident)
        if account is None:
            raise LookupError("模拟账户不存在")
        portfolio = Portfolio(account.initial_cash)
        portfolio.cash = account.cash
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM paper_positions WHERE account_id=?", (ident,)
            ).fetchall()
        for row in rows:
            symbol = Symbol.parse(row["symbol"])
            portfolio.positions[symbol] = Position(
                symbol, row["total_qty"], row["available_qty"], row["cost_basis"]
            )
        return portfolio

    def save_snapshot(
        self, ident: str, portfolio: Portfolio, state: Mapping[str, Any],
        prices: Mapping[Symbol, float], *, at: datetime, benchmark_value: float | None = None,
    ) -> None:
        encoded = json.dumps(_encode(dict(state)), ensure_ascii=False)
        market = sum(p.total_qty * prices.get(s, 0.0) for s, p in portfolio.positions.items())
        with self._connect() as con:
            self._write_snapshot(con, ident, portfolio, encoded, prices, at,
                                 market, benchmark_value)

    def commit_event(
        self, ident: str, key: str, portfolio: Portfolio,
        state: Mapping[str, Any], prices: Mapping[Symbol, float], *,
        at: datetime, detail: str = "",
    ) -> None:
        """Atomically persist account state and mark one scheduler event complete."""
        encoded = json.dumps(_encode(dict(state)), ensure_ascii=False)
        market = sum(p.total_qty * prices.get(s, 0.0) for s, p in portfolio.positions.items())
        with self._connect() as con:
            self._write_snapshot(con, ident, portfolio, encoded, prices, at, market, None)
            con.execute(
                "UPDATE paper_events SET status='completed',detail=?,completed_at=? "
                "WHERE account_id=? AND event_key=?",
                (detail, _now(), ident, key),
            )

    @staticmethod
    def _write_snapshot(
        con, ident: str, portfolio: Portfolio, encoded: str,
        prices: Mapping[Symbol, float], at: datetime, market: float,
        benchmark_value: float | None,
    ) -> None:
        now = _now()
        con.execute(
            "UPDATE paper_accounts SET cash=?,state_json=?,updated_at=? WHERE id=?",
            (portfolio.cash, encoded, now, ident),
        )
        con.execute("DELETE FROM paper_positions WHERE account_id=?", (ident,))
        con.executemany(
            "INSERT INTO paper_positions VALUES (?,?,?,?,?,?,?)",
            [(ident, str(s), p.total_qty, p.available_qty, p.cost_basis,
              prices.get(s, 0.0), now) for s, p in portfolio.positions.items()],
        )
        con.execute(
            "INSERT OR REPLACE INTO paper_equity VALUES (?,?,?,?,?,?,?)",
            (ident, at.isoformat(), str(at.date()), portfolio.cash, market,
             portfolio.cash + market, benchmark_value),
        )

    def begin_event(self, ident: str, key: str, at: datetime, phase: str) -> bool:
        with self._connect() as con:
            existing = con.execute(
                "SELECT status FROM paper_events WHERE account_id=? AND event_key=?",
                (ident, key),
            ).fetchone()
            if existing is not None:
                return existing["status"] == "running"
            cur = con.execute(
                "INSERT OR IGNORE INTO paper_events "
                "(account_id,event_key,scheduled_at,phase,status) VALUES (?,?,?,?,?)",
                (ident, key, at.isoformat(), phase, "running"),
            )
        return cur.rowcount == 1

    def pending_orders(self, ident: str) -> list[Order]:
        from qsys.core.trading import OrderType, Side

        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM paper_orders WHERE account_id=? AND status='pending' "
                "ORDER BY created_at", (ident,),
            ).fetchall()
        return [Order(
            row["order_id"], Symbol.parse(row["symbol"]), Side(row["side"]),
            row["remaining_qty"], OrderType(row["order_type"]), row["limit_price"],
            datetime.fromisoformat(row["created_at"]),
        ) for row in rows]

    def finish_event(self, ident: str, key: str, *, detail: str = "") -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE paper_events SET status='completed',detail=?,completed_at=? "
                "WHERE account_id=? AND event_key=?",
                (detail, _now(), ident, key),
            )

    def record_orders(self, ident: str, event_key: str, orders: Sequence[Order]) -> None:
        now = _now()
        with self._connect() as con:
            con.executemany(
                "INSERT OR IGNORE INTO paper_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(ident, o.order_id, event_key, str(o.symbol), o.side.value, o.qty, o.qty,
                  o.order_type.value, o.limit_price, "pending", o.created_at.isoformat(), now)
                 for o in orders],
            )

    def record_results(
        self, ident: str, fills: Sequence[Fill], rejects: Sequence[Reject],
        remaining: Mapping[str, int],
    ) -> None:
        with self._connect() as con:
            con.executemany(
                "INSERT OR IGNORE INTO paper_fills "
                "(account_id,order_id,symbol,side,qty,price,fee,filled_at) VALUES (?,?,?,?,?,?,?,?)",
                [(ident, f.order_id, str(f.symbol), f.side.value, f.qty, f.price, f.fee,
                  f.filled_at.isoformat()) for f in fills],
            )
            con.executemany(
                "INSERT OR IGNORE INTO paper_rejects "
                "(account_id,order_id,symbol,side,qty,reason,at) VALUES (?,?,?,?,?,?,?)",
                [(ident, r.order_id, str(r.symbol), r.side.value, r.qty, r.reason,
                  r.at.isoformat()) for r in rejects],
            )
            touched = {x.order_id for x in fills} | {x.order_id for x in rejects}
            for order_id in touched:
                left = int(remaining.get(order_id, 0))
                status = "pending" if left else (
                    "rejected" if any(r.order_id == order_id for r in rejects)
                    and not any(f.order_id == order_id for f in fills) else "filled"
                )
                con.execute(
                    "UPDATE paper_orders SET remaining_qty=?,status=?,updated_at=? "
                    "WHERE account_id=? AND order_id=?",
                    (left, status, _now(), ident, order_id),
                )

    def rows(self, ident: str, table: str, *, limit: int = 500) -> list[dict]:
        allowed = {
            "positions": ("paper_positions", "updated_at DESC"),
            "orders": ("paper_orders", "created_at DESC"),
            "fills": ("paper_fills", "filled_at DESC"),
            "rejects": ("paper_rejects", "at DESC"),
            "events": ("paper_events", "scheduled_at DESC"),
            "logs": ("paper_logs", "at DESC"),
            "equity": ("paper_equity", "at ASC"),
        }
        if table not in allowed:
            raise ValueError("unknown paper table")
        name, order = allowed[table]
        with self._connect() as con:
            rows = con.execute(
                f"SELECT * FROM {name} WHERE account_id=? ORDER BY {order} LIMIT ?",
                (ident, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def summary(self, ident: str) -> dict[str, Any]:
        account = self.account(ident)
        if account is None:
            raise LookupError("模拟账户不存在")
        equity = self.rows(ident, "equity", limit=10000)
        total = equity[-1]["total_value"] if equity else account.cash
        peak, drawdown = account.initial_cash, 0.0
        for row in equity:
            peak = max(peak, row["total_value"])
            if peak:
                drawdown = max(drawdown, 1 - row["total_value"] / peak)
        return {
            "total_value": total,
            "return_pct": (total / account.initial_cash - 1) * 100,
            "max_drawdown_pct": drawdown * 100,
            "position_count": len(self.rows(ident, "positions")),
        }

    @staticmethod
    def _account(row) -> PaperAccount:
        return PaperAccount(
            row["id"], row["name"], row["backtest_id"], row["strategy_id"],
            row["strategy_name"], row["version_id"], row["status"],
            row["initial_cash"], row["cash"], row["activation_date"],
            json.loads(row["config_json"]), _decode(json.loads(row["state_json"])),
            row["error"], row["created_at"], row["updated_at"], row["ended_at"],
        )


def _encode(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return {"__qsys_type__": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__qsys_type__": "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {"__qsys_type__": "time", "value": value.isoformat()}
    if isinstance(value, Symbol):
        return {"__qsys_type__": "symbol", "value": str(value)}
    if isinstance(value, Mapping):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    raise TypeError(
        f"ctx.state contains unsupported {type(value).__name__}; "
        "store durable strategy state as JSON-compatible values"
    )


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    tag = value.get("__qsys_type__")
    if tag == "date":
        return date.fromisoformat(value["value"])
    if tag == "datetime":
        return datetime.fromisoformat(value["value"])
    if tag == "time":
        return time.fromisoformat(value["value"])
    if tag == "symbol":
        return Symbol.parse(value["value"])
    return {k: _decode(v) for k, v in value.items()}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
