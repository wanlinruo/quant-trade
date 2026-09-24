"""服务管理页：看板状态判定、明细页、在没有任何记录时也能打开。"""

from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

from qsys.core.calendar import AShareCalendar
from qsys.ops import services
from qsys.ops.health import DOWN, HEALTHY, MORNING, HealthObservation, HealthStore, SHANGHAI
from qsys.ops.services import ServiceRun
from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.config import WebConfig
from qsys.web.service_views import ServiceConsole, windows_task_show
from qsys.web.views import MonitorService

NOW = datetime(2026, 9, 17, 20, 0)  # 周四
SESSIONS = [date(2026, 9, d) for d in (10, 11, 14, 15, 16, 17)]


def record(root, service, started, status_fn=None, monkeypatch=None):
    monkeypatch.setattr(services, "_now", lambda: started)
    with ServiceRun.open(root, service) as run:
        if service != "service-checks":
            run.session = started.date()
        if status_fn:
            status_fn(run)


def console(root, *, calendar=True):
    return ServiceConsole(
        root, calendar=(lambda: AShareCalendar(SESSIONS)) if calendar else None,
        systemd=lambda unit: {"ActiveState": "active", "SubState": "waiting"},
        now=lambda: NOW,
    )


def states(board, service_id):
    _, rows, _ = board
    for spec, cells in rows:
        if spec.id == service_id:
            return {cell.day: cell.state for cell in cells}
    raise AssertionError(service_id)


def fail_step(run):
    with run.step("日线") as step:
        step.fail("0 行")


def test_board_states(tmp_path, monkeypatch):
    record(tmp_path, "tushare-sync", datetime(2026, 9, 14, 17, 0), monkeypatch=monkeypatch)
    record(tmp_path, "tushare-sync", datetime(2026, 9, 15, 17, 0), fail_step, monkeypatch)
    record(tmp_path, "tushare-sync", datetime(2026, 9, 15, 19, 0), monkeypatch=monkeypatch)
    record(tmp_path, "tushare-sync", datetime(2026, 9, 16, 17, 0), fail_step, monkeypatch)
    record(tmp_path, "premarket-limits", datetime(2026, 9, 17, 8, 58), monkeypatch=monkeypatch)
    got = states(console(tmp_path).board(14), "tushare-sync")
    assert got[date(2026, 9, 14)] == "ok"
    assert got[date(2026, 9, 15)] == "fixed"     # 17:00 失败、19:00 补跑成功
    assert got[date(2026, 9, 16)] == "bad"
    assert got[date(2026, 9, 17)] == "missed"    # 截止 19:30 已过仍无记录
    assert got[date(2026, 9, 13)] == "none"      # 运行记录启用前


def test_missed_and_closed(tmp_path, monkeypatch):
    record(tmp_path, "tushare-sync", datetime(2026, 9, 11, 17, 0), monkeypatch=monkeypatch)
    got = states(console(tmp_path).board(14), "daily-archive")
    assert got[date(2026, 9, 12)] == "closed"    # 周六
    assert got[date(2026, 9, 14)] == "missed"    # 交易日无记录
    assert got[date(2026, 9, 17)] == "missed"    # 截止 17:00 已过


def test_pending_before_due(tmp_path, monkeypatch):
    record(tmp_path, "tushare-sync", datetime(2026, 9, 11, 17, 0), monkeypatch=monkeypatch)
    early = ServiceConsole(tmp_path, calendar=lambda: AShareCalendar(SESSIONS),
                           systemd=lambda unit: {}, now=lambda: datetime(2026, 9, 17, 16, 0))
    assert states(early.board(14), "tushare-sync")[date(2026, 9, 17)] == "pending"


def test_without_calendar_nothing_is_called_missed(tmp_path, monkeypatch):
    record(tmp_path, "tushare-sync", datetime(2026, 9, 11, 17, 0), monkeypatch=monkeypatch)
    board = console(tmp_path, calendar=False).board(14)
    assert board[2] is False
    assert set(states(board, "daily-archive").values()) == {"none"}


