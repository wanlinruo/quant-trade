from datetime import date, datetime, time

import pytest

from qsys.core.calendar import AShareCalendar
from qsys.data.cutover import FacadeMode
from qsys.live.messages import MarketQuote
from qsys.paper.runtime import PaperRuntime
from qsys.paper.store import PaperStore
from qsys.web.workbench import WorkbenchStore


SOURCE = """
from qsys.strategy.base import Strategy
class Demo(Strategy):
    def on_session_start(self, ctx):
        ctx.state["starts"] = ctx.state.get("starts", 0) + 1
"""


class Quotes:
    def request(self, symbols):
        now = datetime.now()
        return {s: MarketQuote(symbol=s, at=now, price=4000, open=3990,
                               high=4010, low=3980, prev_close=3980,
                               volume=10_000) for s in symbols}


def test_runtime_executes_each_scheduled_event_once_and_recovers_state(warehouse, tmp_path):
    db = tmp_path / "workbench.sqlite3"
    workbench = WorkbenchStore(db)
    strategy = workbench.create_strategy("demo", SOURCE)
    job = workbench.enqueue(strategy.id, {
        "start": "2026-01-01", "end": "2026-02-01", "cash": 100_000,
        "params": {}, "jq_compat": False,
    })
    workbench.claim_next()
    workbench.finish(job.id, "succeeded", run_name=job.id)
    store = PaperStore(db)
    today = date.today()
    account = store.create(job.id, "paper", 100_000, today)
    runtime = PaperRuntime(account.id, store, warehouse.root,
                           AShareCalendar([today]), Quotes(), tmp_path)
    moment = datetime.combine(today, time(9, 15, 10))
    runtime.tick(moment)
    runtime.tick(moment)
    restored = store.account(account.id)
    assert restored.status == "running"
    assert restored.state["starts"] == 1
    events = store.rows(account.id, "events")
    assert len(events) == 1
    assert events[0]["status"] == "completed"


def test_paper_runtime_honors_explicit_canonical_facade(
    warehouse, tmp_path, monkeypatch
):
    import pyarrow as pa
    from qsys.data import schema

    monkeypatch.setenv("QSYS_MARKET_DATA_FACADE", "canonical")
    runtime, _, _, today = _runtime(warehouse, tmp_path, Quotes())
    assert runtime.feed._market_data.config.mode is FacadeMode.CANONICAL
    warehouse.write("price_limit_daily", pa.table({
        "symbol": ["600036.SH"], "dt": [today], "pre_close": [40.0],
        "limit_up": [44.0], "limit_down": [36.0],
    }, schema=schema.PRICE_LIMIT_DAILY), year=today.year)
    bound = runtime.market.bind(datetime.combine(today, time(11, 25)))
    assert bound.limit_up_price(["600036.SH"], today) == {"600036.SH": 44.0}
    assert runtime._limits_on(today) == {"600036.SH": (44.0, 36.0)}


class CountingQuotes(Quotes):
    def __init__(self):
        self.calls = 0

    def request(self, symbols):
        self.calls += 1
        return super().request(symbols)


def _runtime(warehouse, tmp_path, quotes, source=SOURCE):
    db = tmp_path / "workbench.sqlite3"
    workbench = WorkbenchStore(db)
    strategy = workbench.create_strategy("demo", source)
    job = workbench.enqueue(strategy.id, {
        "start": "2026-01-01", "end": "2026-02-01", "cash": 100_000,
        "params": {}, "jq_compat": False,
    })
    workbench.claim_next()
    workbench.finish(job.id, "succeeded", run_name=job.id)
    store = PaperStore(db)
    today = date.today()
    account = store.create(job.id, "paper", 100_000, today)
    runtime = PaperRuntime(account.id, store, warehouse.root,
                           AShareCalendar([today]), quotes, tmp_path)
    return runtime, store, account.id, today


