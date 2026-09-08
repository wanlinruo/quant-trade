from datetime import date, datetime, timedelta

import pytest

from qsys.core.events import Reject
from qsys.core.trading import Fill, Side
from qsys.core.types import Market, Symbol
from qsys.live.heartbeat import HeartbeatMonitor
from qsys.live.messages import AccountSnapshot, PositionSnapshot
from qsys.live.transport import MemoryTransport
from qsys.ops.journal import SessionJournal
from qsys.web.config import WebConfig
from qsys.web.views import MonitorService

CMB = Symbol("600036", Market.SH)
D0 = date(2024, 1, 2)
D = date(2024, 1, 3)
T = datetime(2024, 1, 3, 9, 30)


@pytest.fixture
def service(warehouse):
    journal = SessionJournal(warehouse)
    journal.record_equity(D0, cash=100_000.0, market_value=0.0)
    journal.record_equity(D, cash=88_000.0, market_value=13_000.0)
    journal.record_fills(D, [Fill("a", CMB, Side.BUY, 1000, 12.0, 5.1, T)])
    journal.record_rejects(D, [Reject("b", CMB, Side.BUY, 100, "涨停买不到", T)])
    journal.record_reconciliation(D, "intraday", is_consistent=True, at=T)

    transport = MemoryTransport()
    transport.set_key(
        "state:account",
        AccountSnapshot(
            at=T, cash=88_000.0,
            positions={"600036.SH": PositionSnapshot(total_qty=1000, available_qty=0,
                                                     avg_cost=12.01)},
        ).model_dump_json(),
    )
    HeartbeatMonitor(transport).beat("agent")
    HeartbeatMonitor(transport).beat("core")

    return MonitorService(journal, config=WebConfig(), transport=transport)


def test_latest_session(service):
    assert service.latest_session() == D


def test_latest_session_is_none_when_empty(warehouse):
    svc = MonitorService(SessionJournal(warehouse), config=WebConfig())
    assert svc.latest_session() is None


def test_has_session(service):
    assert service.has_session(D) is True
    assert service.has_session(date(2020, 1, 1)) is False


def test_overview_carries_the_equity_curve(service):
    assert service.overview(D).equity_curve == [100_000.0, 101_000.0]


def test_overview_equity_line_shows_change(service):
    view = service.overview(D)
    assert view.equity_line.total_value == 101_000.0
    assert view.equity_line.change_pct == pytest.approx(0.01)


def test_overview_lists_positions_from_the_account_snapshot(service):
    view = service.overview(D)
    assert len(view.positions) == 1
    assert view.positions[0]["symbol"] == "600036.SH"
    assert view.positions[0]["total_qty"] == 1000


def test_overview_positions_are_empty_without_a_snapshot(warehouse):
    svc = MonitorService(SessionJournal(warehouse), config=WebConfig(),
                         transport=MemoryTransport())
    assert svc.overview(D).positions == ()


def test_overview_reports_heartbeats(service):
    view = service.overview(D)
    assert any(h["source"] == "agent" and h["alive"] for h in view.heartbeats)


def test_overview_marks_a_silent_source(service):
    """心跳超时必须被标出来。"""
    view = service.overview(D, now=datetime.now() + timedelta(seconds=600))
    assert all(not h["alive"] for h in view.heartbeats)
    assert view.is_healthy is False


def test_overview_without_a_transport_has_no_heartbeats(warehouse):
    svc = MonitorService(SessionJournal(warehouse), config=WebConfig())
    assert svc.overview(D).heartbeats == ()


def test_trades_view(service):
    view = service.trades(D)
    assert len(view.fills) == 1
    assert len(view.rejects) == 1
    assert view.rejects[0]["reason"] == "涨停买不到"


def test_health_view(service):
    view = service.health(D)
    assert len(view.reconciliations) == 1
    assert view.reconciliations[0]["layer"] == "intraday"


def test_health_view_uses_the_quality_provider(warehouse):
    svc = MonitorService(
        SessionJournal(warehouse), config=WebConfig(),
        quality_summary_provider=lambda: "quality: clean (0 violations)",
    )
    assert "clean" in svc.health(D).quality_summary


def test_quality_provider_failure_is_contained(warehouse):
    """质量报告取不到不能让整个页面 500。"""

    def broken() -> str:
        raise RuntimeError("warehouse offline")

    svc = MonitorService(SessionJournal(warehouse), config=WebConfig(),
                         quality_summary_provider=broken)
    assert "warehouse offline" in svc.health(D).quality_summary


def test_report_builds_a_daily_report(service):
    report = service.report(D)
    assert report.dt == D
    assert report.is_healthy is True


def test_equity_curve_is_limited_by_config(warehouse):
    journal = SessionJournal(warehouse)
    for i in range(10):
        journal.record_equity(date(2024, 1, 1) + timedelta(days=i),
                              cash=float(i), market_value=0.0)
    svc = MonitorService(journal, config=WebConfig(equity_days=3))
    assert len(svc.overview(date(2024, 1, 10)).equity_curve) == 3
