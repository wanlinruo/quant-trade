from datetime import datetime

from qsys.agent.adapter import FakeAdapter
from qsys.agent.execution_agent import ExecutionAgent
from qsys.live.commissioning import COMMISSIONING_KEY, CommissioningLease
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


def test_bounded_commissioning_lease_allows_stopped_core_and_is_reported(tmp_path):
    transport = MemoryTransport()
    adapter = FakeAdapter(auto_fill=False)
    safety = SafetyPolicy(
        TradingMode.LIVE, account_id="A1", protected_account_ids=("A1",),
        orders_enabled=True, local_kill_path=tmp_path / "K",
    )
    agent = ExecutionAgent(transport, adapter, STREAMS, safety=safety)
    transport.set_key(
        COMMISSIONING_KEY, CommissioningLease.arm().model_dump_json(),
    )

    assert agent.enforce_core_heartbeat(startup_grace_seconds=0) is True
    assert adapter.cancelled is False
    agent.beat()
    beat = Heartbeat.model_validate_json(
        transport.get_key(f"{STREAMS.heartbeat}:agent")
    )
    assert beat.commissioning_lease_active is True


def test_commissioning_lease_blocks_strategy_but_accepts_the_fixed_order(tmp_path):
    transport = MemoryTransport()
    adapter = FakeAdapter(auto_fill=False)
    safety = SafetyPolicy(
        TradingMode.LIVE, account_id="A1", protected_account_ids=("A1",),
        orders_enabled=True, local_kill_path=tmp_path / "K",
    )
    agent = ExecutionAgent(transport, adapter, STREAMS, safety=safety)
    transport.set_key(COMMISSIONING_KEY, CommissioningLease.arm().model_dump_json())
    transport.append(STREAMS.orders, cmd().to_payload())
    commission = OrderCommand(
        client_order_id="c20261012-100000-abcdef",
        strategy_id="commissioning", symbol="511380.SH", side="buy", qty=100,
        order_type="limit", limit_price=12.8, created_at=datetime.now(),
    )
    transport.append(STREAMS.orders, commission.to_payload())

    agent.run_once()

    assert adapter.placed == [("511380.SH", "buy", 100, "limit", 12.8)]
    rejects = drain(transport, STREAMS.rejects, RejectEvent)
    assert any("blocked during commissioning" in item.reason for item in rejects)


def test_commissioning_rejects_any_account_level_active_order(tmp_path):
    transport = MemoryTransport()
    adapter = FakeAdapter(auto_fill=False, active_order_count=1)
    safety = SafetyPolicy(
        TradingMode.LIVE, account_id="A1", protected_account_ids=("A1",),
        orders_enabled=True, local_kill_path=tmp_path / "K",
    )
    agent = ExecutionAgent(transport, adapter, STREAMS, safety=safety)
    transport.set_key(COMMISSIONING_KEY, CommissioningLease.arm().model_dump_json())
    commission = OrderCommand(
        client_order_id="c20261012-100000-abcdef",
        strategy_id="commissioning", symbol="511380.SH", side="buy", qty=100,
        order_type="limit", limit_price=12.8, created_at=datetime.now(),
    )
    transport.append(STREAMS.orders, commission.to_payload())

    agent.run_once()

    assert adapter.placed == []
    rejects = drain(transport, STREAMS.rejects, RejectEvent)
    assert len(rejects) == 1
    assert "active broker order" in rejects[0].reason


