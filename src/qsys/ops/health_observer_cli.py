"""CLI for the independently released service health observer."""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
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
from qsys.ops.alerts import AlertBus, LogAlertSink, Severity, WebhookAlertSink, ledger
from qsys.ops.infra_watch import EdgeTracker, survey
from qsys.ops.services import CORE_SERVICE_IDS, SERVICE_BY_ID, active_core_service
from qsys.ops.trading_chain import ChainAssessment, assess_paper, assess_shadow
from qsys.web.service_views import ServiceConsole, windows_task_show

log = structlog.get_logger("health-observer")


# Windows 计划任务要走 SSH 查，贵且不稳，所以按这个间隔缓存。
TASK_CACHE_SECONDS = 300.0


# 查询失败后多久再试。按整个缓存周期等下去，一次偶发的 SSH 失败就要换来
# 5 分钟的「问不出来」——而偏偏重启前后最容易偶发，那正是最需要看清楚的时候。
#
# 但也不能太急：这次查询是**同步**的，会把采样主循环堵住最多一个超时
# （20 秒）。Windows 真的长时间失联时，60 秒重试等于三分之一时间在堵，
# 会拖慢真正要紧的心跳判定。看门狗自身的状态没那么急——它守的进程一死，
# 心跳告警一分钟内就会叫。
TASK_RETRY_SECONDS = 120.0


class CachedWindowsTask:
    def __init__(self, host: str, key: Path,
                 interval: float = TASK_CACHE_SECONDS,
                 retry: float = TASK_RETRY_SECONDS) -> None:
        self.host, self.key, self.interval = host, key, interval
        self.retry = retry
        self.value: dict = {}
        self.next_refresh = 0.0

    def __call__(self, task: str) -> dict:
        now = time.monotonic()
        if now >= self.next_refresh:
            self.value = windows_task_show(task, host=self.host, key=self.key)
            # 问到了就按正常周期歇着；没问到就快点再试，别把盲区拖满一个周期
            self.next_refresh = now + (self.interval if self.value else self.retry)
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
            "activity": beat.activity if beat else None,
            "event_key": beat.event_key if beat else None,
            "event_phase": beat.event_phase if beat else None,
            "event_started_at": (
                beat.event_started_at.isoformat()
                if beat and beat.event_started_at else None
            ),
            "event_timeout_s": beat.event_timeout_s if beat else None,
            "event_timed_out": beat.event_timed_out if beat else False,
        }

    return read


# 0x41301：任务此刻正在运行，还没有本次结果——不是失败。
# 0x41301：快照拍下时任务正在运行，还没有本次结果——不是失败。
_TASK_STILL_RUNNING = 267009

# 看门狗每 5 分钟跑一次，而上面的查询又按 5 分钟缓存。两者叠加，**完全健康**
# 的看门狗观测到的「距上次运行」也能到 600 秒。阈值必须盖过这个和，否则每隔
# 几分钟就误报一次——2026-10-04 实测到它在 healthy / degraded 之间反复横跳，
# 并在一次重启后推出一条「Windows 进程看门狗已停止」的假 CRITICAL。
WATCHDOG_INTERVAL_SECONDS = 300.0
_WORST_CASE = WATCHDOG_INTERVAL_SECONDS + TASK_CACHE_SECONDS
WATCHDOG_DELAYED_AFTER = _WORST_CASE + 300.0
WATCHDOG_DOWN_AFTER = _WORST_CASE + 900.0


