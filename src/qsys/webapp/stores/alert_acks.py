"""告警“确认已知悉”存储（#662 计划 06a；设计文档 §13 第 2 项）。

Web 自有的独立 SQLite，只追加：谁、何时、确认了哪个（键, 对象）、确认到哪一次发生（through）。
不改变告警原始记录与任何业务状态；不记录密码等凭据。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class AckRecord:
    key: str
    subject: str
    by: str
    acked_at: datetime
    through: datetime


def _iso(at: datetime) -> str:
    if at.tzinfo is None:
        raise ValueError("时间必须带时区")
    return at.astimezone(timezone.utc).isoformat()


class AlertAckStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _connect(self, write: bool) -> sqlite3.Connection:
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(self.path, timeout=5)
            con.execute("""CREATE TABLE IF NOT EXISTS alert_acks (
                id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, subject TEXT NOT NULL,
                by TEXT NOT NULL, acked_at_utc TEXT NOT NULL, through_utc TEXT NOT NULL)""")
            return con
        return sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)

    def ack(self, key: str, subject: str, *, by: str, through: datetime, at: datetime) -> AckRecord:
        with closing(self._connect(write=True)) as con, con:
            con.execute("INSERT INTO alert_acks (key, subject, by, acked_at_utc, through_utc) VALUES (?, ?, ?, ?, ?)",
                        (key, subject, by, _iso(at), _iso(through)))
        return AckRecord(key, subject, by, at, through)

    def latest(self, until: datetime | None = None) -> dict[tuple[str, str], AckRecord]:
        """每个（键, 对象）最近一次确认；until 给出时只取 acked_at 早于该时刻的确认（按日回看）。"""
        if not self.path.is_file():
            return {}
        sql = "SELECT key, subject, by, acked_at_utc, through_utc FROM alert_acks"
        args: tuple = ()
        if until is not None:
            sql += " WHERE acked_at_utc < ?"
            args = (_iso(until),)
        out: dict[tuple[str, str], AckRecord] = {}
        with closing(self._connect(write=False)) as con:
            for key, subject, by, acked, through in con.execute(sql + " ORDER BY acked_at_utc, id", args):
                out[(key, subject)] = AckRecord(key, subject, by, datetime.fromisoformat(acked),
                                                datetime.fromisoformat(through))
        return out
