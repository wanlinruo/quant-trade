from datetime import datetime

from qsys.agent.adapter import FakeAdapter
from qsys.agent.execution_agent import ExecutionAgent
from qsys.live.config import StreamConfig
from qsys.live.messages import (
    AccountSnapshot,
    ControlCommand,
    FillEvent,
    Heartbeat,
    OrderCommand,
    RejectEvent,
)
from qsys.live.transport import MemoryTransport
from qsys.live.safety import SafetyPolicy, TradingMode

STREAMS = StreamConfig()
T = datetime(2024, 1, 3, 10, 0)


def cmd(oid="c-1", qty=100) -> OrderCommand:
    return OrderCommand(client_order_id=oid, symbol="600036.SH", side="buy", qty=qty,
                        order_type="market", limit_price=None, created_at=T)


def make(**kw):
    transport = MemoryTransport()
    adapter = FakeAdapter(**kw)
    agent = ExecutionAgent(transport, adapter, STREAMS)
    return agent, transport, adapter


def drain(transport, stream, model):
    """读流中全部消息，绕过消费组的 $ 语义。"""
    return [model.from_payload(m.payload) for m in transport.all_messages(stream)]


def test_agent_places_the_order():
    agent, t, adapter = make()
    t.append(STREAMS.orders, cmd().to_payload())
    assert agent.run_once() == 1
    assert adapter.placed == [("600036.SH", "buy", 100, "market", None)]


def test_agent_publishes_a_fill():
    agent, t, _ = make()
    t.append(STREAMS.orders, cmd().to_payload())
    agent.run_once()
    fills = drain(t, STREAMS.fills, FillEvent)
    assert len(fills) == 1
    assert fills[0].client_order_id == "c-1"
    assert fills[0].qty == 100
    assert fills[0].broker_trade_id == "T1"


def test_accepted_but_unfilled_order_does_not_publish_a_fill():
    agent, t, _ = make(auto_fill=False)
    t.append(STREAMS.orders, cmd().to_payload())
    agent.run_once()
    assert drain(t, STREAMS.fills, FillEvent) == []


def test_duplicate_command_does_not_place_twice():
    """网络重传永不导致重复下单。"""
    agent, t, adapter = make()
    payload = cmd().to_payload()
    t.append(STREAMS.orders, payload)
    t.append(STREAMS.orders, payload)
    agent.run_once()
    assert len(adapter.placed) == 1


def test_duplicate_command_replays_the_original_fill():
    agent, t, _ = make()
    payload = cmd().to_payload()
    t.append(STREAMS.orders, payload)
    agent.run_once()
    t.append(STREAMS.orders, payload)
    agent.run_once()
    fills = drain(t, STREAMS.fills, FillEvent)
    assert len(fills) == 1


def test_adapter_failure_produces_a_reject():
    agent, t, _ = make(fail_with="broker offline")
    t.append(STREAMS.orders, cmd().to_payload())
    agent.run_once()
    rejects = drain(t, STREAMS.rejects, RejectEvent)
    assert len(rejects) == 1
    assert "broker offline" in rejects[0].reason


def test_failed_command_can_be_retried():
    """失败的指令不记入已处理集合，允许重试。"""
    agent, t, adapter = make(fail_with="transient")
    t.append(STREAMS.orders, cmd().to_payload())
    agent.run_once()
    adapter.fail_with = None
    t.append(STREAMS.orders, cmd().to_payload())
    agent.run_once()
    assert len(adapter.placed) == 1


def test_control_cancel_all_is_forwarded():
    agent, t, adapter = make()
    t.append(STREAMS.control, ControlCommand(action="cancel_all", at=T).to_payload())
    agent.run_once()
    assert adapter.cancelled is True


def test_publish_account_writes_the_snapshot():
    agent, t, _ = make(cash=98_765.43, positions={"600036.SH": (500, 500, 27.63)})
    agent.publish_account()
    raw = t.get_key(STREAMS.account)
    assert raw is not None
    snap = AccountSnapshot.model_validate_json(raw)
    assert snap.cash == 98_765.43
    assert snap.positions["600036.SH"].available_qty == 500


def test_beat_reports_the_effective_windows_safety_posture(tmp_path):
    transport = MemoryTransport()
    safety = SafetyPolicy(
        TradingMode.LIVE, account_id="A1", protected_account_ids=("A1",),
        orders_enabled=True, local_kill_path=tmp_path / "K",
    )
    agent = ExecutionAgent(
        transport, FakeAdapter(), STREAMS, safety=safety,
    )
    agent.beat()
    raw = transport.get_key(f"{STREAMS.heartbeat}:agent")
    beat = Heartbeat.model_validate_json(raw)
    assert beat.trading_mode == "live"
    assert beat.orders_enabled is True
    assert beat.account_protected is True
    assert beat.local_kill_engaged is False


def test_run_once_with_no_commands_returns_zero():
    agent, _, _ = make()
    assert agent.run_once() == 0


def test_fresh_but_timed_out_core_event_fails_closed_on_windows(tmp_path):
    transport = MemoryTransport()
    adapter = FakeAdapter(auto_fill=False)
    safety = SafetyPolicy(
        TradingMode.LIVE, account_id="A1", protected_account_ids=("A1",),
        orders_enabled=True, local_kill_path=tmp_path / "K",
    )
    agent = ExecutionAgent(transport, adapter, STREAMS, safety=safety)
    transport.set_key(
        f"{STREAMS.heartbeat}:core",
        Heartbeat(
            source="core", at=datetime.now(), activity="event",
            event_key="2026-10-09:bar:15:00:00", event_phase="bar",
            event_started_at=datetime.now(), event_timeout_s=30,
            event_timed_out=True,
        ).model_dump_json(),
    )

    assert agent.enforce_core_heartbeat() is False
    assert adapter.cancelled is True
    assert (tmp_path / "K").read_text() == "core strategy event timeout"

    adapter.cancelled = False
    assert agent.enforce_core_heartbeat() is False
    assert adapter.cancelled is False  # 同一超时事件不每轮重复撤单


def test_malformed_command_is_rejected_not_fatal():
    """畸形消息不应让代理崩溃——记录并跳过。"""
    agent, t, adapter = make()
    t.append(STREAMS.orders, {"type": "OrderCommand", "data": "not json"})
    t.append(STREAMS.orders, cmd().to_payload())
    processed = agent.run_once()
    assert processed >= 1
    assert len(adapter.placed) == 1


def test_remote_kill_blocks_buy_but_keeps_reducing_path_open(tmp_path):
    transport = MemoryTransport()
    adapter = FakeAdapter(auto_fill=False)
    safety = SafetyPolicy(
        TradingMode.LIVE, account_id="A1", protected_account_ids=("A1",),
        orders_enabled=True, local_kill_path=tmp_path / "K",
    )
    agent = ExecutionAgent(transport, adapter, STREAMS, safety=safety)
    transport.set_key("control:kill", "manual")
    transport.append(STREAMS.orders, cmd("buy").to_payload())
    sell = cmd("sell").model_copy(update={"side": "sell"})
    transport.append(STREAMS.orders, sell.to_payload())
    agent.run_once()
    assert adapter.placed == [("600036.SH", "sell", 100, "market", None)]