def _watchdog_state(task: dict, now: datetime) -> tuple[str, str]:
    if not task:
        return UNKNOWN, "windows_task_unreachable"
    if task.get("State") == "Disabled":
        return DOWN, "watchdog_disabled"
    if task.get("State") not in ("Ready", "Running"):
        return DOWN, "watchdog_failed"
    result = task.get("LastTaskResult")
    # 非零即失败，只有 0x41301 例外：它表示「拍快照时在跑」。机器被断电时
    # 这个值会连同 State=Ready 一起冻在那里，直到下一次运行才刷新——把它当成
    # 失败，等于每次重启都误报一次看门狗死亡。真死了由下面的时长判定兜住。
    if result not in (0, _TASK_STILL_RUNNING):
        return DOWN, "watchdog_failed"
    raw = task.get("LastRunTime")
    if not raw:
        return UNKNOWN, "watchdog_last_run_missing"
    age = (now - datetime.fromisoformat(raw)).total_seconds()
    if age > WATCHDOG_DOWN_AFTER:
        return DOWN, "watchdog_stale"
    if age > WATCHDOG_DELAYED_AFTER:
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
    schedule = row.get("systemd") or {}
    execution = row.get("last_exit") or {}
    if getattr(spec, "kind", None) == "timer" and schedule:
        if schedule.get("ActiveState") in {"inactive", "failed", "deactivating"}:
            reason = "schedule_inactive"
        elif execution.get("ActiveState") in {"active", "activating", "reloading"}:
            reason = "scheduled_task_running"
        elif execution.get("Result") not in {None, "", "success"}:
            reason = "scheduled_task_failed"
        elif schedule.get("ActiveState") == "active":
            reason = "schedule_ready"
    business_state = business.state if business is not None else UNKNOWN
    if business is not None:
        if business.state == DOWN or (
            business.state == DEGRADED and state != DOWN
        ):
            state, reason = business.state, business.reason_code
    details = {"host": spec.host}
    if getattr(spec, "kind", None) == "timer" and schedule:
        details["systemd_schedule"] = {
            key: schedule[key] for key in ("ActiveState", "SubState") if key in schedule
        }
    if row.get("last_exit"):
        details["systemd_execution"] = {
            key: value for key, value in row["last_exit"].items()
            if key in {"ActiveState", "SubState", "Result", "ExecMainStatus",
                       "ExecMainStartTimestamp", "ExecMainExitTimestamp"}
        }
    heartbeat = row.get("heartbeat") or {}
    if heartbeat.get("activity") is not None:
        details["heartbeat_activity"] = {
            name: heartbeat.get(name) for name in (
                "activity", "event_key", "event_phase", "event_started_at",
                "event_timeout_s", "event_timed_out",
            )
        }
    if business is not None:
        details["trading_chain"] = {
            "reason_code": business.reason_code,
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
        self.states: dict[str, tuple[str, bool]] = {}

    def update(self, item: HealthObservation) -> None:
        if item.service_id not in ("paper", *CORE_SERVICE_IDS):
            return
        previous, previous_unreadable = self.states.get(item.service_id, (None, False))
        current = item.business_state
        unreadable = item.reason_code == "strategy_state_unavailable"
        self.states[item.service_id] = (current, unreadable)
        detail = (item.details or {}).get("trading_chain", {}).get("detail", "")
        name = {
            "paper": "模拟交易", "shadow-core": "影子盘", "live-core": "实盘",
        }[item.service_id]
        if current == DOWN and (previous != DOWN or unreadable != previous_unreadable):
            title = f"{name}状态库读取失败" if unreadable else f"{name}交易链路失败"
            self.alerts.critical(title, detail)
        elif current == DEGRADED and previous not in (DEGRADED, DOWN):
            self.alerts.warning(f"{name}策略事件延迟", detail)
        elif (current == HEALTHY and previous in (DEGRADED, DOWN)
              and (item.details or {}).get("trading_chain", {}).get("reason_code")
              != "strategy_events_not_due"):
            title = f"{name}状态库读取已恢复" if previous_unreadable else f"{name}交易链路已恢复"
            self.alerts.info(title, detail)


class ProcessAlertTracker:
    """任何一个服务进程掉线都报一次，恢复再报一次。

    以前只有 paper / shadow-core 的**业务链路**有告警：Windows 上的行情网关
    或执行代理整个停掉，一声不响——这两个恰恰是实盘的必经之路。

    同样边沿触发：国庆七天 Windows 不开机应该是一条告警，不是两千条。
    UNKNOWN（问不出来）不报——采样器自己连不上 Windows 时刷一串「离线」，
    比不报更糟。
    """

    def __init__(self, alerts: AlertBus) -> None:
        self.alerts = alerts
        self.states: dict[str, str] = {}
        self.schedule_states: dict[str, str] = {}

    def update(self, item: HealthObservation) -> None:
        previous = self.states.get(item.service_id)
        current = item.process_state
        if current == UNKNOWN:
            return
        spec = SERVICE_BY_ID.get(item.service_id)
        if spec is not None and spec.kind == "timer" and item.reason_code in {
            "schedule_inactive", "scheduled_task_running", "scheduled_task_failed", "schedule_ready",
        }:
            self._schedule_update(item, spec.name)
            return
        self.states[item.service_id] = current
        name = SERVICE_BY_ID[item.service_id].name if (
            item.service_id in SERVICE_BY_ID) else item.service_id
        if current == DOWN and previous not in (None, DOWN):
            self.alerts.critical(f"{name}已停止", item.reason_code)
        elif current == HEALTHY and previous == DOWN:
            self.alerts.info(f"{name}已恢复", item.reason_code)

    def _schedule_update(self, item: HealthObservation, name: str) -> None:
        current = item.reason_code
        previous = self.schedule_states.get(item.service_id)
        self.schedule_states[item.service_id] = current
        self.states[item.service_id] = item.process_state
        if previous is None or previous == current:
            return
        execution = (item.details or {}).get("systemd_execution", {})
        detail = "; ".join(
            f"{key}={execution[key]}" for key in (
                "Result", "ExecMainStatus", "ExecMainStartTimestamp", "ExecMainExitTimestamp",
            ) if execution.get(key) not in (None, "")
        )
        if current == "schedule_inactive":
            self.alerts.critical(f"{name}调度未启用", detail)
        elif current == "scheduled_task_failed":
            self.alerts.critical(f"{name}最近执行失败", detail)
        elif previous in {"schedule_inactive", "scheduled_task_failed"}:
            if current == "scheduled_task_running":
                title = f"{name}调度可用，任务正在执行（结果待定）"
            elif (execution.get("Result") == "success"
                  and execution.get("ExecMainStatus") in (0, "0")
                  and execution.get("ExecMainExitTimestamp")):
                title = f"{name}调度可用，上次进程正常退出"
            else:
                title = f"{name}调度可用（尚无成功执行证据）"
            self.alerts.info(title, detail)


class InfraAlerts:
    """周期性巡检基础设施，边沿触发地推给运维机器人。"""

    def __init__(self, alerts: AlertBus, *, storage_base: Path, config_path,
                 tls_site: str | None, tls_port: int, interval: float = 300.0) -> None:
        self.alerts, self.tracker = alerts, EdgeTracker()
        self.storage_base, self.config_path = storage_base, config_path
        self.tls_site, self.tls_port = tls_site, tls_port
        self.interval, self.next_at = interval, 0.0

    def due(self, monotonic: float) -> bool:
        return monotonic >= self.next_at

    def run(self, monotonic: float, transport=None) -> None:
        self.next_at = monotonic + self.interval
        try:
            findings = survey(self.read(transport))
        except Exception as exc:          # 巡检自己出错不能拖垮采样循环
            log.error("infra_watch.survey_failed", error=str(exc))
            return
        for notice in self.tracker.update(findings):
            {
                Severity.CRITICAL: self.alerts.critical,
                Severity.WARNING: self.alerts.warning,
                Severity.INFO: self.alerts.info,
            }[notice.severity](notice.title, notice.detail)

    def read(self, transport) -> dict:
        from qsys.ops.infra import failed_units
        from qsys.ops.storage import disk_status

        reading: dict = {"failed_units": failed_units()}
        try:
            reading["disk_free_bytes"] = disk_status(self.storage_base).free
        except OSError as exc:
            log.info("infra_watch.disk_unavailable", error=str(exc))
        try:
            reading["system_disk_free_bytes"] = disk_status(Path("/")).free
        except OSError as exc:
            log.info("infra_watch.system_disk_unavailable", error=str(exc))
        reading["cert_days"] = _cert_days(self.tls_site, self.tls_port)
        reading.update(_redis_state(transport, self.config_path))
        return reading


def _cert_days(site: str | None, port: int) -> int | None:
    if not site:
        return None
    try:
        from qsys.ops.service_checks import serving_not_after

        not_after = serving_not_after("127.0.0.1", port, site)
        return (not_after - datetime.now(timezone.utc).replace(tzinfo=None)).days
    except Exception as exc:
        log.info("infra_watch.cert_unavailable", error=str(exc))
        return None


def _redis_state(transport, config_path) -> dict:
    """Redis 通不通，以及急停开关在不在。两件事共用一个连接。"""
    if transport is None:
        return {}
    try:
        from qsys.live.kill_switch import KillSwitch

        return {"kill_switch": KillSwitch(transport).reason()}
    except Exception as exc:
        return {"redis_error": str(exc)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None)
    parser.add_argument("--tushare-config", default=None)
    parser.add_argument("--service-runs", default="var/service-runs")
    parser.add_argument("--state", default="var/service-health.sqlite3")
    parser.add_argument("--alert-ledger", default=None,
                        help="组件自有告警投递流水目录")
    parser.add_argument("--shadow-state", default="var/shadow-state.sqlite3")
    parser.add_argument("--live-state", default="var/live-state.sqlite3")
    parser.add_argument("--workbench", default="var/workbench/workbench.sqlite3")
    parser.add_argument("--active-interval", type=float, default=5.0)
    parser.add_argument("--idle-interval", type=float, default=30.0)
    parser.add_argument("--infra-interval", type=float, default=300.0,
                        help="基础设施巡检间隔；磁盘与证书都不是秒级变化的东西")
    parser.add_argument("--storage-base", default="/data/qsys")
    parser.add_argument("--tls-site", default=None,
                        help="公网入口域名（作 SNI 探 127.0.0.1）；不给则不看证书")
    parser.add_argument("--tls-port", type=int, default=8443)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    from qsys.live.config import load_deploy_config
    from qsys.live.readonly_transport import readonly_from_config

    deploy = load_deploy_config(args.config)
    bus = AlertBus(ops_sinks(deploy), ledger(args.alert_ledger))
    alert_tracker = BusinessAlertTracker(bus)
    process_tracker = ProcessAlertTracker(bus)
    infra_alerts = InfraAlerts(
        bus, storage_base=Path(args.storage_base), config_path=args.config,
        tls_site=args.tls_site, tls_port=args.tls_port,
        interval=args.infra_interval,
    )
    transport = None
    try:
        transport = readonly_from_config(deploy)
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
    store.pin_reader_sidecars()
    import atexit
    atexit.register(store.close)
    log.info("health_observer.started", state=args.state)
    active_core = active_core_service(deploy.safety.mode)
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
                service_id = row["spec"].id
                if service_id in CORE_SERVICE_IDS and service_id != active_core:
                    # Overwrite any observation left by the previous mode.  An
                    # intentionally stopped core is N/A, never DOWN.
                    store.record(HealthObservation(
                        service_id=service_id, observed_at_utc=now, phase=phase,
                        expected_interval_s=30, warn_after_s=60, down_after_s=90,
                        state=UNKNOWN, process_state=UNKNOWN, business_state=UNKNOWN,
                        reason_code="inactive_by_mode", source="deploy_config",
                        details={"mode": deploy.safety.mode},
                    ))
                    continue
                business = None
                if sessions is not None and now.date() in sessions:
                    if service_id == "paper":
                        business = assess_paper(Path(args.workbench), now)
                    elif service_id in CORE_SERVICE_IDS:
                        state_path = (
                            Path(args.live_state) if service_id == "live-core"
                            else Path(args.shadow_state)
                        )
                        if service_id == "live-core":
                            from qsys.ops.trading_chain import recorded_auction_time

                            auction_at = recorded_auction_time(state_path)
                        else:
                            auction_at = shadow_auction_at(
                                args.config, Path(args.workbench)
                            )
                        business = assess_shadow(
                            state_path, now, auction_at=auction_at,
                        )
                item = observation(row, now, phase, business)
                alert_tracker.update(item)
                process_tracker.update(item)
                store.record(item)
        except Exception as exc:
            # 采样失败不能影响被观测服务；下一轮继续并由缺测区间呈灰色。
            log.error("health_observer.sample_failed", error=str(exc))
        if infra_alerts.due(started):
            infra_alerts.run(started, transport)
        active = phase in (PREPARE, MORNING, AFTERNOON, CALENDAR_UNKNOWN)
        interval = args.active_interval if active else args.idle_interval
        time.sleep(max(0.2, interval - (time.monotonic() - started)))


if __name__ == "__main__":
    sys.exit(main())
