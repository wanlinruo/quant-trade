from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from qsys.ops.alerts import AlertBus, RecordingAlertSink, Severity
from qsys.ops.health import DEGRADED, DOWN, HEALTHY, MORNING
from qsys.ops.trading_chain import ChainAssessment
from scripts.run_health_observer import BusinessAlertTracker, observation


def row(service="shadow-core", state="ok"):
    return {
        "spec": SimpleNamespace(
            id=service, host="Linux", windows_task=None,
        ),
        "state": state,
        "heartbeat_assessment": None,
    }


def test_business_failure_overrides_a_healthy_process():
    item = observation(
        row(), datetime(2026, 9, 21, 9, 30, 15), MORNING,
        ChainAssessment(DOWN, "strategy_event_failed", "竞价行情超时", 2, 1),
    )
    assert item.state == DOWN
    assert item.process_state == HEALTHY
    assert item.business_state == DOWN
    assert item.reason_code == "strategy_event_failed"
    assert item.details["trading_chain"]["completed"] == 1


def test_business_alerts_fire_once_and_report_recovery():
    sink = RecordingAlertSink()
    tracker = BusinessAlertTracker(AlertBus([sink]))
    now = datetime(2026, 9, 21, 9, 30, 15)
    delayed = observation(
        row(), now, MORNING,
        ChainAssessment(DEGRADED, "strategy_event_delayed", "竞价未落库"),
    )
    failed = observation(
        row(), now, MORNING,
        ChainAssessment(DOWN, "strategy_event_failed", "竞价行情超时"),
    )
    recovered = observation(
        row(), now, MORNING,
        ChainAssessment(HEALTHY, "strategy_events_current", "2/2 完成"),
    )
    tracker.update(delayed)
    tracker.update(delayed)
    tracker.update(failed)
    tracker.update(failed)
    tracker.update(recovered)
    assert [alert.severity for alert in sink.alerts] == [
        Severity.WARNING, Severity.CRITICAL, Severity.INFO,
    ]


def _task(age_seconds=30, **over):
    now = datetime(2026, 9, 26, 23, 30)
    base = {"State": "Running", "LastTaskResult": 267009,
            "LastRunTime": (now - timedelta(seconds=age_seconds)).isoformat()}
    return {**base, **over}, now


def test_a_task_caught_mid_run_is_not_a_failure():
    """0x41301 是「正在运行、尚无结果」；看门狗每 5 分钟跑一次，探测可能正好撞上。"""
    from scripts.run_health_observer import _watchdog_state

    task, now = _task()
    assert _watchdog_state(task, now)[1] == "watchdog_fresh"


def test_an_interrupted_run_left_by_a_reboot_is_not_a_failure():
    """机器断电时 0x41301 会连同 State=Ready 冻在那里，直到下次运行才刷新。

    把它当失败，等于每次重启都推一条「看门狗已停止」——2026-10-04 真发过一条。
    真死了由下面的时长判定兜住。
    """
    from scripts.run_health_observer import _watchdog_state

    task, now = _task(State="Ready", LastTaskResult=267009)
    assert _watchdog_state(task, now)[1] == "watchdog_fresh"


def test_a_real_non_zero_result_is_still_a_failure():
    from scripts.run_health_observer import _watchdog_state

    task, now = _task(State="Ready", LastTaskResult=1)
    assert _watchdog_state(task, now) == ("down", "watchdog_failed")


def test_a_healthy_watchdog_seen_through_the_cache_is_not_called_late():
    """任务每 300 秒跑一次，查询又按 300 秒缓存：健康状态下观测到的「距上次运行」
    也能到 600 秒。阈值盖不住这个和，就会每几分钟误报一次。"""
    from scripts.run_health_observer import (
        TASK_CACHE_SECONDS, WATCHDOG_INTERVAL_SECONDS, _watchdog_state,
    )

    worst_case = WATCHDOG_INTERVAL_SECONDS + TASK_CACHE_SECONDS
    task, now = _task(age_seconds=worst_case, State="Ready", LastTaskResult=0)
    assert _watchdog_state(task, now)[1] == "watchdog_fresh"


def test_a_watchdog_that_really_stopped_is_still_caught():
    from scripts.run_health_observer import WATCHDOG_DOWN_AFTER, _watchdog_state

    task, now = _task(age_seconds=WATCHDOG_DOWN_AFTER + 60,
                      State="Ready", LastTaskResult=0)
    assert _watchdog_state(task, now) == ("down", "watchdog_stale")


