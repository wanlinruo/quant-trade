"""Windows 执行端的本地持久化去重台账。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol


class ReportLedger(Protocol):
    def seen_trade_ids(self) -> frozenset[str]: ...
    def closed_order_ids(self) -> frozenset[str]: ...
    def remember_trade(self, traded_id: str) -> None: ...
    def remember_closed(self, client_order_id: str) -> None: ...
    def remember_order(self, client_order_id: str, broker_order_id: str) -> None: ...
    def broker_order_id(self, client_order_id: str) -> str | None: ...
    def last_status(self, client_order_id: str) -> str | None: ...
    def remember_status(self, client_order_id: str, signature: str) -> None: ...


class MemoryReportLedger:
    def __init__(self) -> None:
        self.trades: set[str] = set()
        self.closed: set[str] = set()
        self.orders: dict[str, str] = {}
        self.statuses: dict[str, str] = {}

    def seen_trade_ids(self) -> frozenset[str]:
        return frozenset(self.trades)

    def closed_order_ids(self) -> frozenset[str]:
        return frozenset(self.closed)

    def remember_trade(self, traded_id: str) -> None:
        self.trades.add(traded_id)

    def remember_closed(self, client_order_id: str) -> None:
        self.closed.add(client_order_id)

    def remember_order(self, client_order_id: str, broker_order_id: str) -> None:
        self.orders[client_order_id] = broker_order_id

    def broker_order_id(self, client_order_id: str) -> str | None:
        return self.orders.get(client_order_id)

    def last_status(self, client_order_id: str) -> str | None:
        return self.statuses.get(client_order_id)

    def remember_status(self, client_order_id: str, signature: str) -> None:
        self.statuses[client_order_id] = signature


class SQLiteReportLedger:
    def __init__(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(target)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                traded_id TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS closed_orders (
                client_order_id TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS orders (
                client_order_id TEXT PRIMARY KEY,
                broker_order_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS order_statuses (
                client_order_id TEXT PRIMARY KEY,
                signature TEXT NOT NULL
            );
        """)
        self._db.commit()

    def seen_trade_ids(self) -> frozenset[str]:
        return frozenset(row[0] for row in self._db.execute("SELECT traded_id FROM trades"))

    def closed_order_ids(self) -> frozenset[str]:
        return frozenset(row[0] for row in self._db.execute(
            "SELECT client_order_id FROM closed_orders"
        ))

    def remember_trade(self, traded_id: str) -> None:
        self._db.execute("INSERT OR IGNORE INTO trades VALUES (?)", (traded_id,))
        self._db.commit()

    def remember_closed(self, client_order_id: str) -> None:
        self._db.execute("INSERT OR IGNORE INTO closed_orders VALUES (?)", (client_order_id,))
        self._db.commit()

    def remember_order(self, client_order_id: str, broker_order_id: str) -> None:
        self._db.execute(
            "INSERT INTO orders VALUES (?, ?) ON CONFLICT(client_order_id) "
            "DO UPDATE SET broker_order_id=excluded.broker_order_id",
            (client_order_id, broker_order_id),
        )
        self._db.commit()

    def broker_order_id(self, client_order_id: str) -> str | None:
        row = self._db.execute(
            "SELECT broker_order_id FROM orders WHERE client_order_id=?",
            (client_order_id,),
        ).fetchone()
        return None if row is None else str(row[0])

    def last_status(self, client_order_id: str) -> str | None:
        row = self._db.execute(
            "SELECT signature FROM order_statuses WHERE client_order_id=?",
            (client_order_id,),
        ).fetchone()
        return None if row is None else str(row[0])

    def remember_status(self, client_order_id: str, signature: str) -> None:
        self._db.execute(
            "INSERT INTO order_statuses VALUES (?, ?) ON CONFLICT(client_order_id) "
            "DO UPDATE SET signature=excluded.signature",
            (client_order_id, signature),
        )
        self._db.commit()