def test_no_quotes_or_equity_rows_outside_the_session_window(warehouse, tmp_path):
    """B：2026-09-17 00:00 ~ 00:49 每秒请求一次行情、写一条权益，共 2,704 条，
    最后一次超时把账户打成 error。盘外不该碰 QMT。"""
    quotes = CountingQuotes()
    runtime, store, ident, today = _runtime(warehouse, tmp_path, quotes)
    for moment in (time(0, 49, 53), time(9, 14, 59)):
        runtime.tick(datetime.combine(today, moment))
    assert quotes.calls == 0
    assert store.rows(ident, "equity") == []

    # 收盘结算事件之后：事件本身的提交会记账，但此后的空转 tick 不再碰行情
    runtime.tick(datetime.combine(today, time(15, 30, 30)))
    calls, rows = quotes.calls, len(store.rows(ident, "equity"))
    for moment in (time(15, 31), time(20, 0), time(23, 59, 59)):
        runtime.tick(datetime.combine(today, moment))
    assert quotes.calls == calls
    assert len(store.rows(ident, "equity")) == rows


def test_quotes_refresh_inside_the_session_window(warehouse, tmp_path):
    quotes = CountingQuotes()
    runtime, store, ident, today = _runtime(warehouse, tmp_path, quotes)
    runtime.tick(datetime.combine(today, time(9, 31)))
    assert quotes.calls >= 1
    assert len(store.rows(ident, "equity")) >= 1


def test_realtime_limit_up_comes_from_the_warehouse_not_the_qmt_tick(
    warehouse, tmp_path, monkeypatch
):
    """D-19：QMT get_full_tick 不给涨停价，盘中退出据此永远判「未涨停」、封板也卖。

    盘中涨停价与回测同源：price_limits_daily（Tushare stk_limit，盘前写入）。
    """
    import pyarrow as pa
    from qsys.data import schema

    monkeypatch.setenv("QSYS_MARKET_DATA_FACADE", "legacy")
    runtime, store, ident, today = _runtime(warehouse, tmp_path, CountingQuotes())
    warehouse.write("price_limits_daily", pa.table({
        "symbol": ["600036.SH"], "dt": [today], "pre_close": [None],
        "limit_up": [44.0], "limit_down": [36.0],
    }, schema=schema.PRICE_LIMITS_DAILY), year=today.year)
    bound = runtime.market.bind(datetime.combine(today, time(11, 25)))
    assert bound.limit_up_price(["600036.SH", "000001.SZ"], today) == {"600036.SH": 44.0}


def test_realtime_suspension_truth_overrides_a_stale_qmt_price(
    warehouse, tmp_path, monkeypatch,
):
    """停牌股的 QMT tick 可能保留前收盘价并报 suspended=False。"""
    import pyarrow as pa
    from qsys.data import schema

    monkeypatch.setenv("QSYS_MARKET_DATA_FACADE", "legacy")
    runtime, _, _, today = _runtime(warehouse, tmp_path, CountingQuotes())
    warehouse.write("suspend_daily", pa.table({
        "symbol": ["600036.SH"], "dt": [today],
        "suspend_type": ["S"], "suspend_timing": [None],
    }, schema=schema.SUSPEND_DAILY), year=today.year)

    bound = runtime.market.bind(datetime.combine(today, time(9, 25, 10)))

    assert bound.suspended_symbols(["600036.SH"], today) == {"600036.SH"}


def test_runtime_limits_reload_until_the_premarket_job_has_written_today(
    warehouse, tmp_path, monkeypatch
):
    """盘前任务晚到时，模拟撮合不能把「当日空表」缓存一整天。"""
    import pyarrow as pa
    from qsys.data import schema

    monkeypatch.setenv("QSYS_MARKET_DATA_FACADE", "legacy")
    runtime, store, ident, today = _runtime(warehouse, tmp_path, CountingQuotes())
    assert runtime._limits_on(today) == {}
    warehouse.write("price_limits_daily", pa.table({
        "symbol": ["600036.SH"], "dt": [today], "pre_close": [None],
        "limit_up": [44.0], "limit_down": [36.0],
    }, schema=schema.PRICE_LIMITS_DAILY), year=today.year)
    assert runtime._limits_on(today) == {"600036.SH": (44.0, 36.0)}


def test_paper_events_leave_run_rows(warehouse, tmp_path):
    runtime, store, ident, today = _runtime(warehouse, tmp_path, Quotes())
    moment = datetime.combine(today, time(9, 15, 10))

    runtime.tick(moment)

    rows = store.strategy_runs(ident, today)
    assert [row["phase"] for row in rows] == ["session_start"]
    assert rows[0]["summary"]