def test_a_disabled_watchdog_is_down_regardless_of_age():
    from scripts.run_health_observer import _watchdog_state

    task, now = _task(State="Disabled", LastTaskResult=0)
    assert _watchdog_state(task, now) == ("down", "watchdog_disabled")


# ---- 告警要进运维群（2026-10-01 发现一直发在策略群）----

def _deploy(ops="", strategy="https://x/live"):
    from qsys.live.config import AlertConfig
    return SimpleNamespace(alerts=AlertConfig(
        webhook_url=strategy, ops_webhook_url=ops, timeout_seconds=5.0))


def _urls(sinks):
    return [s._url for s in sinks if hasattr(s, "_url")]


def test_chain_alerts_go_to_the_ops_robot_not_the_strategy_one():
    """链路断了是运维事故，不是策略行情——发进实盘群会把实盘群淹掉。"""
    from scripts.run_health_observer import ops_sinks

    assert _urls(ops_sinks(_deploy(ops="https://x/ops"))) == ["https://x/ops"]


def test_a_single_robot_still_gets_everything():
    """只配了一个机器人时不能静默——ops_url 会回落到它。"""
    from scripts.run_health_observer import ops_sinks

    assert _urls(ops_sinks(_deploy(ops="", strategy="https://x/only"))) == ["https://x/only"]


def test_no_robot_configured_still_logs():
    from scripts.run_health_observer import ops_sinks

    sinks = ops_sinks(_deploy(ops="", strategy=""))
    assert _urls(sinks) == [] and len(sinks) == 1


# ---- 任何服务掉线都要报（以前只有 paper / shadow-core 的业务链路有）----

from qsys.ops.health import UNKNOWN  # noqa: E402
from scripts.run_health_observer import ProcessAlertTracker  # noqa: E402


def _process(service, state):
    return observation(row(service, state), datetime(2026, 10, 1, 10, 0), MORNING)


def test_a_windows_service_going_down_is_announced():
    """行情网关与执行代理整个停掉以前一声不响——这两个恰恰是实盘的必经之路。"""
    sink = RecordingAlertSink()
    tracker = ProcessAlertTracker(AlertBus([sink]))
    tracker.update(_process("market-gateway", "ok"))
    tracker.update(_process("market-gateway", "bad"))

    assert len(sink.alerts) == 1
    assert sink.alerts[0].severity is Severity.CRITICAL
    assert "QMT 行情网关已停止" in sink.alerts[0].title


def test_a_long_outage_is_still_one_alert():
    sink = RecordingAlertSink()
    tracker = ProcessAlertTracker(AlertBus([sink]))
    tracker.update(_process("market-gateway", "ok"))
    for _ in range(2016):
        tracker.update(_process("market-gateway", "bad"))

    assert len(sink.alerts) == 1


def test_recovery_is_announced_as_well():
    sink = RecordingAlertSink()
    tracker = ProcessAlertTracker(AlertBus([sink]))
    tracker.update(_process("market-gateway", "ok"))
    tracker.update(_process("market-gateway", "bad"))
    tracker.update(_process("market-gateway", "ok"))

    assert [a.severity for a in sink.alerts] == [Severity.CRITICAL, Severity.INFO]


def test_a_degraded_process_does_not_emit_stopped_or_recovery_alerts():
    sink = RecordingAlertSink()
    tracker = ProcessAlertTracker(AlertBus([sink]))
    tracker.update(_process("market-gateway", "ok"))
    tracker.update(_process("market-gateway", "warn"))
    tracker.update(_process("market-gateway", "ok"))

    assert sink.alerts == []


def test_the_very_first_sample_does_not_alert():
    """采样器刚起来时不该把「本来就停着」当成刚刚发生的事故补报一遍。"""
    sink = RecordingAlertSink()
    ProcessAlertTracker(AlertBus([sink])).update(_process("market-gateway", "bad"))

    assert sink.alerts == []


def test_an_unobservable_service_is_not_called_down():
    """连不上 Windows 时刷一串「离线」，比不报更糟。"""
    sink = RecordingAlertSink()
    tracker = ProcessAlertTracker(AlertBus([sink]))
    tracker.update(_process("market-gateway", "ok"))
    unknown = _process("market-gateway", "unobservable")
    assert unknown.process_state == UNKNOWN
    tracker.update(unknown)

    assert sink.alerts == []


