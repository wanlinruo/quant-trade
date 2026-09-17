"""服务管理页：看板状态判定、明细页、在没有任何记录时也能打开。"""

from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

from qsys.core.calendar import AShareCalendar
from qsys.ops import services
from qsys.ops.services import ServiceRun
from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.config import WebConfig
from qsys.web.service_views import ServiceConsole
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
    assert http.get("/services/tushare-sync").status_code == 200
    assert http.get("/services/nope").status_code == 404


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