def test_skipped_only_day_shows_closed(tmp_path, monkeypatch):
    record(tmp_path, "premarket-limits", datetime(2026, 9, 12, 8, 58),
           lambda run: run.skip("非交易日"), monkeypatch)
    assert states(console(tmp_path).board(14), "premarket-limits")[date(2026, 9, 12)] == "closed"


def client(root, warehouse, **kwargs):
    config = WebConfig()
    return TestClient(create_app(
        MonitorService(SessionJournal(warehouse), config=config), config,
        services=console(root, **kwargs),
    ))


def test_pages_open_without_any_record(tmp_path, warehouse):
    http = client(tmp_path / "runs", warehouse)
    page = http.get("/services")
    assert page.status_code == 200
    assert "Tushare 收盘同步" in page.text and "suspend_daily" in page.text
    assert "服务管理" in page.text
    assert "每日业务验收" in page.text and "在线时刻轴" in page.text
    assert "Windows 进程看门狗" in page.text
    assert http.get("/services/tushare-sync").status_code == 200
    assert http.get("/services/nope").status_code == 404


def test_watchdog_is_in_process_status_but_not_daily_board(tmp_path, warehouse):
    live = ServiceConsole(
        tmp_path, systemd=lambda unit: {},
        windows_task=lambda task: {"State": "Ready", "LastTaskResult": 0,
                                   "LastRunTime": "2026-09-19T14:17:17"},
        now=lambda: NOW,
    )
    assert "watchdog" not in [spec.id for spec, _ in live.board()[1]]
    assert [spec.id for spec, _ in live.board()[1]] == [
        "premarket-limits", "daily-archive", "tushare-sync", "catalog-verify",
        "service-checks",
    ]
    row = next(r for r in live.process_status() if r["spec"].id == "watchdog")
    assert row["state"] == "ok"
    page = TestClient(create_app(
        MonitorService(SessionJournal(warehouse), config=WebConfig()), WebConfig(),
        services=live,
    )).get("/services")
    assert "Windows 进程看门狗" in page.text
    assert "计划任务 Ready" in page.text


def test_watchdog_disabled_and_unreachable_are_distinct(tmp_path):
    disabled = ServiceConsole(tmp_path, windows_task=lambda task: {
        "State": "Disabled", "LastTaskResult": 0}, now=lambda: NOW)
    unavailable = ServiceConsole(tmp_path, windows_task=lambda task: {}, now=lambda: NOW)
    assert next(r for r in disabled.process_status() if r["spec"].id == "watchdog")["state"] == "bad"
    assert next(r for r in unavailable.process_status() if r["spec"].id == "watchdog")["state"] == "unknown"


def test_heartbeat_process_status_uses_phase_specific_warn_and_down_thresholds(tmp_path):
    last = datetime(2026, 9, 17, 10, 0)
    live = ServiceConsole(
        tmp_path, calendar=lambda: AShareCalendar(SESSIONS), systemd=lambda unit: {},
        heartbeat=lambda source: {"last_beat": last, "alive": True,
                                  "expected_interval_s": 1, "phase": "morning"},
        now=lambda: last + timedelta(seconds=4),
    )
    market = next(r for r in live.process_status() if r["spec"].id == "market-gateway")
    assert market["state"] == "warn"
    assert market["heartbeat_assessment"].reason_code == "heartbeat_delayed"


def test_unreachable_heartbeat_is_unknown_even_when_systemd_process_is_active(tmp_path):
    live = ServiceConsole(
        tmp_path, calendar=lambda: AShareCalendar(SESSIONS),
        systemd=lambda unit: {"ActiveState": "active"},
        heartbeat=lambda source: (_ for _ in ()).throw(ConnectionError("redis down")),
        now=lambda: NOW,
    )
    shadow = next(r for r in live.process_status() if r["spec"].id == "shadow-core")
    assert shadow["state"] == "unknown"