# ---- 基础设施巡检：按间隔跑，边沿触发 ----

from qsys.ops.infra_watch import Finding  # noqa: E402
from scripts.run_health_observer import InfraAlerts  # noqa: E402


def _infra(sink, **kw):
    return InfraAlerts(AlertBus([sink]), storage_base=Path("/nonexistent"),
                       config_path=None, tls_site=None, tls_port=8443, **kw)


def test_the_survey_runs_on_its_own_slower_clock():
    """磁盘和证书都不是秒级变化的东西，不该跟着 5 秒的采样一起跑。"""
    watch = _infra(RecordingAlertSink(), interval=300.0)

    assert watch.due(0.0) is True
    watch.run(0.0)
    assert watch.due(299.0) is False
    assert watch.due(300.0) is True


def test_a_finding_is_pushed_once_and_its_recovery_once(monkeypatch):
    sink = RecordingAlertSink()
    watch = _infra(sink)
    bad = [Finding("disk", Severity.CRITICAL, "磁盘即将写满", "剩余 6.0 GiB")]
    findings = bad

    monkeypatch.setattr("scripts.run_health_observer.survey", lambda reading: findings)
    watch.run(0.0)
    watch.run(300.0)
    assert [a.title for a in sink.alerts] == ["磁盘即将写满"]

    findings = []
    watch.run(600.0)
    assert [a.title for a in sink.alerts] == ["磁盘即将写满", "磁盘即将写满：已恢复"]


def test_a_broken_survey_never_stops_the_sampling_loop(monkeypatch):
    sink = RecordingAlertSink()
    watch = _infra(sink)

    def explode(reading):
        raise RuntimeError("boom")

    monkeypatch.setattr("scripts.run_health_observer.survey", explode)
    watch.run(0.0)          # 不抛

    assert sink.alerts == []
    assert watch.due(0.0) is False   # 仍然推进了下次时间，不会原地空转


# ---- 查询失败要快点重试，别把盲区拖满一个缓存周期（2026-10-04）----

def _cached(answers):
    """answers 依次作为 windows_task_show 的返回值。"""
    from scripts.run_health_observer import CachedWindowsTask

    calls = []
    cache = CachedWindowsTask("h", Path("k"), interval=300.0, retry=120.0)
    seq = list(answers)

    def fake(task, *, host, key):
        calls.append(task)
        return seq.pop(0) if seq else {}

    import scripts.run_health_observer as mod
    return cache, calls, fake, mod


GOOD = {"State": "Ready", "LastTaskResult": 0, "LastRunTime": "2026-10-04T11:00:00"}


def test_a_good_answer_is_held_for_the_whole_interval(monkeypatch):
    cache, calls, fake, mod = _cached([GOOD, GOOD])
    monkeypatch.setattr(mod, "windows_task_show", fake)
    monkeypatch.setattr(mod.time, "monotonic", lambda: 0.0)
    assert cache("t") == GOOD

    monkeypatch.setattr(mod.time, "monotonic", lambda: 299.0)
    assert cache("t") == GOOD
    assert len(calls) == 1          # 没有重复去问


def test_a_failed_answer_is_retried_well_before_the_full_interval(monkeypatch):
    """偏偏重启前后最容易偶发失败，而那正是最需要看清楚的时候。

    但也不能太急：查询是同步的，会堵住采样主循环，所以折中到两分钟。
    """
    cache, calls, fake, mod = _cached([{}, GOOD])
    monkeypatch.setattr(mod, "windows_task_show", fake)
    monkeypatch.setattr(mod.time, "monotonic", lambda: 0.0)
    assert cache("t") == {}

    monkeypatch.setattr(mod.time, "monotonic", lambda: 119.0)
    assert cache("t") == {}
    assert len(calls) == 1          # 还没到重试点

    monkeypatch.setattr(mod.time, "monotonic", lambda: 120.0)
    assert cache("t") == GOOD       # 远早于整个缓存周期
    assert len(calls) == 2
    from scripts.run_health_observer import TASK_CACHE_SECONDS, TASK_RETRY_SECONDS
    assert TASK_RETRY_SECONDS < TASK_CACHE_SECONDS
