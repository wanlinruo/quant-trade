"""异步发送尝试账本；独立于旧回报台账和Live业务库。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from qsys.live.messages import DispatchStatusEvent, OrderCommand


def canonical_command(command: OrderCommand) -> str:
    return json.dumps(json.loads(command.model_dump_json()), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=True, allow_nan=False)


class DispatchConflictError(RuntimeError):
    """同一业务ID出现不同意图；不得拒单释放原意图或覆盖旧事实。"""


class DispatchStore:
    def __init__(self, path: Path, *, scope: str) -> None:
        self.path = path
        self.closed = False
        if path.is_symlink():
            raise ValueError("dispatch database must not be a symlink")
        if not path.exists():
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS dispatch_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            binding = json.dumps(dict(schema_version=1, scope=scope), sort_keys=True)
            old = db.execute("SELECT value FROM dispatch_metadata WHERE key='binding'").fetchone()
            if old is not None and old[0] != binding:
                raise ValueError("dispatch store scope/schema mismatch")
            db.execute("INSERT OR IGNORE INTO dispatch_metadata VALUES ('binding',?)", (binding,))
            db.execute('''CREATE TABLE IF NOT EXISTS dispatch_attempts (
                client_order_id TEXT PRIMARY KEY, command_json TEXT NOT NULL,
                command_sha256 TEXT NOT NULL, attempt_id TEXT NOT NULL,
                session_nonce TEXT NOT NULL, state TEXT NOT NULL,
                reason TEXT NOT NULL, updated_at TEXT NOT NULL, receipt_json TEXT,
                CHECK(state IN ('dispatching','transport_received','unknown','rejected_before_send')))''')

    @contextmanager
    def connection(self):
        if self.closed:
            raise RuntimeError("dispatch store is closed")
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def close(self) -> None:
        self.closed = True

    def read(self, client_order_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM dispatch_attempts WHERE client_order_id=?",
                             (client_order_id,)).fetchone()
            return dict(row) if row is not None else None

    def existing(self, command: OrderCommand) -> dict | None:
        row = self.read(command.client_order_id)
        if row is not None and row['command_json'] != canonical_command(command):
            raise DispatchConflictError("immutable dispatch intent differs")
        return row

    def claim(self, command: OrderCommand, attempt_id: str, session_nonce: str, at: datetime) -> dict:
        raw = canonical_command(command)
        with self.connection() as db:
            # The owner lock is held by the validation factory. The primary
            # key additionally prevents two connections claiming one intent.
            db.execute("INSERT INTO dispatch_attempts VALUES (?,?,?,?,?,'dispatching','',?,NULL)",
                       (command.client_order_id, raw, hashlib.sha256(raw.encode()).hexdigest(),
                        attempt_id, session_nonce, at.isoformat()))
        return self.read(command.client_order_id)

    def reject_before_send(self, command: OrderCommand, attempt_id: str,
                           session_nonce: str, reason: str, at: datetime) -> dict:
        raw = canonical_command(command)
        with self.connection() as db:
            db.execute("INSERT INTO dispatch_attempts VALUES (?,?,?,?,?,'rejected_before_send',?,?,NULL)",
                       (command.client_order_id, raw, hashlib.sha256(raw.encode()).hexdigest(),
                        attempt_id, session_nonce, reason, at.isoformat()))
        return self.read(command.client_order_id)

    def has_unresolved(self) -> bool:
        with self.connection() as db:
            return db.execute("SELECT 1 FROM dispatch_attempts WHERE state IN ('unknown','dispatching') LIMIT 1").fetchone() is not None

    def update(self, client_order_id: str, *, state: str, reason: str,
               at: datetime, receipt_json: str | None = None) -> dict:
        if state not in {'unknown', 'transport_received'}:
            raise ValueError("invalid dispatch transition")
        with self.connection() as db:
            count = db.execute('''UPDATE dispatch_attempts SET state=?, reason=?, updated_at=?,
                receipt_json=COALESCE(?,receipt_json)
                WHERE client_order_id=? AND state='dispatching' ''',
                (state, reason, at.isoformat(), receipt_json, client_order_id)).rowcount
            if count != 1:
                raise RuntimeError("dispatch attempt not in sending state")
        return self.read(client_order_id)

    def recover(self, at: datetime) -> int:
        with self.connection() as db:
            return db.execute("""UPDATE dispatch_attempts SET state='unknown',
                reason='gateway_restart_requires_reconciliation',updated_at=?
                WHERE state IN ('dispatching','transport_received')""", (at.isoformat(),)).rowcount

    @staticmethod
    def event(row: dict) -> DispatchStatusEvent:
        command = OrderCommand.model_validate_json(row['command_json'])
        receipt = row['receipt_json']
        return DispatchStatusEvent(
            client_order_id=command.client_order_id, strategy_id=command.strategy_id,
            session_id=command.session_id, attempt_id=row['attempt_id'],
            session_nonce=row['session_nonce'], command_sha256=row['command_sha256'],
            state=row['state'], reason=row['reason'], at=datetime.fromisoformat(row['updated_at']),
            transport_ack_sha256=hashlib.sha256(receipt.encode()).hexdigest() if receipt is not None else None,
        )