def test_persisted_health_avoids_live_remote_probes_and_feeds_timeline(tmp_path):
    store = HealthStore(tmp_path / "health.sqlite3")
    observed = (NOW - timedelta(minutes=1)).replace(tzinfo=SHANGHAI)
    store.record(HealthObservation(
        service_id="market-gateway", observed_at_utc=observed, phase=MORNING,
        expected_interval_s=1, warn_after_s=3, down_after_s=10,
        state=HEALTHY, process_state=HEALTHY, business_state="unknown",
        reason_code="heartbeat_fresh", source="redis_heartbeat",
    ))
    live = ServiceConsole(
        tmp_path, health_store=store, now=lambda: NOW,
        systemd=lambda unit: (_ for _ in ()).throw(AssertionError("must not probe")),
        windows_task=lambda task: (_ for _ in ()).throw(AssertionError("must not ssh")),
    )
    row = next(r for r in live.process_status() if r["spec"].id == "market-gateway")
    assert row["state"] == "ok"
    assert live.health_timeline()[0]["segments"][-1]["state"] == "healthy"


def test_health_timeline_excludes_timers_but_keeps_watchdog(tmp_path):
    store = HealthStore(tmp_path / "health.sqlite3")
    observed = (NOW - timedelta(minutes=1)).replace(tzinfo=SHANGHAI)
    for service_id in ("market-gateway", "tushare-sync", "watchdog"):
        store.record(HealthObservation(
            service_id=service_id, observed_at_utc=observed, phase=MORNING,
            expected_interval_s=30, warn_after_s=60, down_after_s=90,
            state=HEALTHY, process_state=HEALTHY, business_state="unknown",
            reason_code="healthy", source="test",
        ))
    live = ServiceConsole(tmp_path, health_store=store, now=lambda: NOW)

    assert [row["spec"].id for row in live.health_timeline()] == [
        "market-gateway", "watchdog",
    ]


def test_configured_empty_health_store_never_falls_back_to_remote_probes(tmp_path):
    live = ServiceConsole(
        tmp_path, health_store=HealthStore(tmp_path / "health.sqlite3"), now=lambda: NOW,
        systemd=lambda unit: (_ for _ in ()).throw(AssertionError("must not probe")),
        windows_task=lambda task: (_ for _ in ()).throw(AssertionError("must not ssh")),
    )
    assert {row["state"] for row in live.process_status()} == {"unknown"}


def test_persisted_health_stays_fresh_for_checkpoint_interval_plus_headroom(tmp_path):
    store = HealthStore(tmp_path / "health.sqlite3")
    observed = (NOW - timedelta(minutes=4)).replace(tzinfo=SHANGHAI)
    store.record(HealthObservation(
        service_id="market-gateway", observed_at_utc=observed, phase=MORNING,
        expected_interval_s=1, warn_after_s=3, down_after_s=10,
        state=HEALTHY, process_state=HEALTHY, business_state="unknown",
        reason_code="heartbeat_fresh", source="redis_heartbeat",
    ))
    live = ServiceConsole(tmp_path, health_store=store, now=lambda: NOW)
    assert next(r for r in live.process_status()
                if r["spec"].id == "market-gateway")["state"] == "ok"

    stale = ServiceConsole(
        tmp_path, health_store=store, now=lambda: NOW + timedelta(minutes=4),
    )
    row = next(r for r in stale.process_status() if r["spec"].id == "market-gateway")
    assert row["state"] == "unknown"
    assert row["health_observation"]["reason_code"] == "observer_stale"


def test_windows_task_query_uses_read_only_encoded_powershell(monkeypatch, tmp_path):
    import base64
    import subprocess

    def fake_run(args, **kwargs):
        script = base64.b64decode(args[-1]).decode("utf-16le")
        assert "Get-ScheduledTask -TaskName 'qsys-watchdog'" in script
        assert "Get-ScheduledTaskInfo -TaskName 'qsys-watchdog'" in script
        assert "Start-ScheduledTask" not in script
        return subprocess.CompletedProcess(
            args, 0, b'{"State":"Ready","LastTaskResult":0}',
            b'\xd5\xfd\xd4\xda',  # PowerShell stderr 可以不是 UTF-8
        )

    monkeypatch.setattr("qsys.web.service_views.subprocess.run", fake_run)
    assert windows_task_show("qsys-watchdog", host="win.example", key=tmp_path / "key") == {
        "State": "Ready", "LastTaskResult": 0,
    }


