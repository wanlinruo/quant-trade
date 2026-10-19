"""Intraday business-health checks for paper and shadow strategy chains."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

from qsys.ops.health import DEGRADED, DOWN, HEALTHY


# 竞价时刻是策略参数（auction_at），不是常量：09:25 撮合后决策的账户用 09:25:10。
# 这里必须跟着各自的参数走，否则会把跑对了的账户报成"事件失败"
# （2026-09-28 就这样误报了一上午：策略按 09:25:10 跑完，监控还在等 09:30:00）。
DEFAULT_AUCTION = time(9, 30)
EVENTS = (
    ("session_start", time(9, 15)),
    ("auction", DEFAULT_AUCTION),
    ("checkpoint", time(11, 25)),
    ("checkpoint", time(14, 25)),
    ("checkpoint", time(14, 50)),
    ("bar", time(15, 0)),
    ("session_end", time(15, 30)),
)
EVENT_WARN_AFTER_SECONDS = 10


def account_auction_time(config_json: str | None) -> time:
    """从模拟账户存档的配置里取 auction_at；取不到或不合法就用默认值。

    解析交给策略自己的 ``auction_time``，两处各写一份迟早走样。监控读不懂参数时
    宁可退回默认值，也不要因为解析失败就把整条链判死。
    """
    import json

    from qsys.strategy.structural.strategy import auction_time

    try:
        params = (json.loads(config_json or "{}") or {}).get("params") or {}
        return auction_time(params.get("auction_at"))
    except (ValueError, TypeError, json.JSONDecodeError):
        return DEFAULT_AUCTION


def recorded_auction_time(path: Path) -> time:
    """从核心实际落盘的最近竞价事件读取时刻；取不到则退回默认值。

    live-core 的独立巡检账户无权读取含凭据的生产配置，但它本来就能只读
    live-state。事件键是运行时按真实 Schedule 生成的，因此比监控侧常量可靠。
    """
    from qsys.strategy.structural.strategy import auction_time

    try:
        with sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True) as con:
            row = con.execute(
                "SELECT event_key FROM live_events "
                "WHERE event_key LIKE '%:auction:%' ORDER BY event_key DESC LIMIT 1"
            ).fetchone()
        if not row:
            return DEFAULT_AUCTION
        return auction_time(str(row[0]).split(":", 2)[2])
    except (OSError, sqlite3.Error, ValueError, TypeError, IndexError):
        return DEFAULT_AUCTION


@dataclass(frozen=True, slots=True)
class ChainAssessment:
    state: str
    reason_code: str
    detail: str
    expected: int = 0
    completed: int = 0


def _key(day, phase: str, at: time) -> str:
    return f"{day}:{phase}:{at.isoformat()}"


def _due(now: datetime, grace_seconds: float,
         auction_at: time = DEFAULT_AUCTION) -> list[tuple[str, time, float]]:
    result = []
    for phase, at in EVENTS:
        at = auction_at if phase == "auction" else at
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


def assess_shadow(path: Path, now: datetime, *, grace_seconds: float = 30,
                  auction_at: time = DEFAULT_AUCTION) -> ChainAssessment:
    """``auction_at`` 取影子核心承袭的那个模拟账户的参数，由调用方解析后传入。"""
    due = _due(now, grace_seconds, auction_at)
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
    try:
        with sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True) as con:
            con.row_factory = sqlite3.Row
            accounts = con.execute(
                "SELECT id,name,config_json FROM paper_accounts WHERE status='running' "
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
    names = {row["id"]: row["name"] for row in accounts}
    # 竞价时刻逐账户算：同时在跑的账户可以用不同的 auction_at
    # （09-28 就是旧账户 09:30、新账户 09:25:10 并存）。
    due_by_account = {
        row["id"]: _due(now, grace_seconds, account_auction_time(row["config_json"]))
        for row in accounts
    }
    if not any(due_by_account.values()):
        return ChainAssessment(HEALTHY, "strategy_events_not_due", "首个事件尚未到时")
    expected = [
        (ident, _key(now.date(), phase, at), age)
        for ident, due in due_by_account.items() for phase, at, age in due
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
