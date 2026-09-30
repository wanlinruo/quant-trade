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
from qsys.ops.alerts import AlertBus, LogAlertSink, WebhookAlertSink, ledger
from qsys.ops.trading_chain import ChainAssessment, assess_paper, assess_shadow
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


# 0x41301：任务此刻正在运行，还没有本次结果——不是失败。
_TASK_STILL_RUNNING = 267009


def _watchdog_state(task: dict, now: datetime) -> tuple[str, str]:
    if not task:
        return UNKNOWN, "windows_task_unreachable"
    if task.get("State") == "Disabled":
        return DOWN, "watchdog_disabled"
    result = task.get("LastTaskResult")
    running_now = result == _TASK_STILL_RUNNING and task.get("State") == "Running"
    if (result != 0 and not running_now) or task.get("State") not in ("Ready", "Running"):
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


def shadow_auction_at(config_path, workbench: Path):
    """影子核心的竞价时刻＝它承袭的那个模拟账户的 auction_at。

    影子核心不自己填参数，而是读 ``[strategy] follows_paper_account`` 指向的账户，
    所以监控的预期时刻也必须问同一个地方；写死 09:30 会把 09:25:10 的账户
    误报成事件失败（2026-09-28 实际发生过）。任何一步取不到就退回默认值，
    监控解析不了参数不该让整条链判死。
    """
    import sqlite3

    from qsys.ops.trading_chain import DEFAULT_AUCTION, account_auction_time

    try:
        from qsys.live.config import load_deploy_config

        ident = load_deploy_config(config_path).strategy.follows_paper_account
        if not ident:
            return DEFAULT_AUCTION
        with sqlite3.connect(f"file:{workbench}?mode=ro", uri=True) as con:
            row = con.execute(
                "SELECT config_json FROM paper_accounts WHERE id=?", (ident,)
            ).fetchone()
        return account_auction_time(row[0]) if row else DEFAULT_AUCTION
    except Exception as exc:  # 监控自己出错不该把被观测方判死
        log.warning("health_observer.shadow_auction_unresolved", error=str(exc))
        return DEFAULT_AUCTION


def observation(
    row: dict, now: datetime, phase: str,
    business: ChainAssessment | None = None,
) -> HealthObservation:
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
    process_state = state
    business_state = business.state if business is not None else UNKNOWN
    if business is not None:
        if business.state == DOWN or (
            business.state == DEGRADED and state != DOWN
        ):
            state, reason = business.state, business.reason_code
    details = {"host": spec.host}
    if business is not None:
        details["trading_chain"] = {
            "detail": business.detail,
            "expected": business.expected,
            "completed": business.completed,
        }
    return HealthObservation(
        service_id=spec.id, observed_at_utc=now, phase=phase,
        expected_interval_s=policy.expected_interval_s,
        warn_after_s=policy.warn_after_s, down_after_s=policy.down_after_s,
        state=state, process_state=process_state, business_state=business_state,
        reason_code=reason, source=source, last_beat_at_utc=last_beat,
        details=details,
    )


def ops_sinks(deploy) -> list:
    """本进程的告警去运维群，不去策略群。

    链路断了是「系统管理者今天要处理的事」，不是某条策略的行情。发进实盘群会
    两头吃亏：实盘群被运维噪音淹，运维群反而漏掉事故——2026-09-29 配好三个
    机器人时这里没跟着改，一直发错群（2026-10-01 发现）。
    """
    sinks = [LogAlertSink()]
    if deploy.alerts.ops_url:
        sinks.append(WebhookAlertSink(
            deploy.alerts.ops_url, timeout=deploy.alerts.timeout_seconds,
        ))
    return sinks


class BusinessAlertTracker:
    """Emit once per chain failure/degradation and once after recovery."""

    def __init__(self, alerts: AlertBus) -> None:
        self.alerts = alerts
        self.states: dict[str, str] = {}

    def update(self, item: HealthObservation) -> None:
        if item.service_id not in ("paper", "shadow-core"):
            return
        previous = self.states.get(item.service_id)
        current = item.business_state
        self.states[item.service_id] = current
        detail = (item.details or {}).get("trading_chain", {}).get("detail", "")
        name = "模拟交易" if item.service_id == "paper" else "影子盘"
        if current == DOWN and previous != DOWN:
            self.alerts.critical(f"{name}交易链路失败", detail)
        elif current == DEGRADED and previous not in (DEGRADED, DOWN):
            self.alerts.warning(f"{name}策略事件延迟", detail)
        elif current == HEALTHY and previous in (DEGRADED, DOWN):
            self.alerts.info(f"{name}交易链路已恢复", detail)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None)
    parser.add_argument("--tushare-config", default=None)
    parser.add_argument("--service-runs", default="var/service-runs")
    parser.add_argument("--state", default="var/service-health.sqlite3")
    parser.add_argument("--shadow-state", default="var/shadow-state.sqlite3")
    parser.add_argument("--workbench", default="var/workbench/workbench.sqlite3")
    parser.add_argument("--active-interval", type=float, default=5.0)
    parser.add_argument("--idle-interval", type=float, default=30.0)
    args = parser.parse_args()

    from qsys.live.config import load_deploy_config
    from qsys.live.redis_transport import RedisTransport

    deploy = load_deploy_config(args.config)
    alert_tracker = BusinessAlertTracker(AlertBus(ops_sinks(deploy), ledger()))
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
                business = None
                if sessions is not None and now.date() in sessions:
                    if row["spec"].id == "paper":
                        business = assess_paper(Path(args.workbench), now)
                    elif row["spec"].id == "shadow-core":
                        business = assess_shadow(
                            Path(args.shadow_state), now,
                            auction_at=shadow_auction_at(
                                args.config, Path(args.workbench)
                            ),
                        )
                item = observation(row, now, phase, business)
                alert_tracker.update(item)
                store.record(item)
        except Exception as exc:
            # 采样失败不能影响被观测服务；下一轮继续并由缺测区间呈灰色。
            log.error("health_observer.sample_failed", error=str(exc))
        active = phase in (PREPARE, MORNING, AFTERNOON, CALENDAR_UNKNOWN)
        interval = args.active_interval if active else args.idle_interval
        time.sleep(max(0.2, interval - (time.monotonic() - started)))


if __name__ == "__main__":
    sys.exit(main())