def test_paper_recorder_failure_does_not_break_the_event(
    warehouse, tmp_path, monkeypatch
):
    runtime, store, ident, today = _runtime(warehouse, tmp_path, Quotes())

    def boom(*args, **kwargs):
        raise RuntimeError("坏了")

    monkeypatch.setattr(store, "record_run", boom)
    moment = datetime.combine(today, time(9, 15, 10))

    runtime.tick(moment)

    events = store.rows(ident, "events")
    assert events and events[0]["status"] == "completed"
    assert store.strategy_runs(ident, today) == []


def test_paper_strategy_error_leaves_a_run_row(warehouse, tmp_path, monkeypatch):
    runtime, store, ident, today = _runtime(warehouse, tmp_path, Quotes())

    def boom(ctx):
        raise RuntimeError("策略坏了")

    monkeypatch.setattr(runtime.strategy, "on_session_start", boom)
    moment = datetime.combine(today, time(9, 15, 10))

    with pytest.raises(RuntimeError, match="策略坏了"):
        runtime.tick(moment)

    rows = store.strategy_runs(ident, today)
    assert len(rows) == 1
    assert rows[0]["summary"] == "事件失败：策略坏了"


def test_paper_failed_event_is_not_hidden_as_success_after_grace(
    warehouse, tmp_path, monkeypatch
):
    runtime, store, ident, today = _runtime(warehouse, tmp_path, Quotes())
    runtime.schedule.checkpoint("10:30")

    def boom(ctx, at):
        raise RuntimeError("行情响应积压")

    monkeypatch.setattr(runtime.strategy, "on_checkpoint", boom)
    with pytest.raises(RuntimeError, match="行情响应积压"):
        runtime.tick(datetime.combine(today, time(10, 30, 1)))
    runtime.tick(datetime.combine(today, time(10, 31, 31)))

    event = next(row for row in store.rows(ident, "events") if row["phase"] == "checkpoint")
    assert event["status"] == "failed" and "行情响应积压" in event["detail"]
    run = store.strategy_runs(ident, today)[-1]
    assert run["error"] == "行情响应积压"


def test_paper_expired_event_leaves_a_run_row(warehouse, tmp_path):
    runtime, store, ident, today = _runtime(warehouse, tmp_path, Quotes())
    runtime.schedule.checkpoint("10:30")

    runtime.tick(datetime.combine(today, time(10, 32)))

    rows = store.strategy_runs(ident, today)
    assert [row["phase"] for row in rows] == ["session_start", "checkpoint"]
    assert rows[1]["note"] == "进程恢复时事件已过期，未补跑"


# ---- 通知要带标的（2026-10-01：以前只说「下单 2 笔」）----

BUYER = """
from qsys.core.trading import OrderType, Side
from qsys.core.types import Symbol
from qsys.strategy.base import Strategy
class Demo(Strategy):
    def on_session_start(self, ctx):
        ctx.order.submit(Symbol.parse("600036.SH"), Side.BUY, 300,
                         OrderType.LIMIT, 41.07)
"""


def test_a_paper_order_is_announced_with_the_symbol_and_price(warehouse, tmp_path):
    """手机上收到「下单 1 笔」是没法判断要不要干预的。"""
    from qsys.ops.alerts import AlertBus, RecordingAlertSink

    runtime, store, ident, today = _runtime(warehouse, tmp_path, Quotes(), source=BUYER)
    sink = RecordingAlertSink()
    runtime.notifier._alerts = AlertBus([sink])

    runtime.tick(datetime.combine(today, time(9, 15, 10)))

    sent = [a for a in sink.alerts if "买入" in a.title or "下单" in a.title]
    assert sent, [a.title for a in sink.alerts]
    assert "600036.SH" in sent[0].detail


def test_a_context_failure_never_stops_the_notification(warehouse, tmp_path, monkeypatch):
    """通知细节取不到就发简版，不能连带把消息整条吃掉。"""
    from qsys.ops.alerts import AlertBus, RecordingAlertSink

    runtime, store, ident, today = _runtime(warehouse, tmp_path, Quotes())
    sink = RecordingAlertSink()
    runtime.notifier._alerts = AlertBus([sink])
    monkeypatch.setattr(runtime.store, "previous_total_value",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("坏了")))

    runtime.tick(datetime.combine(today, time(15, 5)))

    assert store.strategy_runs(ident, today)
