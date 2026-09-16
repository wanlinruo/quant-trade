from datetime import date, datetime

import pytest

from qsys.core.portfolio import Portfolio
from qsys.core.trading import Order, OrderType, Side
from qsys.core.types import Symbol
from qsys.paper.store import PaperStore
from qsys.web.workbench import WorkbenchStore


SOURCE = """
from qsys.strategy.base import Strategy
class Demo(Strategy):
    pass
"""


def seeded(tmp_path):
    path = tmp_path / "workbench.sqlite3"
    workbench = WorkbenchStore(path)
    strategy = workbench.create_strategy("demo", SOURCE)
    job = workbench.enqueue(strategy.id, {
        "start": "2026-01-01", "end": "2026-02-01", "cash": 100_000,
        "params": {}, "jq_compat": False,
    })
    workbench.claim_next()
    workbench.finish(job.id, "succeeded", run_name=job.id)
    return PaperStore(path), job


def test_create_requires_successful_immutable_backtest(tmp_path):
    store, job = seeded(tmp_path)
    account = store.create(job.id, "模拟一号", 200_000, date(2026, 9, 17))
    assert account.status == "scheduled"
    assert account.version_id == job.version_id
    assert store.source(account.id) == SOURCE


def test_state_and_portfolio_survive_reopen(tmp_path):
    store, job = seeded(tmp_path)
    account = store.create(job.id, "模拟", 100_000, date.today())
    portfolio = Portfolio(100_000)
    symbol = Symbol.parse("600036.SH")
    state = {"day": date(2026, 9, 16), "symbols": [symbol]}
    store.save_snapshot(account.id, portfolio, state, {symbol: 40.0},
                        at=datetime(2026, 9, 16, 15, 0))
    restored = PaperStore(store.path).account(account.id)
    assert restored.state["day"] == date(2026, 9, 16)
    assert restored.state["symbols"] == [symbol]


def test_lifecycle_is_validated_and_end_does_not_clear_account(tmp_path):
    store, job = seeded(tmp_path)
    account = store.create(job.id, "模拟", 100_000, date.today())
    assert store.transition(account.id, "pause").status == "paused"
    assert store.transition(account.id, "resume").status == "scheduled"
    assert store.transition(account.id, "end").status == "ended"
    with pytest.raises(ValueError):
        store.transition(account.id, "resume")


def test_pause_cancels_pending_virtual_orders(tmp_path):
    store, job = seeded(tmp_path)
    account = store.create(job.id, "模拟", 100_000, date.today())
    symbol = Symbol.parse("600036.SH")
    order = Order("o1", symbol, Side.BUY, 100, OrderType.MARKET, None,
                  datetime(2026, 9, 16, 10, 0))
    store.record_orders(account.id, "event-1", [order])
    store.transition(account.id, "pause")
    assert store.rows(account.id, "orders")[0]["status"] == "cancelled"
    assert store.rows(account.id, "rejects")[0]["reason"] == "paper account paused"