def test_run_detail_page(tmp_path, warehouse, monkeypatch):
    root = tmp_path / "runs"
    record(root, "tushare-sync", datetime(2026, 9, 16, 17, 0), fail_step, monkeypatch)
    run_id = next(root.rglob("*.json")).stem
    http = client(root, warehouse)
    page = http.get(f"/services/runs/2026-09-16/{run_id}")
    assert page.status_code == 200
    assert "0 行" in page.text
    assert http.get("/services/runs/2026-09-16/missing").status_code == 404


def test_services_route_is_404_when_not_enabled(warehouse):
    config = WebConfig()
    http = TestClient(create_app(MonitorService(SessionJournal(warehouse), config=config), config))
    assert http.get("/services").status_code == 404


def test_failed_checks_turn_a_successful_run_red_and_show_on_detail(tmp_path, warehouse, monkeypatch):
    root = tmp_path / "runs"
    record(root, "tushare-sync", datetime(2026, 9, 16, 17, 0), monkeypatch=monkeypatch)

    def checks(run):
        run.session = date(2026, 9, 16)
        with run.step("tushare-sync｜涨停列表到货", table="limit_list_daily") as step:
            step.fail("期望：当日涨停/炸板列表非空；实际：0 行")

    record(root, "service-checks", datetime(2026, 9, 16, 20, 0), checks, monkeypatch)
    got = states(console(root).board(14), "tushare-sync")
    assert got[date(2026, 9, 16)] == "qcbad"
    sync_id = next(p for p in root.rglob("tushare-sync-*.json")).stem
    page = client(root, warehouse).get(f"/services/runs/2026-09-16/{sync_id}")
    assert "涨停列表到货" in page.text and "0 行" in page.text


def check_run(session, *steps):
    def fill(run):
        run.session = session
        for name, status in steps:
            with run.step(name) as step:
                step.status = status
    return fill


def test_repaired_checks_show_fixed_and_missed_service_with_passing_data_shows_fixed(
        tmp_path, monkeypatch):
    day = date(2026, 9, 17)
    record(tmp_path, "daily-archive", datetime(2026, 9, 17, 23, 8), monkeypatch=monkeypatch)
    record(tmp_path, "service-checks", datetime(2026, 9, 17, 20, 0), check_run(
        day, ("daily-archive｜分钟线到货", "failed"),
        ("premarket-limits｜开盘前取到当日涨跌停价", "failed")), monkeypatch)
    record(tmp_path, "service-checks", datetime(2026, 9, 17, 23, 10), check_run(
        day, ("daily-archive｜分钟线到货", "repaired"),
        ("premarket-limits｜开盘前取到当日涨跌停价", "repaired")), monkeypatch)
    board = console(tmp_path).board(14)
    assert states(board, "daily-archive")[day] == "fixed"
    assert states(board, "premarket-limits")[day] == "fixed"     # 没执行，但数据核对已通过
    assert states(board, "service-checks")[day] == "fixed"       # 20:00 失败、23:10 已修复


def test_missed_service_stays_missed_while_its_data_check_fails(tmp_path, monkeypatch):
    day = date(2026, 9, 17)
    record(tmp_path, "service-checks", datetime(2026, 9, 17, 20, 0), check_run(
        day, ("premarket-limits｜开盘前取到当日涨跌停价", "failed")), monkeypatch)
    assert states(console(tmp_path).board(14), "premarket-limits")[day] == "missed"


