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


def _oversized(warehouse, tmp_path, name, risk):
    strategy = tmp_path / "strategy.py"
    strategy.write_text(SOURCE, encoding="utf-8")
    streams = StreamConfig()
    transport = MemoryTransport()
    now = datetime.now()
    transport.set_key(streams.account, AccountSnapshot(
        at=now, cash=100_001.0, total_asset=100_001.0, positions={}
    ).model_dump_json())
    return LiveRuntime(
        strategy, warehouse.root, AShareCalendar([date.today()]), Quotes(),
        transport, streams, LiveStore(tmp_path / name),
        local_kill_path=tmp_path / "K", risk=risk,
    )


def test_an_account_cap_is_enforced_when_one_is_configured(warehouse, tmp_path):
    import pytest

    with pytest.raises(LivePreflightError, match="超过首期上限"):
        _oversized(warehouse, tmp_path, "too-large.sqlite3",
                   RiskConfig(max_account_asset=100_000.0))


def test_no_account_cap_by_default(warehouse, tmp_path):
    """账户总资产默认不设上限：卡住敞口的是 enabled_capital，不是账户大小。

    账户会因为转入资金、创业板资产门槛、日后盈利自然长大，拿它当闸门只会误伤
    （2026-09-28 维护者为创业板权限转入 5 万，门禁立刻因超 10 万而失败）。
    """
    runtime = _oversized(warehouse, tmp_path, "uncapped.sqlite3", RiskConfig())

    assert runtime.enabled_capital == 10_000.0   # 敞口仍被启用资金卡住


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


def test_failed_event_stays_visible_when_retry_window_expires(
    warehouse, tmp_path, monkeypatch
):
    import sqlite3

    runtime, store = _runtime_with_store(warehouse, tmp_path)
    runtime.schedule.checkpoint("10:30")

    def boom(ctx, at):
        raise RuntimeError("行情响应积压")

    monkeypatch.setattr(runtime.strategy, "on_checkpoint", boom)
    day = date.today()
    with pytest.raises(RuntimeError, match="行情响应积压"):
        runtime.tick(datetime.combine(day, time(10, 30, 1)))
    runtime.tick(datetime.combine(day, time(10, 32, 2)))   # 超过 120 秒宽限

    with sqlite3.connect(store.path) as con:
        row = con.execute(
            "SELECT status,detail FROM live_events WHERE phase='checkpoint'"
        ).fetchone()
    assert row[0] == "failed" and "行情响应积压" in row[1]
    run = store.strategy_runs(day)[-1]
    assert run["error"] == "行情响应积压"


def test_an_expired_event_leaves_a_run_row(warehouse, tmp_path):
    runtime, store = _runtime_with_store(warehouse, tmp_path)
    runtime.schedule.checkpoint("10:30")
    moment = datetime.combine(date.today(), time(10, 32, 1))

    runtime.tick(moment)

    rows = store.strategy_runs(moment.date())
    assert [row["phase"] for row in rows] == ["session_start", "checkpoint"]
    assert rows[1]["note"] == "事件已过期，未补跑"


def test_a_transient_quote_timeout_is_retried_inside_the_grace_window(
    warehouse, tmp_path, monkeypatch
):
    """2026-09-21：影子核心在 09:30 请求行情超时，30 秒宽限期内没能重试，

    当天的竞价事件被判过期跳过。宽限期放宽到 120 秒后，同一事件要能重试成功。
    """
    from qsys.paper.quotes import QuoteTimeout

    runtime, store = _runtime_with_store(warehouse, tmp_path)
    runtime.schedule.checkpoint("10:30")
    calls = []

    def flaky(ctx, at):
        calls.append(at)
        if len(calls) == 1:
            raise QuoteTimeout("QMT 行情请求超时（41 个标的）")

    monkeypatch.setattr(runtime.strategy, "on_checkpoint", flaky)
    day = date.today()
    with pytest.raises(QuoteTimeout):
        runtime.tick(datetime.combine(day, time(10, 30, 6)))
    runtime.tick(datetime.combine(day, time(10, 30, 36)))

    assert len(calls) == 2
    rows = [r for r in store.strategy_runs(day) if r["phase"] == "checkpoint"]
    assert rows[-1]["error"] == ""


# ---- 对账不一致：实盘停机，影子只告警（2026-09-28）----

def _drifted(streams, cash=100_000.0):
    """券商账户里多出核心不知道的东西——维护者转账或买逆回购就会这样。"""
    from qsys.live.messages import PositionSnapshot

    return AccountSnapshot(
        at=datetime.now(), cash=cash, total_asset=cash + 5000.0,
        positions={"600036.SH": PositionSnapshot(
            total_qty=500, available_qty=500, avg_cost=10.0)},
    )


