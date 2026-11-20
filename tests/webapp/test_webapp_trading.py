from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient

from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, Order, OrderType, Side
from qsys.core.types import Symbol
from qsys.live.kill_review import ReviewInputs, review
from qsys.live.messages import AccountSnapshot, DispatchStatusEvent, Heartbeat, OrderStatusEvent, PositionSnapshot
from qsys.live.store import LiveStore
from qsys.live.transport import MemoryTransport
from qsys.ops.journal import SessionJournal
from qsys.webapp.live_kill_status import KillSource, LiveKillStatus
from qsys.webapp.queries.trading import TradingQuery
from qsys.webapp.routes.trading import trading_router

TZ = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 11, 19)
NOW = datetime(2026, 11, 19, 14, 0, tzinfo=TZ)
SYM = Symbol.parse("600000.SH")


class Kill:
    def read(self):
        return LiveKillStatus("clear", "停机闸已解除", "只表示三道闸未生效", NOW.isoformat(), 5,
                              (KillSource("linux", "Linux", "clear", "未发现停机标记"),
                               KillSource("redis", "Redis", "clear", "停机闸未生效"),
                               KillSource("agent", "Windows", "clear", "停机闸未生效", NOW.isoformat())))


def query(warehouse, tmp_path: Path):
    journal = SessionJournal(warehouse)
    journal.record_equity(DAY, cash=90_000, market_value=10_000)
    journal.record_reconciliation(DAY, "eod", mode="live", is_consistent=True, at=NOW)
    state = tmp_path / "live.sqlite3"
    store = LiveStore(state)
    account = AccountSnapshot(at=NOW, cash=90_000, market_value=10_000, total_asset=100_000,
                              active_order_count=0,
                              positions={str(SYM): PositionSnapshot(total_qty=1000, available_qty=1000,
                                                                    avg_cost=10, market_value=10_000)})
    store.initialize(account)
    transport = MemoryTransport()
    transport.set_key("state:account", account.model_dump_json())
    for source in ("core", "agent", "market"):
        transport.set_key(f"state:heartbeat:{source}", Heartbeat(
            source=source, at=NOW, expected_interval_s=5,
            trading_mode="live" if source == "agent" else None,
            orders_enabled=True if source == "agent" else None,
            account_protected=True if source == "agent" else None,
            local_kill_engaged=False if source == "agent" else None).model_dump_json())
    return TradingQuery(journal=journal, store=store, transport=transport, kill_reader=Kill(),
                        review_path=tmp_path / "review.json", clock=lambda: NOW), store, journal


def test_live_keeps_return_missing_and_uses_direct_market_value(warehouse, tmp_path):
    q, _, _ = query(warehouse, tmp_path)
    got = q.live(DAY)
    assert got.observation.total_value == 100_000
    assert got.observation.return_ratio is None
    assert got.positions[0].market_value == 10_000
    assert "资金变动流水" in got.missing["return_ratio"]
    assert {h.source: h.fresh for h in got.heartbeats} == {"core": True, "agent": True, "market": True}


def test_orders_do_not_infer_broker_acceptance_and_unknown_is_not_retried(warehouse, tmp_path):
    q, store, journal = query(warehouse, tmp_path)
    order = Order("o1", SYM, Side.BUY, 100, OrderType.LIMIT, 10.0, NOW)
    store.record_orders("auction", [order])
    dispatch = DispatchStatusEvent(client_order_id="o1", strategy_id="s", session_id=str(DAY),
                                   attempt_id="1" * 32, session_nonce="2" * 32,
                                   command_sha256="3" * 64, state="unknown", at=NOW, reason="timeout")
    fill = Fill("o1", SYM, Side.BUY, 100, 10, 5, NOW + timedelta(seconds=3),
                broker_order_id="b1", broker_trade_id="t1")
    portfolio = Portfolio(100_000)
    store.commit_reports(portfolio, {}, {}, [fill], [], [], {"o1": 0}, at=NOW, dispatches=[dispatch])
    journal.record_fills(DAY, [fill])
    got = q.orders(DAY)
    assert got.orders[0].broker_accepted_at is None  # 成交时刻不能冒充券商受理时刻
    assert got.orders[0].broker_order_id == "b1"
    assert got.orders[0].unknown_no_retry is True
    assert "不重发" not in got.missing["broker_accepted_at"]  # 两条口径分别表达


def test_reconciliation_does_not_invent_order_layer_and_reads_review(warehouse, tmp_path):
    q, _, _ = query(warehouse, tmp_path)
    account = AccountSnapshot(at=NOW, cash=100_000, total_asset=100_000, active_order_count=0,
                              positions={})
    inputs = ReviewInputs(None, None, True,
                          Heartbeat(source="agent", at=NOW, trading_mode="live", orders_enabled=True,
                                    account_protected=True, local_kill_engaged=False),
                          Heartbeat(source="market", at=NOW), account, Portfolio(100_000), 0)
    q.review_path.write_text(review(inputs, confirmed_by="维护者", now=NOW).model_dump_json(), encoding="utf-8")
    got = q.reconciliation(DAY)
    cells = {c.layer: c.state for c in got.heatmap if c.day == DAY}
    assert cells == {"cash": "consistent", "positions": "consistent", "orders": "unknown"}
    assert got.review and got.review.overall == "ready" and got.review.stale is False


def test_trading_routes_disabled_without_query():
    app = FastAPI()
    app.include_router(trading_router(None))
    client = TestClient(app)
    for url in ("/api/app/trading/live", "/api/app/trading/orders",
                "/api/app/trading/reconciliation", "/api/app/trading/runs",
                "/api/app/trading/paper"):
        assert client.get(url).json() == {"schema_version": 1, "enabled": False}


def test_action_request_rejects_extra_fields(warehouse, tmp_path):
    q, _, _ = query(warehouse, tmp_path)
    q.paper = object()  # 只需证明请求体先按 extra=forbid 校验
    app = FastAPI()
    app.include_router(trading_router(q))
    response = TestClient(app).post("/api/app/trading/paper/x/action",
                                    json={"action": "pause", "force": True})
    assert response.status_code == 422
