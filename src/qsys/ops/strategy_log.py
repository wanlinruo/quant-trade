"""策略运行记录：一个事件一条，说清跑了什么、为什么没动作。

`RunReport` 由策略通过 ``ctx.report(...)`` 填写；运行时事件结束时补齐账户
快照与下单数，``build_record`` 合成一行中文结论。这里只做转述，不做推断。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


PHASE_LABELS = {
    "session_start": "日初结算",
    "auction": "竞价选股",
    "checkpoint": "检查点",
    "bar": "收盘撮合",
    "session_end": "收盘对账",
}


class RunReport:
    """策略在一个事件里的上报。字段后写覆盖，原因按 key 累加。"""

    def __init__(self) -> None:
        self._candidates: int | None = None
        self._routed: int | None = None
        self._passed: int | None = None
        self._reasons: dict[str, int] = {}
        self._note = ""

    def report(
        self,
        candidates: int | None = None,
        passed: int | None = None,
        reasons: Mapping[str, int] | None = None,
        note: str | None = None,
        routed: int | None = None,
    ) -> None:
        """`routed` 是"入档"数：池子里通过价格带与竞价量、真正进到闸门的那些。"""
        if candidates is not None:
            self._candidates = int(candidates)
        if routed is not None:
            self._routed = int(routed)
        if passed is not None:
            self._passed = int(passed)
        for key, count in (reasons or {}).items():
            self._reasons[key] = self._reasons.get(key, 0) + int(count)
        if note is not None:
            self._note = note

    @property
    def candidates(self) -> int | None:
        return self._candidates

    @property
    def routed(self) -> int | None:
        return self._routed

    @property
    def passed(self) -> int | None:
        return self._passed

    @property
    def reasons(self) -> dict[str, int]:
        return dict(self._reasons)

    @property
    def note(self) -> str:
        return self._note


@dataclass(frozen=True, slots=True)
class RunRecord:
    event_key: str
    session: date
    phase: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    cash: float
    market_value: float
    total_value: float
    position_count: int
    candidates: int | None
    routed: int | None
    passed: int | None
    orders: int
    rejects: int
    summary: str
    reasons: dict[str, int] = field(default_factory=dict)
    note: str = ""
    error: str = ""


RUN_COLUMNS: tuple[str, ...] = (
    "event_key",
    "session",
    "phase",
    "started_at",
    "finished_at",
    "duration_ms",
    "cash",
    "market_value",
    "total_value",
    "position_count",
    "candidates",
    "routed",
    "passed",
    "orders",
    "rejects",
    "summary",
    "reasons_json",
    "note",
    "error",
)


def _summarize(
    report: RunReport, *, orders: int, position_count: int, error: str
) -> str:
    if error:
        return f"事件失败：{error}"
    if report.note:
        return report.note
    if report.candidates is None:
        if orders:
            return f"持仓 {position_count} 只，下单 {orders} 笔"
        return f"持仓 {position_count} 只，无下单"
    if report.candidates == 0:
        return "候选池为空，未下单"
    stages = [f"候选 {report.candidates} 只"]
    if report.routed is not None:
        stages.append(f"入档 {report.routed} 只")
    stages.append(f"闸门通过 {report.passed or 0} 只")
    funnel = " → ".join(stages)
    return f"{funnel} → 下单 {orders} 笔" if orders else f"{funnel}，未下单"


def build_record(
    report: RunReport,
    *,
    event_key: str,
    session: date,
    phase: str,
    started_at: datetime,
    finished_at: datetime,
    cash: float,
    market_value: float,
    position_count: int,
    orders: int,
    rejects: int,
    error: str = "",
) -> RunRecord:
    return RunRecord(
        event_key=event_key,
        session=session,
        phase=phase,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=int((finished_at - started_at).total_seconds() * 1000),
        cash=cash,
        market_value=market_value,
        total_value=cash + market_value,
        position_count=position_count,
        candidates=report.candidates,
        routed=report.routed,
        passed=report.passed,
        orders=orders,
        rejects=rejects,
        summary=_summarize(
            report, orders=orders, position_count=position_count, error=error
        ),
        reasons=report.reasons,
        note=report.note,
        error=error,
    )


def run_values(record: RunRecord) -> tuple[Any, ...]:
    """按 ``RUN_COLUMNS`` 的顺序展开，原因编码为 JSON。"""
    return (
        record.event_key,
        record.session.isoformat(),
        record.phase,
        record.started_at.isoformat(timespec="seconds"),
        record.finished_at.isoformat(timespec="seconds"),
        record.duration_ms,
        record.cash,
        record.market_value,
        record.total_value,
        record.position_count,
        record.candidates,
        record.routed,
        record.passed,
        record.orders,
        record.rejects,
        record.summary,
        json.dumps(record.reasons, ensure_ascii=False),
        record.note,
        record.error,
    )


def decode_run(row: Any) -> dict[str, Any]:
    """将 sqlite 行转换成页面使用的字典，并解析原因 JSON。"""
    decoded = dict(row)
    decoded["reasons"] = json.loads(decoded.pop("reasons_json") or "{}")
    return decoded
