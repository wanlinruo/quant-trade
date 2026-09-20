from datetime import date, datetime, time

import pytest

from qsys.core.calendar import AShareCalendar
from qsys.data.cutover import FacadeMode
from qsys.live.config import StreamConfig
from qsys.live.config import RiskConfig
from qsys.live.messages import AccountSnapshot, Heartbeat, MarketQuote
from qsys.live.runtime import LivePreflightError, LiveRuntime
from qsys.live.shadow_broker import ShadowBroker
from qsys.live.store import LiveStore
from qsys.live.transport import MemoryTransport


SOURCE = """
from qsys.strategy.base import Strategy
class Demo(Strategy):
    def on_session_start(self, ctx):
        ctx.state['starts'] = ctx.state.get('starts', 0) + 1
"""


class Quotes:
    def request(self, symbols):
        now = datetime.now()
        return {s: MarketQuote(symbol=s, at=now, price=10, open=10, high=10,
                               low=10, prev_close=10, bid1=9.99, ask1=10.01)
                for s in symbols}


def _runtime_with_store(warehouse, tmp_path):
    strategy = tmp_path / "strategy.py"
    strategy.write_text(SOURCE, encoding="utf-8")
    streams = StreamConfig()
    transport = MemoryTransport()
    now = datetime.now()
    transport.set_key(
        streams.account,
        AccountSnapshot(
            at=now, cash=100_000.0, total_asset=100_000.0, positions={}
        ).model_dump_json(),
    )
    for source in ("agent", "market"):
        transport.set_key(
            f"{streams.heartbeat}:{source}",
            Heartbeat(source=source, at=now).model_dump_json(),
        )
    store = LiveStore(tmp_path / "live.sqlite3")
    runtime = LiveRuntime(
        strategy,
        warehouse.root,
        AShareCalendar([date.today()]),
        Quotes(),
        transport,
        streams,
        store,
        local_kill_path=tmp_path / "K",
    )
    return runtime, store


def test_live_runtime_executes_event_once_and_recovers(warehouse, tmp_path):
    strategy = tmp_path / "strategy.py"
    strategy.write_text(SOURCE, encoding="utf-8")
    streams = StreamConfig()
    transport = MemoryTransport()
    now = datetime.now()
    transport.set_key(streams.account, AccountSnapshot(
        at=now, cash=100_000.0, total_asset=100_000.0, positions={}
    ).model_dump_json())
    for source in ("agent", "market"):
        transport.set_key(
            f"{streams.heartbeat}:{source}",
            Heartbeat(source=source, at=now).model_dump_json(),
        )
    runtime = LiveRuntime(
        strategy, warehouse.root, AShareCalendar([date.today()]), Quotes(),
        transport, streams, LiveStore(tmp_path / "live.sqlite3"),
        local_kill_path=tmp_path / "K",
    )
    moment = datetime.combine(date.today(), time(9, 15, 10))
    runtime.tick(moment)
    runtime.tick(moment)
    _, state, _, _ = runtime.store.load()
    assert state["starts"] == 1
    assert runtime.journal.sessions() == [date.today()]


def test_live_runtime_honors_explicit_canonical_facade(
    warehouse, tmp_path, monkeypatch
):
    monkeypatch.setenv("QSYS_MARKET_DATA_FACADE", "canonical")
    runtime, _ = _runtime_with_store(warehouse, tmp_path)
    assert runtime.feed._market_data.config.mode is FacadeMode.CANONICAL


def test_first_stage_rejects_an_oversized_account(warehouse, tmp_path):
    import pytest

    strategy = tmp_path / "strategy.py"
    strategy.write_text(SOURCE, encoding="utf-8")
    streams = StreamConfig()
    transport = MemoryTransport()
    now = datetime.now()
    transport.set_key(streams.account, AccountSnapshot(
        at=now, cash=100_001.0, total_asset=100_001.0, positions={}
    ).model_dump_json())
    with pytest.raises(LivePreflightError, match="超过首期上限"):
        LiveRuntime(
            strategy, warehouse.root, AShareCalendar([date.today()]), Quotes(),
            transport, streams, LiveStore(tmp_path / "too-large.sqlite3"),
            local_kill_path=tmp_path / "K", risk=RiskConfig(),
        )


def test_shadow_runtime_uses_separate_broker_and_kill_key(warehouse, tmp_path):
    strategy = tmp_path / "strategy.py"
    strategy.write_text(SOURCE, encoding="utf-8")
    streams = StreamConfig()
    transport = MemoryTransport()
    now = datetime.now()
    transport.set_key(streams.account, AccountSnapshot(
        at=now, cash=10_000.0, total_asset=10_000.0, positions={}
    ).model_dump_json())

    runtime = LiveRuntime(
        strategy, warehouse.root, AShareCalendar([date.today()]), Quotes(),
        transport, streams, LiveStore(tmp_path / "shadow.sqlite3"),
        local_kill_path=tmp_path / "SHADOW_KILL", shadow=True,
    )

    assert isinstance(runtime.broker, ShadowBroker)
    runtime.kill_switch.engage("test")
    assert transport.get_key("shadow:control:kill") is not None
    assert transport.get_key("control:kill") is None
    assert transport.all_messages(streams.orders) == []


def test_every_event_leaves_a_run_row_even_with_no_orders(warehouse, tmp_path):
    runtime, store = _runtime_with_store(warehouse, tmp_path)
    moment = datetime.combine(date.today(), time(9, 15, 10))

    runtime.tick(moment)

    rows = store.strategy_runs(moment.date())
    assert [row["phase"] for row in rows] == ["session_start"]
    assert rows[0]["orders"] == 0 and rows[0]["summary"]
    assert rows[0]["duration_ms"] >= 0


def test_a_failing_recorder_never_breaks_the_event(
    warehouse, tmp_path, monkeypatch
):
    runtime, store = _runtime_with_store(warehouse, tmp_path)

    def boom(record):
        raise RuntimeError("磁盘满了")

    monkeypatch.setattr(store, "record_run", boom)
    moment = datetime.combine(date.today(), time(9, 15, 10))

    runtime.tick(moment)

    _, state, _, _ = store.load()
    assert state["starts"] == 1
    assert store.strategy_runs(moment.date()) == []


def test_a_failing_strategy_still_leaves_an_error_row(
    warehouse, tmp_path, monkeypatch
):
    runtime, store = _runtime_with_store(warehouse, tmp_path)

    def boom(ctx):
        raise RuntimeError("策略坏了")

    monkeypatch.setattr(runtime.strategy, "on_session_start", boom)
    moment = datetime.combine(date.today(), time(9, 15, 10))

    with pytest.raises(RuntimeError, match="策略坏了"):
        runtime.tick(moment)

    rows = store.strategy_runs(moment.date())
    assert len(rows) == 1
    assert rows[0]["error"] == "策略坏了"
    assert rows[0]["summary"] == "事件失败：策略坏了"


def test_an_expired_event_leaves_a_run_row(warehouse, tmp_path):
    runtime, store = _runtime_with_store(warehouse, tmp_path)
    runtime.schedule.checkpoint("10:30")
    moment = datetime.combine(date.today(), time(10, 31))

    runtime.tick(moment)

    rows = store.strategy_runs(moment.date())
    assert [row["phase"] for row in rows] == ["session_start", "checkpoint"]
    assert rows[1]["note"] == "事件已过期，未补跑"