def test_commissioning_rejects_forged_id_and_partial_buy_but_allows_reducing_sell(
    tmp_path,
):
    transport = MemoryTransport()
    adapter = FakeAdapter(auto_fill=False)
    safety = SafetyPolicy(
        TradingMode.LIVE, account_id="A1", protected_account_ids=("A1",),
        orders_enabled=True, local_kill_path=tmp_path / "K",
    )
    agent = ExecutionAgent(transport, adapter, STREAMS, safety=safety)
    transport.set_key(COMMISSIONING_KEY, CommissioningLease.arm().model_dump_json())
    base = OrderCommand(
        client_order_id="c20261012-100000-abcdef",
        strategy_id="commissioning", symbol="511380.SH", side="buy", qty=100,
        order_type="limit", limit_price=12.8, created_at=datetime.now(),
    )
    transport.append(
        STREAMS.orders,
        base.model_copy(update={"client_order_id": "commission-forged"}).to_payload(),
    )
    transport.append(STREAMS.orders, base.model_copy(update={"qty": 50}).to_payload())
    transport.append(
        STREAMS.orders,
        base.model_copy(update={"side": "sell", "qty": 50}).to_payload(),
    )

    agent.run_once()

    assert adapter.placed == [("511380.SH", "sell", 50, "limit", 12.8)]
    rejects = drain(transport, STREAMS.rejects, RejectEvent)
    assert len(rejects) == 2


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


def test_commissioning_rejected_in_async_mode():
    class Dispatcher:
        def __init__(self):
            self.rejected = []

        def matches(self, transport, streams):
            return True

        def replay_existing(self, command):
            return False

        def reject_before_send(self, command, reason):
            self.rejected.append(reason)

        def dispatch(self, command):
            raise AssertionError('must not dispatch commissioning in async mode')
    transport, dispatcher = MemoryTransport(), Dispatcher()
    agent = ExecutionAgent(transport, FakeAdapter(), STREAMS, async_dispatcher=dispatcher)
    transport.append(STREAMS.orders, OrderCommand(
        client_order_id="qc2611090001", strategy_id="commissioning", symbol="511380.SH",
        side="buy", qty=100, order_type="limit", limit_price=12.0, created_at=T).to_payload())
    agent.run_once()
    assert dispatcher.rejected == ["commissioning_disabled_in_async_mode"]


def test_cycle_hook_receives_kill_state(tmp_path):
    seen = []
    transport = MemoryTransport()
    kill_file = tmp_path / "AGENT_KILL"
    agent = ExecutionAgent(
        transport, FakeAdapter(), STREAMS, cycle_hook=seen.append,
        safety=SafetyPolicy(TradingMode.LIVE, account_id="a", protected_account_ids=("a",),
                            orders_enabled=True, local_kill_path=kill_file))
    agent.run_once()
    kill_file.write_text("x")
    agent.run_once()
    kill_file.unlink()
    transport.set_key("control:kill", "manual")
    agent.run_once()
    assert seen == [False, True, True]


def _stopped_core_agent(tmp_path):
    from datetime import timedelta

    transport = MemoryTransport()
    adapter = FakeAdapter(auto_fill=False)
    safety = SafetyPolicy(
        TradingMode.LIVE, account_id="A1", protected_account_ids=("A1",),
        orders_enabled=True, local_kill_path=tmp_path / "K",
    )
    agent = ExecutionAgent(transport, adapter, STREAMS, safety=safety)
    agent._started_at = datetime.now() - timedelta(minutes=10)
    transport.set_key(
        f"{STREAMS.heartbeat}:core",
        Heartbeat(source="core", at=datetime.now() - timedelta(minutes=5)).model_dump_json(),
    )
    return agent, adapter, transport


def _resume(agent, transport):
    from qsys.live.messages import ControlCommand

    transport.append(STREAMS.control, ControlCommand(
        action="resume", at=datetime.now(), reason="manual",
    ).to_payload())
    agent.run_once()


def test_resume_gives_a_stopped_core_the_startup_grace(tmp_path):
    # 11-17：核心停止期间 resume 只放开约 1 秒，下一轮又因心跳超时落闸，
    # 核心启动复核要求闸已解除，二者死锁（#670）。resume 后按启动宽限等核心接管。
    agent, adapter, transport = _stopped_core_agent(tmp_path)
    assert agent.enforce_core_heartbeat() is False
    assert (tmp_path / "K").exists()

    _resume(agent, transport)
    adapter.cancelled = False

    assert not (tmp_path / "K").exists()
    assert agent.enforce_core_heartbeat() is False
    assert not (tmp_path / "K").exists()
    assert adapter.cancelled is False


