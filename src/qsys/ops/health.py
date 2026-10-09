"""服务健康时段、心跳策略与本地历史存储。

这是运营可观测性模型，不参与 LIVE 风控落闸。所有落库时间统一 UTC；没有观测
记录的时间不能推断为健康。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")

HEALTHY = "healthy"
DEGRADED = "degraded"
DOWN = "down"
UNKNOWN = "unknown"
PLANNED = "planned"

PREPARE = "prepare"
MORNING = "morning"
LUNCH = "lunch"
AFTERNOON = "afternoon"
OFF_HOURS = "off_hours"
CALENDAR_UNKNOWN = "calendar_unknown"


def _local(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=SHANGHAI)
    return moment.astimezone(SHANGHAI)


def _utc(moment: datetime) -> datetime:
    return _local(moment).astimezone(timezone.utc)


def trading_phase(moment: datetime, trading_days: set[date] | None) -> str:
    """按上海时间与权威交易日集合返回健康策略时段。"""
    local = _local(moment)
    if trading_days is None:
        return CALENDAR_UNKNOWN
    if local.date() not in trading_days:
        return OFF_HOURS
    clock = local.time().replace(tzinfo=None)
    if time(8, 45) <= clock < time(9, 0):
        return PREPARE
    if time(9, 0) <= clock < time(11, 30):
        return MORNING
    if time(11, 30) <= clock < time(13, 0):
        return LUNCH
    if time(13, 0) <= clock < time(15, 40):
        return AFTERNOON
    return OFF_HOURS


@dataclass(frozen=True, slots=True)
class HeartbeatPolicy:
    expected_interval_s: float
    warn_after_s: float
    down_after_s: float


@dataclass(slots=True)
class HeartbeatCadence:
    """只控制心跳写入时机，不控制业务循环或请求消费。"""

    next_due: float = 0.0
    interval_s: float | None = None

    def due(self, monotonic: float, interval_s: float) -> bool:
        interval_s = max(0.1, interval_s)
        # A phase/activity transition to a faster cadence must take effect in
        # this loop, rather than waiting for the old (possibly 30 s) deadline.
        if self.interval_s is None or interval_s < self.interval_s:
            self.next_due = min(self.next_due, monotonic)
        self.interval_s = interval_s
        if monotonic < self.next_due:
            return False
        self.next_due = monotonic + interval_s
        return True


def heartbeat_policy(source: str, phase: str, *, active: bool = False) -> HeartbeatPolicy:
    """返回发送/展示策略；日历未知时始终采用保守高频。"""
    critical = phase in (PREPARE, MORNING, AFTERNOON, CALENDAR_UNKNOWN)
    if source in ("core", "agent"):
        return HeartbeatPolicy(1, 3, 10)
    if source == "market":
        if active or critical:
            return HeartbeatPolicy(1, 3, 10)
        if phase == LUNCH:
            return HeartbeatPolicy(2, 5, 10)
        return HeartbeatPolicy(30, 60, 90)
    if source == "paper":
        high_frequency = active or phase == CALENDAR_UNKNOWN
        return HeartbeatPolicy(2, 5, 10) if high_frequency else HeartbeatPolicy(30, 60, 90)
    return HeartbeatPolicy(30, 60, 90)


@dataclass(frozen=True, slots=True)
class HeartbeatAssessment:
    state: str
    reason_code: str
    age_s: float | None
    phase: str
    policy: HeartbeatPolicy


def assess_heartbeat(
    source: str,
    last_beat: datetime | None,
    now: datetime,
    trading_days: set[date] | None,
    *,
    active: bool = False,
    advertised_interval_s: float | None = None,
    advertised_phase: str | None = None,
) -> HeartbeatAssessment:
    phase = trading_phase(now, trading_days)
    policy = heartbeat_policy(source, phase, active=active)
    cadence_mismatch = False
    # QMT's calendar can lag the authoritative Linux calendar briefly after a
    # long holiday.  While the gateway still advertises its legal off-hours
    # cadence, judge liveness against that cadence instead of declaring it
    # stopped every ten seconds.  The mismatch remains visible as DEGRADED and
    # a genuinely stopped gateway still becomes DOWN within 90 seconds.
    if (
        source == "market"
        and advertised_phase == OFF_HOURS
        and advertised_interval_s == 30
        and policy.expected_interval_s < advertised_interval_s
    ):
        cadence_mismatch = True
        policy = HeartbeatPolicy(
            advertised_interval_s,
            max(policy.warn_after_s, advertised_interval_s * 2),
            max(policy.down_after_s, advertised_interval_s * 3),
        )
    if last_beat is None:
        return HeartbeatAssessment(DOWN, "heartbeat_missing", None, phase, policy)
    age = (_utc(now) - _utc(last_beat)).total_seconds()
    if age < -2:
        return HeartbeatAssessment(UNKNOWN, "clock_skew", age, phase, policy)
    age = max(0.0, age)
    if phase == CALENDAR_UNKNOWN:
        return HeartbeatAssessment(UNKNOWN, "calendar_unavailable", age, phase, policy)
    if age > policy.down_after_s:
        return HeartbeatAssessment(DOWN, "heartbeat_stale", age, phase, policy)
    if age > policy.warn_after_s:
        return HeartbeatAssessment(DEGRADED, "heartbeat_delayed", age, phase, policy)
    if cadence_mismatch:
        return HeartbeatAssessment(
            DEGRADED, "heartbeat_cadence_mismatch", age, phase, policy
        )
    return HeartbeatAssessment(HEALTHY, "heartbeat_fresh", age, phase, policy)


@dataclass(frozen=True, slots=True)
class HealthObservation:
    service_id: str
    observed_at_utc: datetime
    phase: str
    expected_interval_s: float
    warn_after_s: float
    down_after_s: float
    state: str
    process_state: str
    business_state: str
    reason_code: str
    source: str
    last_beat_at_utc: datetime | None = None
    details: dict | None = None

    def normalized(self) -> HealthObservation:
        return HealthObservation(
            **{
                **asdict(self),
                "observed_at_utc": _utc(self.observed_at_utc),
                "last_beat_at_utc": _utc(self.last_beat_at_utc)
                if self.last_beat_at_utc else None,
            }
        )


class HealthStore:
    """只记状态变化与周期检查点的 SQLite WAL 存储。"""

    def __init__(self, path: Path, *, checkpoint_interval: timedelta = timedelta(minutes=5)):
        self.path = Path(path)
        self.checkpoint_interval = checkpoint_interval
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=5)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=5000")
        return con

    def _initialize(self) -> None:
        with self._connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript("""
                CREATE TABLE IF NOT EXISTS health_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    service_id TEXT NOT NULL,
                    observed_at_utc TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    expected_interval_s REAL NOT NULL,
                    warn_after_s REAL NOT NULL,
                    down_after_s REAL NOT NULL,
                    state TEXT NOT NULL,
                    process_state TEXT NOT NULL,
                    business_state TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    source TEXT NOT NULL,
                    last_beat_at_utc TEXT,
                    details TEXT NOT NULL,
                    checkpoint INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS ix_health_service_time
                ON health_observations(service_id, observed_at_utc);
                CREATE TABLE IF NOT EXISTS health_recovery_candidates (
                    service_id TEXT PRIMARY KEY,
                    healthy_count INTEGER NOT NULL
                );
            """)

    @staticmethod
    def _signature(row: sqlite3.Row | None) -> tuple | None:
        if row is None:
            return None
        return tuple(row[name] for name in (
            "phase", "expected_interval_s", "warn_after_s", "down_after_s", "state",
            "process_state", "business_state", "reason_code", "source",
            "details",
        ))

    def record(self, observation: HealthObservation) -> bool:
        item = observation.normalized()
        payload = json.dumps(item.details or {}, ensure_ascii=False, sort_keys=True)
        values = (
            item.phase, item.expected_interval_s, item.warn_after_s, item.down_after_s,
            item.state, item.process_state, item.business_state, item.reason_code,
            item.source, item.last_beat_at_utc.isoformat() if item.last_beat_at_utc else None,
            payload,
        )
        with self._connect() as con:
            previous = con.execute(
                "SELECT * FROM health_observations WHERE service_id=? "
                "ORDER BY observed_at_utc DESC, id DESC LIMIT 1", (item.service_id,),
            ).fetchone()
            elapsed = None if previous is None else (
                item.observed_at_utc - datetime.fromisoformat(previous["observed_at_utc"])
            )
            if previous is not None and previous["state"] in (DEGRADED, DOWN, UNKNOWN) \
                    and item.state == HEALTHY:
                candidate = con.execute(
                    "SELECT healthy_count FROM health_recovery_candidates WHERE service_id=?",
                    (item.service_id,),
                ).fetchone()
                if candidate is None:
                    con.execute(
                        "INSERT INTO health_recovery_candidates VALUES (?, 1)",
                        (item.service_id,),
                    )
                    return False
                con.execute(
                    "DELETE FROM health_recovery_candidates WHERE service_id=?",
                    (item.service_id,),
                )
            elif item.state != HEALTHY:
                con.execute(
                    "DELETE FROM health_recovery_candidates WHERE service_id=?",
                    (item.service_id,),
                )
            changed = self._signature(previous) != values[:9] + (payload,)
            checkpoint = not changed and elapsed is not None and elapsed >= self.checkpoint_interval
            if not changed and not checkpoint:
                return False
            con.execute(
                "INSERT INTO health_observations (service_id, observed_at_utc, phase, "
                "expected_interval_s, warn_after_s, down_after_s, state, process_state, "
                "business_state, reason_code, source, last_beat_at_utc, details, checkpoint) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (item.service_id, item.observed_at_utc.isoformat(), *values, int(checkpoint)),
            )
        return True

    def latest(self, service_id: str | None = None) -> list[dict]:
        params: tuple = ()
        where = ""
        if service_id is not None:
            where, params = "WHERE service_id=?", (service_id,)
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY service_id "
                "ORDER BY observed_at_utc DESC, id DESC) AS rank FROM health_observations "
                f"{where}) WHERE rank=1 ORDER BY service_id", params,
            ).fetchall()
        return [self._row(row) for row in rows]

    def history(self, start: datetime, end: datetime, service_id: str | None = None) -> list[dict]:
        clauses = ["observed_at_utc BETWEEN ? AND ?"]
        params: list[object] = [_utc(start).isoformat(), _utc(end).isoformat()]
        if service_id is not None:
            clauses.append("service_id=?")
            params.append(service_id)
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM health_observations WHERE " + " AND ".join(clauses)
                + " ORDER BY service_id, observed_at_utc, id", params,
            ).fetchall()
        return [self._row(row) for row in rows]

    def timeline(
        self, start: datetime, end: datetime, *, max_gap: timedelta = timedelta(minutes=10),
    ) -> dict[str, list[dict]]:
        """把稀疏事件变成区间；超过检查点容忍时间的部分明确标为 unknown。"""
        begin, finish = _utc(start), _utc(end)
        if finish <= begin:
            return {}
        rows = self.history(begin - max_gap, finish)
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(row["service_id"], []).append(row)
        result: dict[str, list[dict]] = {}
        for service_id, events in grouped.items():
            segments: list[dict] = []
            cursor = begin
            for index, event in enumerate(events):
                observed = event["observed_at_utc"]
                if observed > finish:
                    break
                if observed > cursor:
                    self._append_segment(
                        segments, cursor, min(observed, finish), UNKNOWN, "observation_gap"
                    )
                next_at = (
                    events[index + 1]["observed_at_utc"]
                    if index + 1 < len(events) else finish
                )
                valid_start = max(begin, observed)
                valid_end = min(finish, next_at, observed + max_gap)
                if valid_end > valid_start:
                    self._append_segment(
                        segments, valid_start, valid_end, event["state"], event["reason_code"]
                    )
                cursor = max(cursor, valid_end)
            if cursor < finish:
                self._append_segment(segments, cursor, finish, UNKNOWN, "observation_gap")
            result[service_id] = segments
        return result

    @staticmethod
    def _append_segment(
        segments: list[dict], start: datetime, end: datetime, state: str, reason: str,
    ) -> None:
        if end <= start:
            return
        if segments and segments[-1]["state"] == state \
                and segments[-1]["reason_code"] == reason \
                and segments[-1]["end"] == start:
            segments[-1]["end"] = end
            segments[-1]["duration_s"] = (end - segments[-1]["start"]).total_seconds()
            return
        segments.append({
            "start": start, "end": end, "duration_s": (end - start).total_seconds(),
            "state": state, "reason_code": reason,
        })

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        value = dict(row)
        value.pop("rank", None)
        value["observed_at_utc"] = datetime.fromisoformat(value["observed_at_utc"])
        if value["last_beat_at_utc"]:
            value["last_beat_at_utc"] = datetime.fromisoformat(value["last_beat_at_utc"])
        value["details"] = json.loads(value["details"])
        value["checkpoint"] = bool(value["checkpoint"])
        return value
