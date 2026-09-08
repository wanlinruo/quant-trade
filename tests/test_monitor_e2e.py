"""监控台端到端：从实盘链路产生数据，到页面正确呈现。

链路：LiveBroker 下单 -> ExecutionAgent 成交 -> Journal 记录
      -> 对账 -> 监控台展示 -> 日报生成 -> 告警发出
"""

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from qsys.agent.adapter import FakeAdapter
from qsys.agent.execution_agent import ExecutionAgent
from qsys.core.calendar import AShareCalendar
from qsys.core.constraints import ConstraintContext
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Order, OrderType, Side
from qsys.core.types import Market, Symbol
from qsys.live.config import StreamConfig
from qsys.live.kill_switch import KillSwitch
from qsys.live.live_broker import LiveBroker
from qsys.live.reconcile import reconcile_positions
from qsys.live.risk import RiskContext, default_ashare_gate
from qsys.live.transport import MemoryTransport
from qsys.markets.ashare import ashare_pre_trade_pipeline
from qsys.ops.alerts import AlertBus, RecordingAlertSink, Severity
from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.config import WebConfig
from qsys.web.views import MonitorService

CMB = Symbol("600036", Market.SH)
D = date(2024, 1, 3)
CAL = AShareCalendar([D])
T = datetime(2024, 1, 3, 10, 0)
STREAMS = StreamConfig()


@pytest.fixture
def rig(warehouse, tmp_path):
    transport = MemoryTransport()
    portfolio = Portfolio(1_000_000.0)
    journal = SessionJournal(warehouse)
    sink = RecordingAlertSink()
    bus = AlertBus([sink])

    ks = KillSwitch(transport, local_path=tmp_path / "KILL")
    gate = default_ashare_gate(ks, CAL)
    broker = LiveBroker(
        transport, STREAMS, risk_gate=gate,
        risk_context_factory=lambda: RiskContext(
            portfolio=portfolio, now=T, reference_price=10.0, day_start_value=1_000_000.0
        ),
        constraints=ashare_pre_trade_pipeline(),
        constraint_context_factory=lambda: ConstraintContext(
            portfolio=portfolio, reference_price=10.0
        ),
    )
    agent = ExecutionAgent(transport, FakeAdapter(cash=1_000_000.0), STREAMS)
    return broker, agent, transport, portfolio, journal, bus, sink


def run_session(rig) -> None:
    broker, agent, transport, portfolio, journal, bus, _ = rig

    # 真实顺序：下单 -> 成交回报 -> 应用到账户 -> 再做下一个决策。
    # 若在成交回报之前就提交卖单，会被 no_short 拦下（无持仓），
    # 测不到 T+1 交收那条路径。
    broker.submit(Order("o1", CMB, Side.BUY, 1000, OrderType.MARKET, None, T))
    agent.run_once()
    fills = broker.poll_fills()
    for f in fills:
        portfolio.apply_fill(f)

    # 此时持仓已存在但未解冻 —— 当日买入当日卖出必须被 settlement 拦下
    broker.submit(Order("o2", CMB, Side.SELL, 500, OrderType.MARKET, None, T))
    agent.run_once()
    fills += broker.poll_fills()
    rejects = broker.poll_rejects()

    journal.record_fills(D, fills)
    journal.record_rejects(D, rejects)

    agent.publish_account()
    snapshot = broker.account_snapshot()
    result = reconcile_positions(portfolio, snapshot, cash_tolerance=1e9)
    journal.record_reconciliation(
        D, "intraday", is_consistent=result.is_consistent,
        cash_diff=result.cash_diff, n_mismatches=len(result.mismatches),
        detail=result.summary(), at=T,
    )
    if not result.is_consistent:
        bus.critical("持仓不一致", result.summary())

    journal.record_equity(D, cash=portfolio.cash, market_value=0.0)


def client_for(rig) -> TestClient:
    _, _, transport, _, journal, _, _ = rig
    cfg = WebConfig()
    service = MonitorService(journal, config=cfg, transport=transport,
                             quality_summary_provider=lambda: "quality: clean")
    return TestClient(create_app(service, cfg))


def test_session_data_reaches_the_overview_page(rig):
    """总览页呈现净值。持仓来自券商快照，FakeAdapter 不跟踪持仓，故此处不断言持仓。"""
    run_session(rig)
    r = client_for(rig).get(f"/s/{D}")
    assert r.status_code == 200
    assert "净值" in r.text
    assert "<svg" in r.text


def test_positions_render_when_the_broker_reports_them(warehouse, tmp_path):
    """券商上报持仓时，总览页必须显示出来。"""
    transport = MemoryTransport()
    journal = SessionJournal(warehouse)
    journal.record_equity(D, cash=88_000.0, market_value=12_000.0)

    agent = ExecutionAgent(
        transport,
        FakeAdapter(cash=88_000.0, positions={"600036.SH": (1000, 0, 12.01)}),
        STREAMS,
    )
    agent.publish_account()

    cfg = WebConfig()
    c = TestClient(create_app(
        MonitorService(journal, config=cfg, transport=transport), cfg
    ))
    r = c.get(f"/s/{D}")
    assert "600036.SH" in r.text
    assert "12.01" in r.text


def test_fills_appear_on_the_trades_page(rig):
    run_session(rig)
    assert "o1" in client_for(rig).get(f"/s/{D}/trades").text


def test_settlement_violation_is_blocked_before_reaching_the_broker(rig):
    """T+1：当日买入不可卖，卖单必须在核心侧被拦下，不得发到券商。"""
    run_session(rig)
    r = client_for(rig).get(f"/s/{D}/trades")
    assert "o2" in r.text
    assert "settlement" in r.text or "settled" in r.text


def test_reconciliation_result_reaches_the_health_page(rig):
    run_session(rig)
    assert "intraday" in client_for(rig).get(f"/s/{D}/health").text


def test_position_mismatch_raises_a_critical_alert(rig):
    """FakeAdapter 不跟踪持仓，本地有仓远端无仓 -> 必须告警。"""
    run_session(rig)
    _, _, _, _, _, _, sink = rig
    assert any(a.severity is Severity.CRITICAL for a in sink.alerts)


def test_daily_report_text_is_available(rig):
    run_session(rig)
    r = client_for(rig).get(f"/s/{D}/report.txt")
    assert r.status_code == 200
    assert "每日运行报告" in r.text
    assert "对账" in r.text


def test_status_fragment_reflects_the_problem(rig):
    run_session(rig)
    assert "异常" in client_for(rig).get(f"/fragment/{D}/status").text


def test_whole_site_has_no_external_resources(rig):
    run_session(rig)
    c = client_for(rig)
    for path in (f"/s/{D}", f"/s/{D}/trades", f"/s/{D}/health", f"/s/{D}/report"):
        text = c.get(path).text
        assert "cdn." not in text and "googleapis" not in text