def test_runs_land_on_the_session_they_checked_not_the_day_they_started(
        tmp_path, monkeypatch):
    """09-18 早上补跑的是 09-17 那天的活，格子必须记在 09-17。"""
    session = date(2026, 9, 17)
    record(tmp_path, "daily-archive", datetime(2026, 9, 17, 15, 40), fail_step, monkeypatch)
    record(tmp_path, "daily-archive", datetime(2026, 9, 18, 9, 43),
           lambda run: setattr(run, "session", session), monkeypatch)
    next_morning = ServiceConsole(
        tmp_path, calendar=lambda: AShareCalendar(SESSIONS),
        systemd=lambda unit: {}, now=lambda: datetime(2026, 9, 18, 12, 0),
    )
    got = states(next_morning.board(14), "daily-archive")
    assert got[session] == "fixed"                 # 当天失败、次日补跑成功
    assert got[date(2026, 9, 18)] == "closed"      # 补跑那天不是交易日，没有它自己的活
    cells = next(cells for spec, cells in next_morning.board(14)[1]
                 if spec.id == "daily-archive")
    assert next(cell for cell in cells if cell.day == session).detail_url.startswith(
        "/services/runs/2026-09-18/"
    )


def test_resident_services_show_their_check_results_on_the_board(tmp_path, monkeypatch):
    """影子盘与模拟盘没有运行记录，格子只反映当晚质检的结论。"""
    day = date(2026, 9, 17)
    record(tmp_path, "service-checks", datetime(2026, 9, 17, 20, 0), check_run(
        day, ("shadow-core｜当日策略事件已执行", "failed"),
        ("paper｜当日策略事件已执行", "succeeded")), monkeypatch)
    board = console(tmp_path).acceptance_board(14)
    assert states(board, "shadow-core")[day] == "qcbad"
    assert states(board, "paper")[day] == "ok"
    assert states(board, "shadow-core")[date(2026, 9, 16)] == "none"   # 没质检就不判
    cells = next(cells for spec, cells in board[1] if spec.id == "shadow-core")
    assert next(cell for cell in cells if cell.day == day).detail_url.startswith(
        "/services/runs/2026-09-17/service-checks-"
    )


def test_live_business_health_populates_acceptance_board_and_service_detail(
        tmp_path, warehouse):
    store = HealthStore(tmp_path / "health.sqlite3")
    store.record(HealthObservation(
        service_id="shadow-core",
        observed_at_utc=NOW.replace(tzinfo=SHANGHAI),
        phase=MORNING,
        expected_interval_s=1,
        warn_after_s=3,
        down_after_s=10,
        state=DOWN,
        process_state=HEALTHY,
        business_state=DOWN,
        reason_code="strategy_event_failed",
        source="redis_heartbeat",
        details={"trading_chain": {
            "detail": "auction:09:30:00 事件已过期，未补跑",
            "expected": 2,
            "completed": 1,
        }},
    ))
    live = ServiceConsole(
        tmp_path / "runs",
        calendar=lambda: AShareCalendar(SESSIONS),
        health_store=store,
        now=lambda: NOW,
    )

    cells = next(cells for spec, cells in live.acceptance_board(14)[1]
                 if spec.id == "shadow-core")
    today = next(cell for cell in cells if cell.day == NOW.date())
    assert today.state == "bad"
    assert today.label == "事件失败"
    assert today.detail_url == "/services/shadow-core"

    config = WebConfig()
    page = TestClient(create_app(
        MonitorService(SessionJournal(warehouse), config=config), config,
        services=live,
    )).get("/services/shadow-core")
    assert page.status_code == 200
    assert "交易链路监控" in page.text
    assert "事件失败" in page.text
    assert "auction:09:30:00 事件已过期，未补跑" in page.text
    assert "1 / 2" in page.text


def test_a_monthly_service_is_not_judged_missed_on_ordinary_days(tmp_path, warehouse):
    """月度任务只在执行日该有记录；混进每日看板会天天报漏跑。"""
    console = ServiceConsole(tmp_path, systemd=lambda unit: {}, now=lambda: NOW)

    assert "auction-study" not in [spec.id for spec, _ in console.board()[1]]
    columns, rows, _ = console.periodic_board()
    assert [spec.id for spec, _ in rows] == ["auction-study", "gate-study"]
    states = {cell.day: cell.state for cell in rows[0][1]}
    assert {state for day, state in states.items() if day.day != 1} == {"none"}
