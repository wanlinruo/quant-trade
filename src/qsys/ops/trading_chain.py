"""Intraday business-health checks for paper and shadow strategy chains."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

from qsys.ops.health import DEGRADED, DOWN, HEALTHY


EVENTS = (
    ("session_start", time(9, 15)),
    ("auction", time(9, 30)),
    ("checkpoint", time(11, 25)),
    ("checkpoint", time(14, 25)),
    ("checkpoint", time(14, 50)),
    ("bar", time(15, 0)),
    ("session_end", time(15, 30)),
)
EVENT_WARN_AFTER_SECONDS = 10


@dataclass(frozen=True, slots=True)
class ChainAssessment:
    state: str
    reason_code: str
    detail: str
    expected: int = 0
    completed: int = 0


def _key(day, phase: str, at: time) -> str:
    return f"{day}:{phase}:{at.isoformat()}"


def _due(now: datetime, grace_seconds: float) -> list[tuple[str, time, float]]:
    result = []
    for phase, at in EVENTS:
        age = (now - datetime.combine(now.date(), at)).total_seconds()
        if age >= EVENT_WARN_AFTER_SECONDS:
            result.append((phase, at, min(age, grace_seconds + 1)))
    return result


def _assess_rows(
    expected_rows: list[tuple[str, str, float]],
    events: dict[tuple[str, str], sqlite3.Row],
    errors: dict[tuple[str, str], str],
    *,
    grace_seconds: float,
) -> ChainAssessment:
    failures: list[str] = []
    delays: list[str] = []
    completed = 0
    for owner, key, age in expected_rows:
        row = events.get((owner, key))
        label = f"{owner + ':' if owner else ''}{key.split(':', 1)[1]}"
        if row is None:
            target = failures if age > grace_seconds else delays
            target.append(f"{label} 未落库")
            continue
        detail = str(row["detail"] or "")
        error = errors.get((owner, key), "")
        if row["status"] == "running" and not detail and not error:
            target = failures if age > grace_seconds else delays
            target.append(f"{label} 仍在执行")
            continue
        if row["status"] != "completed" or detail or error:
            message = detail or error or str(row["status"])
            failures.append(f"{label} {message}")
            continue
        completed += 1
    if failures:
        return ChainAssessment(
            DOWN, "strategy_event_failed", "；".join(failures[:10]),
            len(expected_rows), completed,
        )
    if delays:
        return ChainAssessment(
            DEGRADED, "strategy_event_delayed", "；".join(delays[:10]),
            len(expected_rows), completed,
        )
    return ChainAssessment(
        HEALTHY, "strategy_events_current",
        f"应执行 {len(expected_rows)} 个，已正常完成 {completed} 个",
        len(expected_rows), completed,
    )


def assess_shadow(path: Path, now: datetime, *, grace_seconds: float = 30) -> ChainAssessment:
    due = _due(now, grace_seconds)
    expected = [("", _key(now.date(), phase, at), age) for phase, at, age in due]
    if not expected:
        return ChainAssessment(HEALTHY, "strategy_events_not_due", "首个事件尚未到时")
    try:
        with sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT event_key,status,detail FROM live_events "
                "WHERE event_key LIKE ?", (f"{now.date()}:%",),
            ).fetchall()
            runs = con.execute(
                "SELECT event_key,error FROM live_strategy_runs WHERE session=?",
                (now.date().isoformat(),),
            ).fetchall()
    except (OSError, sqlite3.Error) as exc:
        return ChainAssessment(DOWN, "strategy_state_unavailable", str(exc))
    events = {("", row["event_key"]): row for row in rows}
    errors = {("", row["event_key"]): str(row["error"] or "") for row in runs}
    return _assess_rows(expected, events, errors, grace_seconds=grace_seconds)


def assess_paper(path: Path, now: datetime, *, grace_seconds: float = 90) -> ChainAssessment:
    due = _due(now, grace_seconds)
    try:
        with sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True) as con:
            con.row_factory = sqlite3.Row
            accounts = con.execute(
                "SELECT id,name FROM paper_accounts WHERE status='running' "
                "AND activation_date <= ?", (now.date().isoformat(),),
            ).fetchall()
            if not accounts:
                return ChainAssessment(
                    HEALTHY, "strategy_accounts_inactive", "没有已生效的运行中模拟账户"
                )
            rows = con.execute(
                "SELECT account_id,event_key,status,detail FROM paper_events "
                "WHERE event_key LIKE ?", (f"{now.date()}:%",),
            ).fetchall()
            runs = con.execute(
                "SELECT account_id,event_key,error FROM paper_strategy_runs "
                "WHERE session=?", (now.date().isoformat(),),
            ).fetchall()
    except (OSError, sqlite3.Error) as exc:
        return ChainAssessment(DOWN, "strategy_state_unavailable", str(exc))
    if not due:
        return ChainAssessment(HEALTHY, "strategy_events_not_due", "首个事件尚未到时")
    names = {row["id"]: row["name"] for row in accounts}
    expected = [
        (ident, _key(now.date(), phase, at), age)
        for ident in names for phase, at, age in due
    ]
    events = {(row["account_id"], row["event_key"]): row for row in rows}
    errors = {
        (row["account_id"], row["event_key"]): str(row["error"] or "")
        for row in runs
    }
    assessed = _assess_rows(expected, events, errors, grace_seconds=grace_seconds)
    detail = assessed.detail
    for ident, name in names.items():
        detail = detail.replace(f"{ident}:", f"{name}:")
    return ChainAssessment(
        assessed.state, assessed.reason_code, detail,
        assessed.expected, assessed.completed,
    )