def test_resume_grace_expires_and_fails_closed_again(tmp_path):
    from datetime import timedelta

    agent, adapter, transport = _stopped_core_agent(tmp_path)
    _resume(agent, transport)
    agent._core_grace_from = datetime.now() - timedelta(seconds=31)
    adapter.cancelled = False

    assert agent.enforce_core_heartbeat() is False
    assert (tmp_path / "K").read_text() == "core heartbeat timeout"
    assert adapter.cancelled is True


def _core_beat_aged(tmp_path, seconds):
    from datetime import timedelta

    agent, adapter, transport = _stopped_core_agent(tmp_path)
    transport.set_key(
        f"{STREAMS.heartbeat}:core",
        Heartbeat(source="core", at=datetime.now() - timedelta(seconds=seconds)).model_dump_json(),
    )
    return agent, adapter


def test_a_core_heartbeat_gap_under_30_seconds_does_not_latch_the_kill(tmp_path):
    # 11-18 09:42：一次隧道超时让核心心跳空窗 >10 秒，网关即锁存本地闸；Linux 侧
    # 同一故障只算 1 次失败（#677）。维护者 11-18 定阈值 30 秒。
    agent, adapter = _core_beat_aged(tmp_path, 25)
    assert agent.enforce_core_heartbeat() is True
    assert not (tmp_path / "K").exists()
    assert adapter.cancelled is False


def test_a_core_heartbeat_gap_over_30_seconds_still_fails_closed(tmp_path):
    agent, adapter = _core_beat_aged(tmp_path, 31)
    assert agent.enforce_core_heartbeat() is False
    assert (tmp_path / "K").read_text() == "core heartbeat timeout"
    assert adapter.cancelled is True


# ---- 执行器状态进心跳（#662 计划 03b）----

def _agent_with(adapter):
    transport = MemoryTransport()
    return ExecutionAgent(transport, adapter, STREAMS), transport


def _beat(transport):
    return Heartbeat.model_validate_json(transport.get_key(f"{STREAMS.heartbeat}:agent"))


def test_beat_carries_executor_status():
    from qsys.agent.qmt.adapter import ExecutorStatus

    class WithExecutor(FakeAdapter):
        def executor_status(self):
            return ExecutorStatus(epoch="E9", heartbeat_at=datetime(2026, 11, 18, 10, 0, 1),
                                  started_at=datetime(2026, 11, 18, 9, 0), latest_seq=42, ring_full=False)
    agent, t = _agent_with(WithExecutor())
    agent.beat()
    b = _beat(t)
    assert (b.executor_epoch, b.executor_latest_seq, b.executor_ring_full) == ("E9", 42, False)
    assert b.executor_heartbeat_at == datetime(2026, 11, 18, 10, 0, 1)
    assert b.executor_started_at == datetime(2026, 11, 18, 9, 0)


def test_beat_without_executor_capability_leaves_fields_none():
    agent, t = _agent_with(FakeAdapter())
    agent.beat()
    b = _beat(t)
    assert (b.executor_epoch, b.executor_heartbeat_at, b.executor_started_at, b.executor_latest_seq,
            b.executor_ring_full) == (None, None, None, None, None)


def test_executor_status_failure_never_blocks_heartbeat():
    class Broken(FakeAdapter):
        def executor_status(self):
            raise OSError("mapping gone")
    agent, t = _agent_with(Broken())
    agent.beat()
    b = _beat(t)
    assert b.source == "agent" and b.executor_epoch is None and b.executor_heartbeat_at is None


def test_old_reader_ignores_new_heartbeat_fields():
    """N-1：未升级的 Linux 读者（旧 Heartbeat 模型）能解析带执行器字段的新心跳。"""
    from qsys.live.messages import Message
    fields = {k: (v.annotation, v) for k, v in Heartbeat.model_fields.items() if not k.startswith("executor_")}
    from pydantic import create_model
    OldHeartbeat = create_model("OldHeartbeat", __base__=Message, **fields)
    new = Heartbeat(source="agent", at=datetime(2026, 11, 18, 10, 0), executor_epoch="E1", executor_latest_seq=3)
    old = OldHeartbeat.model_validate_json(new.model_dump_json())
    assert old.source == "agent" and not hasattr(old, "executor_epoch")
