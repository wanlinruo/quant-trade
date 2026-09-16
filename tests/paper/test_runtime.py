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
