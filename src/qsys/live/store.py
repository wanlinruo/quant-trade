"""实盘核心的 SQLite 事务状态；券商账户仍是最终真值。"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from qsys.core.events import Reject
from qsys.core.portfolio import Portfolio, Position
from qsys.core.trading import Fill, Order
from qsys.core.types import Symbol
from qsys.live.messages import AccountSnapshot, OrderStatusEvent
from qsys.live.reconcile import is_cash_equivalent, unusable_market_values
from qsys.ops.strategy_log import RUN_COLUMNS, RunRecord, decode_run, run_values
from qsys.paper.store import _decode, _encode


class LiveStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS live_state (
                    id INTEGER PRIMARY KEY CHECK(id=1), cash REAL NOT NULL,
                    initial_value REAL NOT NULL, state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS live_positions (
                    symbol TEXT PRIMARY KEY, total_qty INTEGER NOT NULL,
                    available_qty INTEGER NOT NULL, cost_basis REAL NOT NULL,
                    last_price REAL NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS live_events (
                    event_key TEXT PRIMARY KEY, scheduled_at TEXT NOT NULL,
                    phase TEXT NOT NULL, status TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '', completed_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS live_orders (
                    order_id TEXT PRIMARY KEY, event_key TEXT NOT NULL,
                    symbol TEXT NOT NULL, side TEXT NOT NULL, qty INTEGER NOT NULL,
                    remaining_qty INTEGER NOT NULL, order_type TEXT NOT NULL,
                    limit_price REAL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS live_order_transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL,
                    state TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
                    at TEXT NOT NULL,
                    UNIQUE(order_id, state, detail)
                );
                CREATE TABLE IF NOT EXISTS live_runtime_fills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL,
                    symbol TEXT NOT NULL, side TEXT NOT NULL, qty INTEGER NOT NULL,
                    price REAL NOT NULL, fee REAL NOT NULL, filled_at TEXT NOT NULL,
                    broker_order_id TEXT, broker_trade_id TEXT,
                    UNIQUE(order_id, filled_at, qty, price)
                );
                CREATE TABLE IF NOT EXISTS live_runtime_rejects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL,
                    symbol TEXT NOT NULL, side TEXT NOT NULL, qty INTEGER NOT NULL,
                    reason TEXT NOT NULL, at TEXT NOT NULL,
                    UNIQUE(order_id, at, reason)
                );
                CREATE TABLE IF NOT EXISTS live_strategy_runs (
                    event_key TEXT PRIMARY KEY, session TEXT NOT NULL,
                    phase TEXT NOT NULL, started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL, duration_ms INTEGER NOT NULL,
                    cash REAL NOT NULL, market_value REAL NOT NULL,
                    total_value REAL NOT NULL, position_count INTEGER NOT NULL,
                    candidates INTEGER, routed INTEGER, passed INTEGER,
                    orders INTEGER NOT NULL, rejects INTEGER NOT NULL,
                    summary TEXT NOT NULL, reasons_json TEXT NOT NULL DEFAULT '{}',
                    note TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS live_runs_session
                    ON live_strategy_runs(session, started_at);
            """)
            # 已有库补列：入档数（池子 → 入档 → 闸门通过的中间一段）
            runs = {row[1] for row in con.execute("PRAGMA table_info(live_strategy_runs)")}
            if "routed" not in runs:
                con.execute("ALTER TABLE live_strategy_runs ADD COLUMN routed INTEGER")
            columns = {row[1] for row in con.execute("PRAGMA table_info(live_runtime_fills)")}
            if "broker_order_id" not in columns:
                con.execute("ALTER TABLE live_runtime_fills ADD COLUMN broker_order_id TEXT")
            if "broker_trade_id" not in columns:
                con.execute("ALTER TABLE live_runtime_fills ADD COLUMN broker_trade_id TEXT")
            con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_live_fill_trade "
                "ON live_runtime_fills(broker_trade_id) WHERE broker_trade_id IS NOT NULL"
            )

    def _connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def exists(self) -> bool:
        with self._connect() as con:
            return con.execute("SELECT 1 FROM live_state WHERE id=1").fetchone() is not None

    def record_run(self, record: RunRecord) -> None:
        placeholders = ",".join("?" for _ in RUN_COLUMNS)
        with self._connect() as con:
            con.execute(
                f"INSERT OR REPLACE INTO live_strategy_runs "
                f"({','.join(RUN_COLUMNS)}) VALUES ({placeholders})",
                run_values(record),
            )

    def strategy_runs(self, session: date) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM live_strategy_runs WHERE session=? ORDER BY started_at",
                (session.isoformat(),),
            ).fetchall()
        return [decode_run(row) for row in rows]

    def run_sessions(self, limit: int = 60) -> list[date]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT DISTINCT session FROM live_strategy_runs "
                "ORDER BY session DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [date.fromisoformat(row["session"]) for row in rows]

    def initialize(self, remote: AccountSnapshot) -> None:
        if self.exists():
            return
        portfolio = self._portfolio_from_remote(remote)
        initial = float(remote.total_asset or self._total_from_direct_values(remote))
        self.save(portfolio, {}, {s: p.avg_cost for s, p in portfolio.positions.items()},
                  at=remote.at, initial_value=initial)

    @staticmethod
    def _total_from_direct_values(remote: AccountSnapshot) -> float:
        """券商没给 `total_asset` 时按直取市值求和。

        原先按 `qty × avg_cost` 兜底，那是本项判定为错的反推（逆回购 1000×100
        得 20 万，直取市值只有 10 万）。而且它在生产里真会触发：
        `xtquant_adapter.query_account_details` 把缺失的 `total_asset` 转成 0.0，
        `or` 判其为假。缺市值就算不出起始资金，明确失败（#134）。
        """
        unusable = unusable_market_values(remote)
        total = remote.cash
        for raw_symbol, snapshot in remote.positions.items():
            if raw_symbol in unusable:
                raise ValueError(
                    f"{raw_symbol} 没有可用的券商市值，算不出起始资金；"
                    "先让执行代理上报持仓市值，不要按成本反推"
                )
            total += snapshot.market_value
        return total

    @staticmethod
    def _portfolio_from_remote(remote: AccountSnapshot) -> Portfolio:
        """现金等价物（国债逆回购）算起始现金，不进策略持仓。

        照搬快照会有两个后果：核心以为自己只有券商现金那点钱可用（生产实测
        账户现金 1.31 元、10 万在逆回购里），而逆回购还会被当成策略持仓带进
        本地账本，让对账两侧口径分叉（#134）。
        市值一律直取券商；取不到就不知道起始资金，明确失败而不是按 0 开张。
        """
        unusable = unusable_market_values(remote)
        parked = 0.0
        for raw_symbol, snapshot in remote.positions.items():
            if not is_cash_equivalent(raw_symbol):
                continue
            if raw_symbol in unusable:
                raise ValueError(
                    f"{raw_symbol} 是现金等价物但券商未给可用市值，无法确定起始资金；"
                    "先让执行代理上报持仓市值，不要按 0 或成本反推"
                )
            parked += snapshot.market_value
        portfolio = Portfolio(remote.cash + parked)
        for raw_symbol, snapshot in remote.positions.items():
            if is_cash_equivalent(raw_symbol):
                continue
            if snapshot.total_qty == 0:
                continue
            symbol = Symbol.parse(raw_symbol)
            portfolio.positions[symbol] = Position(
                symbol=symbol, total_qty=snapshot.total_qty,
                available_qty=snapshot.available_qty,
                cost_basis=snapshot.avg_cost * snapshot.total_qty,
            )
        return portfolio

    def load(self) -> tuple[Portfolio, dict[str, Any], dict[Symbol, float], float]:
        with self._connect() as con:
            row = con.execute("SELECT * FROM live_state WHERE id=1").fetchone()
            if row is None:
                raise LookupError("live state has not been initialized")
            positions = con.execute("SELECT * FROM live_positions").fetchall()
        portfolio = Portfolio(float(row["initial_value"]))
        portfolio.cash = float(row["cash"])
        prices: dict[Symbol, float] = {}
        for item in positions:
            if item["total_qty"] == 0:
                continue
            symbol = Symbol.parse(item["symbol"])
            portfolio.positions[symbol] = Position(
                symbol, item["total_qty"], item["available_qty"], item["cost_basis"]
            )
            prices[symbol] = float(item["last_price"])
        state = _decode(json.loads(row["state_json"]))
        return portfolio, state, prices, float(row["initial_value"])

    def save(
        self, portfolio: Portfolio, state: Mapping[str, Any],
        prices: Mapping[Symbol, float], *, at: datetime,
        initial_value: float | None = None,
    ) -> None:
        encoded = json.dumps(_encode(dict(state)), ensure_ascii=False)
        now = at.isoformat()
        with self._connect() as con:
            existing = con.execute("SELECT initial_value FROM live_state WHERE id=1").fetchone()
            initial = initial_value if initial_value is not None else float(existing[0])
            con.execute(
                "INSERT OR REPLACE INTO live_state VALUES (1,?,?,?,?)",
                (portfolio.cash, initial, encoded, now),
            )
            con.execute("DELETE FROM live_positions")
            con.executemany(
                "INSERT INTO live_positions VALUES (?,?,?,?,?,?)",
                [(str(symbol), pos.total_qty, pos.available_qty, pos.cost_basis,
                  float(prices.get(symbol, pos.avg_cost)), now)
                 for symbol, pos in portfolio.positions.items() if pos.total_qty != 0],
            )

    def set_day_start_value(self, value: float, *, at: datetime) -> None:
        """持久化当日风控基准，保证核心盘中重启后日损阈值不漂移。"""
        with self._connect() as con:
            con.execute(
                "UPDATE live_state SET initial_value=?,updated_at=? WHERE id=1",
                (value, at.isoformat()),
            )

    def begin_event(self, key: str, at: datetime, phase: str) -> bool:
        with self._connect() as con:
            row = con.execute(
                "SELECT status FROM live_events WHERE event_key=?", (key,)
            ).fetchone()
            if row is not None:
                return row["status"] == "running"
            con.execute(
                "INSERT INTO live_events(event_key,scheduled_at,phase,status) VALUES (?,?,?,?)",
                (key, at.isoformat(), phase, "running"),
            )
        return True

    def event_detail(self, key: str) -> str:
        with self._connect() as con:
            row = con.execute(
                "SELECT detail FROM live_events WHERE event_key=?", (key,)
            ).fetchone()
        return "" if row is None else str(row["detail"] or "")

    def record_event_failure(self, key: str, detail: str) -> None:
        """Keep an event retryable while making its latest failure observable."""
        with self._connect() as con:
            con.execute(
                "UPDATE live_events SET detail=? WHERE event_key=? AND status='running'",
                (detail, key),
            )

    def fail_event(self, key: str, detail: str, *, at: datetime) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE live_events SET status='failed',detail=?,completed_at=? "
                "WHERE event_key=? AND status='running'",
                (detail, at.isoformat(), key),
            )

    def commit_event(
        self, key: str, portfolio: Portfolio, state: Mapping[str, Any],
        prices: Mapping[Symbol, float], *, at: datetime, detail: str = "",
    ) -> None:
        # save 和事件完成共用同一连接，避免重启时出现“状态已变但事件未完成”。
        encoded = json.dumps(_encode(dict(state)), ensure_ascii=False)
        now = at.isoformat()
        with self._connect() as con:
            initial = float(con.execute(
                "SELECT initial_value FROM live_state WHERE id=1"
            ).fetchone()[0])
            con.execute("INSERT OR REPLACE INTO live_state VALUES (1,?,?,?,?)",
                        (portfolio.cash, initial, encoded, now))
            con.execute("DELETE FROM live_positions")
            con.executemany(
                "INSERT INTO live_positions VALUES (?,?,?,?,?,?)",
                [(str(s), p.total_qty, p.available_qty, p.cost_basis,
                  float(prices.get(s, p.avg_cost)), now)
                 for s, p in portfolio.positions.items() if p.total_qty != 0],
            )
            con.execute(
                "UPDATE live_events SET status='completed',detail=?,completed_at=? "
                "WHERE event_key=?", (detail, now, key),
            )

    def record_orders(
        self, event_key: str, orders: Sequence[Order],
        *, sent_ids: frozenset[str] | None = None,
    ) -> None:
        now = datetime.now().isoformat()
        if sent_ids is None:
            sent_ids = frozenset(order.order_id for order in orders)
        with self._connect() as con:
            con.executemany(
                "INSERT OR IGNORE INTO live_orders VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(o.order_id, event_key, str(o.symbol), o.side.value, o.qty, o.qty,
                  o.order_type.value, o.limit_price, "pending", o.created_at.isoformat(), now)
                 for o in orders],
            )
            for order in orders:
                con.execute(
                    "INSERT OR IGNORE INTO live_order_transitions"
                    "(order_id,state,detail,at) VALUES (?,?,?,?)",
                    (order.order_id, "created", "", now),
                )
                if order.order_id in sent_ids:
                    for state in ("risk_passed", "sent"):
                        con.execute(
                            "INSERT OR IGNORE INTO live_order_transitions"
                            "(order_id,state,detail,at) VALUES (?,?,?,?)",
                            (order.order_id, state, "", now),
                        )

    def record_results(
        self, fills: Sequence[Fill], rejects: Sequence[Reject], remaining: Mapping[str, int]
    ) -> None:
        now = datetime.now().isoformat()
        with self._connect() as con:
            con.executemany(
                "INSERT OR IGNORE INTO live_runtime_fills"
                "(order_id,symbol,side,qty,price,fee,filled_at,broker_order_id,broker_trade_id) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                [(f.order_id, str(f.symbol), f.side.value, f.qty, f.price, f.fee,
                  f.filled_at.isoformat(), f.broker_order_id, f.broker_trade_id) for f in fills],
            )
            con.executemany(
                "INSERT OR IGNORE INTO live_runtime_rejects"
                "(order_id,symbol,side,qty,reason,at) VALUES (?,?,?,?,?,?)",
                [(r.order_id, str(r.symbol), r.side.value, r.qty, r.reason, r.at.isoformat())
                 for r in rejects],
            )
            for order_id in {x.order_id for x in fills} | {x.order_id for x in rejects}:
                left = int(remaining.get(order_id, 0))
                status = "pending" if left else "terminal"
                con.execute(
                    "UPDATE live_orders SET remaining_qty=?,status=?,updated_at=? WHERE order_id=?",
                    (left, status, now, order_id),
                )

    def commit_reports(
        self, portfolio: Portfolio, state: Mapping[str, Any],
        prices: Mapping[Symbol, float], fills: Sequence[Fill],
        rejects: Sequence[Reject], statuses: Sequence[OrderStatusEvent],
        remaining: Mapping[str, int], *, at: datetime,
    ) -> None:
        """将成交去重记录与其导致的组合状态放在同一 SQLite 事务。"""
        encoded = json.dumps(_encode(dict(state)), ensure_ascii=False)
        now = at.isoformat()
        with self._connect() as con:
            initial = float(con.execute(
                "SELECT initial_value FROM live_state WHERE id=1"
            ).fetchone()[0])
            con.execute(
                "INSERT OR REPLACE INTO live_state VALUES (1,?,?,?,?)",
                (portfolio.cash, initial, encoded, now),
            )
            con.execute("DELETE FROM live_positions")
            con.executemany(
                "INSERT INTO live_positions VALUES (?,?,?,?,?,?)",
                [(str(symbol), pos.total_qty, pos.available_qty, pos.cost_basis,
                  float(prices.get(symbol, pos.avg_cost)), now)
                 for symbol, pos in portfolio.positions.items() if pos.total_qty != 0],
            )
            con.executemany(
                "INSERT OR IGNORE INTO live_runtime_fills"
                "(order_id,symbol,side,qty,price,fee,filled_at,broker_order_id,broker_trade_id) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                [(f.order_id, str(f.symbol), f.side.value, f.qty, f.price, f.fee,
                  f.filled_at.isoformat(), f.broker_order_id, f.broker_trade_id)
                 for f in fills],
            )
            con.executemany(
                "INSERT OR IGNORE INTO live_runtime_rejects"
                "(order_id,symbol,side,qty,reason,at) VALUES (?,?,?,?,?,?)",
                [(r.order_id, str(r.symbol), r.side.value, r.qty, r.reason,
                  r.at.isoformat()) for r in rejects],
            )
            for status in statuses:
                con.execute(
                    "UPDATE live_orders SET remaining_qty=?,status=?,updated_at=? "
                    "WHERE order_id=?",
                    (status.remaining_qty, status.status, now, status.client_order_id),
                )
                con.execute(
                    "INSERT OR IGNORE INTO live_order_transitions"
                    "(order_id,state,detail,at) VALUES (?,?,?,?)",
                    (status.client_order_id, status.status,
                     f"{status.filled_qty}/{status.ordered_qty}", status.at.isoformat()),
                )
            for order_id in {x.order_id for x in fills} | {x.order_id for x in rejects}:
                left = int(remaining.get(order_id, 0))
                terminal = "pending" if left else "terminal"
                con.execute(
                    "UPDATE live_orders SET remaining_qty=?,status=?,updated_at=? "
                    "WHERE order_id=?",
                    (left, terminal, now, order_id),
                )
            for fill in fills:
                left = int(remaining.get(fill.order_id, 0))
                state_name = "filled" if left == 0 else "partially_filled"
                detail = fill.broker_trade_id or (
                    f"{fill.filled_at.isoformat()}:{fill.qty}:{fill.price}"
                )
                con.execute(
                    "INSERT OR IGNORE INTO live_order_transitions"
                    "(order_id,state,detail,at) VALUES (?,?,?,?)",
                    (fill.order_id, state_name, detail, fill.filled_at.isoformat()),
                )
            for reject in rejects:
                con.execute(
                    "INSERT OR IGNORE INTO live_order_transitions"
                    "(order_id,state,detail,at) VALUES (?,?,?,?)",
                    (reject.order_id, "rejected", reject.reason, reject.at.isoformat()),
                )

    def order_transitions(self, order_id: str) -> tuple[str, ...]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT state FROM live_order_transitions WHERE order_id=? ORDER BY id",
                (order_id,),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def fill_recorded(self, fill: Fill) -> bool:
        with self._connect() as con:
            if fill.broker_trade_id:
                row = con.execute(
                    "SELECT 1 FROM live_runtime_fills WHERE broker_trade_id=?",
                    (fill.broker_trade_id,),
                ).fetchone()
            else:
                row = con.execute(
                    "SELECT 1 FROM live_runtime_fills WHERE order_id=? AND filled_at=? "
                    "AND qty=? AND price=?",
                    (fill.order_id, fill.filled_at.isoformat(), fill.qty, fill.price),
                ).fetchone()
        return row is not None

    def reject_recorded(self, reject: Reject) -> bool:
        with self._connect() as con:
            row = con.execute(
                "SELECT 1 FROM live_runtime_rejects WHERE order_id=? AND reason=?",
                (reject.order_id, reject.reason),
            ).fetchone()
        return row is not None

    def record_statuses(self, statuses: Sequence[OrderStatusEvent]) -> None:
        now = datetime.now().isoformat()
        with self._connect() as con:
            for status in statuses:
                con.execute(
                    "UPDATE live_orders SET remaining_qty=?,status=?,updated_at=? "
                    "WHERE order_id=?",
                    (status.remaining_qty, status.status, now, status.client_order_id),
                )

    def pending_orders(self) -> tuple[tuple[Order, int], ...]:
        from qsys.core.trading import OrderType, Side

        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM live_orders WHERE remaining_qty>0 "
                "AND status NOT IN ('filled','cancelled','rejected','terminal')"
            ).fetchall()
        return tuple(
            (
                Order(
                    row["order_id"], Symbol.parse(row["symbol"]), Side(row["side"]),
                    row["qty"], OrderType(row["order_type"]), row["limit_price"],
                    datetime.fromisoformat(row["created_at"]),
                ),
                int(row["remaining_qty"]),
            )
            for row in rows
        )