def test_a_drifted_account_halts_the_live_core(warehouse, tmp_path):
    """实盘核心是账户的唯一写入方，多出持仓就说明出事了，必须停。"""
    import pytest

    runtime, _ = _runtime_with_store(warehouse, tmp_path)

    with pytest.raises(LivePreflightError, match="INCONSISTENT"):
        runtime._assert_reconciled(_drifted(runtime.streams))

    assert runtime.kill_switch.is_engaged() is True


def test_a_drifted_account_only_warns_the_shadow_core(warehouse, tmp_path):
    """影子盘一张单都发不出去，而那个账户维护者自己还要用——停机是过度反应。

    2026-09-28 维护者转入 5 万并买了 7 天国债逆回购，影子核心当场停机，
    白丢一整天观察期。
    """
    strategy = tmp_path / "strategy.py"
    strategy.write_text(SOURCE, encoding="utf-8")
    streams = StreamConfig()
    transport = MemoryTransport()
    now = datetime.now()
    transport.set_key(streams.account, AccountSnapshot(
        at=now, cash=10_000.0, total_asset=10_000.0, positions={}
    ).model_dump_json())
    for source in ("agent", "market"):
        transport.set_key(f"{streams.heartbeat}:{source}",
                          Heartbeat(source=source, at=now).model_dump_json())
    runtime = LiveRuntime(
        strategy, warehouse.root, AShareCalendar([date.today()]), Quotes(),
        transport, streams, LiveStore(tmp_path / "shadow-drift.sqlite3"),
        local_kill_path=tmp_path / "SHADOW_KILL", shadow=True,
    )

    runtime._assert_reconciled(_drifted(streams))   # 不抛

    assert runtime.kill_switch.is_engaged() is False


def test_a_standing_difference_is_recorded_once_not_every_tick(warehouse, tmp_path):
    """2026-09-29：一个持续存在的差异写进 10,383 条对账记录（每 2 秒一条）。

    台账要的是「什么时候坏的、什么时候好的」，不是每次尝试都记一行——
    既撑爆仓库，又让 B4.arrival 永远红着；告警同理，同一句推一整天就没人看了。
    """
    from qsys.ops.alerts import AlertBus, RecordingAlertSink

    strategy = tmp_path / "strategy.py"
    strategy.write_text(SOURCE, encoding="utf-8")
    streams = StreamConfig()
    transport = MemoryTransport()
    now = datetime.now()
    transport.set_key(streams.account, AccountSnapshot(
        at=now, cash=10_000.0, total_asset=10_000.0, positions={}
    ).model_dump_json())
    for source in ("agent", "market"):
        transport.set_key(f"{streams.heartbeat}:{source}",
                          Heartbeat(source=source, at=now).model_dump_json())
    sink = RecordingAlertSink()
    runtime = LiveRuntime(
        strategy, warehouse.root, AShareCalendar([date.today()]), Quotes(),
        transport, streams, LiveStore(tmp_path / "dedup.sqlite3"),
        local_kill_path=tmp_path / "SHADOW_KILL", shadow=True,
        alerts=AlertBus([sink]),
    )

    drift = _drifted(streams)
    for _ in range(50):
        runtime._assert_reconciled(drift)

    assert len(sink.alerts) == 1


def test_the_recovery_is_recorded_too(warehouse, tmp_path):
    """只报变化，不等于坏了就沉默——好了也要说一声。"""
    from qsys.ops.alerts import AlertBus, RecordingAlertSink

    runtime, _ = _runtime_with_store(warehouse, tmp_path)
    sink = RecordingAlertSink()
    runtime.alerts = AlertBus([sink])
    runtime.shadow = True

    drift = _drifted(runtime.streams)
    runtime._assert_reconciled(drift)
    runtime._assert_reconciled(drift)          # 同样的差异，不再重复
    matching = AccountSnapshot(at=datetime.now(), cash=runtime.portfolio.cash,
                               total_asset=runtime.portfolio.cash, positions={})
    runtime._assert_reconciled(matching)       # 恢复一致

    assert len(sink.alerts) == 1               # 只有那次变坏；恢复走 journal 不告警


def test_the_live_core_keeps_refusing_on_every_attempt(warehouse, tmp_path):
    """去重只针对留痕与告警。实盘每次都必须挡住，不能第二次就放行。"""
    import pytest

    runtime, _ = _runtime_with_store(warehouse, tmp_path)
    drift = _drifted(runtime.streams)

    for _ in range(3):
        with pytest.raises(LivePreflightError, match="INCONSISTENT"):
            runtime._assert_reconciled(drift)
