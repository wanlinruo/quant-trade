#!/usr/bin/env python3
"""独立服务健康采样器；只读探测，写本地时刻轴 SQLite。"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import structlog

from qsys.live.heartbeat import HeartbeatMonitor
from qsys.ops.health import (
    AFTERNOON,
    CALENDAR_UNKNOWN,
    DEGRADED,
    DOWN,
    HEALTHY,
    MORNING,
    PREPARE,
    UNKNOWN,
    HealthObservation,
    HealthStore,
    HeartbeatPolicy,
    trading_phase,
)
from qsys.web.service_views import ServiceConsole, windows_task_show

log = structlog.get_logger("health-observer")


class CachedWindowsTask:
    def __init__(self, host: str, key: Path, interval: float = 300.0) -> None:
        self.host, self.key, self.interval = host, key, interval
        self.value: dict = {}
        self.next_refresh = 0.0

    def __call__(self, task: str) -> dict:
        now = time.monotonic()
        if now >= self.next_refresh:
            self.next_refresh = now + self.interval
            self.value = windows_task_show(task, host=self.host, key=self.key)
        return self.value


def _calendar(config_path: str | None, *, retry_interval: float = 300.0):
    from qsys.paper.calendar import load_trading_calendar

    cache: dict[date, object] = {}
    next_retry = 0.0

    def get():
        nonlocal next_retry
        today = date.today()
        if today in cache:
            return cache[today]
        now = time.monotonic()
        if now < next_retry:
            return None
        cache.clear()
        next_retry = now + retry_interval
        try:
            cache[today] = load_trading_calendar(config_path)
        except Exception as exc:
            log.warning("health_observer.calendar_unavailable", error=str(exc))
            return None
        return cache[today]

    return get


def _heartbeat_reader(transport, prefix: str):
    monitor = HeartbeatMonitor(transport, prefix=prefix)

    def read(source: str) -> dict:
        beat = monitor.read(source)
        return {
            "source": source,
            "last_beat": beat.at if beat else None,
            "expected_interval_s": beat.expected_interval_s if beat else None,
            "phase": beat.phase if beat else None,
        }

    return read


def _watchdog_state(task: dict, now: datetime) -> tuple[str, str]:
    if not task:
        return UNKNOWN, "windows_task_unreachable"
    if task.get("State") == "Disabled":
        return DOWN, "watchdog_disabled"
    if task.get("LastTaskResult") != 0 or task.get("State") not in ("Ready", "Running"):
        return DOWN, "watchdog_failed"
    raw = task.get("LastRunTime")
    if not raw:
        return UNKNOWN, "watchdog_last_run_missing"
    age = (now - datetime.fromisoformat(raw)).total_seconds()
    if age > 720:
        return DOWN, "watchdog_stale"
    if age > 420:
        return DEGRADED, "watchdog_delayed"
    return HEALTHY, "watchdog_fresh"


def observation(row: dict, now: datetime, phase: str) -> HealthObservation:
    spec = row["spec"]
    assessment = row.get("heartbeat_assessment")
    last_beat = None
    if assessment is not None:
        state, reason, policy = assessment.state, assessment.reason_code, assessment.policy
        last_beat = row["heartbeat"].get("last_beat")
        source = "redis_heartbeat"
    elif spec.windows_task:
        state, reason = _watchdog_state(row["windows_task"], now)
        policy = HeartbeatPolicy(300, 420, 720)
        source = "windows_task"
    else:
        state = {"ok": HEALTHY, "warn": DEGRADED, "bad": DOWN}.get(
            row["state"], UNKNOWN
        )
        reason = {
            HEALTHY: "process_active", DOWN: "process_inactive",
            DEGRADED: "process_degraded", UNKNOWN: "process_unobservable",
        }[state]
        policy = HeartbeatPolicy(30, 60, 90)
        source = "systemd"
    return HealthObservation(
        service_id=spec.id, observed_at_utc=now, phase=phase,
        expected_interval_s=policy.expected_interval_s,
        warn_after_s=policy.warn_after_s, down_after_s=policy.down_after_s,
        state=state, process_state=state, business_state=UNKNOWN,
        reason_code=reason, source=source, last_beat_at_utc=last_beat,
        details={"host": spec.host},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None)
    parser.add_argument("--tushare-config", default=None)
    parser.add_argument("--service-runs", default="var/service-runs")
    parser.add_argument("--state", default="var/service-health.sqlite3")
    parser.add_argument("--active-interval", type=float, default=5.0)
    parser.add_argument("--idle-interval", type=float, default=30.0)
    args = parser.parse_args()

    from qsys.live.config import load_deploy_config
    from qsys.live.redis_transport import RedisTransport

    deploy = load_deploy_config(args.config)
    try:
        transport = RedisTransport.from_config(deploy)
        heartbeat = _heartbeat_reader(transport, deploy.streams.heartbeat)
    except Exception as exc:
        # Redis 失联不应让 systemd/Windows 观测一起消失。
        log.warning("health_observer.redis_unavailable", error=str(exc))
        heartbeat = None
    calendar = _calendar(args.tushare_config or args.config)
    windows = CachedWindowsTask(
        os.environ.get("QSYS_WINDOWS_SSH_HOST", "Administrator@118.25.42.149"),
        Path(os.environ.get("QSYS_WINDOWS_SSH_KEY", "/home/ubuntu/.ssh/id_ed25519_winnode")),
    )
    console = ServiceConsole(
        Path(args.service_runs), calendar=calendar,
        heartbeat=heartbeat,
        windows_task=windows,
    )
    store = HealthStore(Path(args.state))
    log.info("health_observer.started", state=args.state)
    while True:
        started = time.monotonic()
        now = datetime.now()
        current_calendar = calendar()
        sessions = None if current_calendar is None else set(
            current_calendar.sessions_in_range(now.date(), now.date())
        )
        phase = trading_phase(now, sessions)
        try:
            for row in console.process_status():
                store.record(observation(row, now, phase))
        except Exception as exc:
            # 采样失败不能影响被观测服务；下一轮继续并由缺测区间呈灰色。
            log.error("health_observer.sample_failed", error=str(exc))
        active = phase in (PREPARE, MORNING, AFTERNOON, CALENDAR_UNKNOWN)
        interval = args.active_interval if active else args.idle_interval
        time.sleep(max(0.2, interval - (time.monotonic() - started)))


if __name__ == "__main__":
    sys.exit(main())
