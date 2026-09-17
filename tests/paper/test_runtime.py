from datetime import date, datetime, time

from qsys.core.calendar import AShareCalendar
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


class CountingQuotes(Quotes):
    def __init__(self):
        self.calls = 0

    def request(self, symbols):
        self.calls += 1
        return super().request(symbols)


def _runtime(warehouse, tmp_path, quotes):
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


def test_realtime_limit_up_comes_from_the_warehouse_not_the_qmt_tick(warehouse, tmp_path):
    """D-19：QMT get_full_tick 不给涨停价，盘中退出据此永远判「未涨停」、封板也卖。

    盘中涨停价与回测同源：price_limits_daily（Tushare stk_limit，盘前写入）。
    """
    import pyarrow as pa
    from qsys.data import schema

    runtime, store, ident, today = _runtime(warehouse, tmp_path, CountingQuotes())
    warehouse.write("price_limits_daily", pa.table({
        "symbol": ["600036.SH"], "dt": [today], "pre_close": [None],
        "limit_up": [44.0], "limit_down": [36.0],
    }, schema=schema.PRICE_LIMITS_DAILY), year=today.year)
    bound = runtime.market.bind(datetime.combine(today, time(11, 25)))
    assert bound.limit_up_price(["600036.SH", "000001.SZ"], today) == {"600036.SH": 44.0}


def test_runtime_limits_reload_until_the_premarket_job_has_written_today(warehouse, tmp_path):
    """盘前任务晚到时，模拟撮合不能把「当日空表」缓存一整天。"""
    import pyarrow as pa
    from qsys.data import schema

    runtime, store, ident, today = _runtime(warehouse, tmp_path, CountingQuotes())
    assert runtime._limits_on(today) == {}
    warehouse.write("price_limits_daily", pa.table({
        "symbol": ["600036.SH"], "dt": [today], "pre_close": [None],
        "limit_up": [44.0], "limit_down": [36.0],
    }, schema=schema.PRICE_LIMITS_DAILY), year=today.year)
    assert runtime._limits_on(today) == {"600036.SH": (44.0, 36.0)}
