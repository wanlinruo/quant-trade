"""回测工作台持久化与单 worker 队列。"""

from __future__ import annotations

import pytest

from qsys.backtest.workbench import BacktestQueue, WorkbenchStore

SOURCE = '''
from qsys.strategy.base import Strategy
PARAMETERS = {"window": {"type": "int", "default": 20, "min": 2}}
class Demo(Strategy):
    pass
'''


@pytest.fixture
def store(tmp_path):
    return WorkbenchStore(tmp_path / "workbench" / "jobs.sqlite3")


def test_draft_and_immutable_run_version_are_separate(store):
    strategy = store.create_strategy("demo", SOURCE)
    first = store.enqueue(strategy.id, {
        "start": "2024-01-01",
        "end": "2024-01-31",
        "cash": 100_000,
        "params": {"window": 20},
    })
    old_source = store.source_for_job(first.id)
    store.save_strategy(
        strategy.id,
        "demo",
        SOURCE.replace("pass", "def on_bar(self, ctx, bars): pass"),
    )
    assert store.source_for_job(first.id) == old_source
    assert len(store.versions(strategy.id)) == 1


def test_queue_claim_is_fifo_and_only_claims_once(store):
    strategy = store.create_strategy("demo", SOURCE)
    first = store.enqueue(strategy.id, {"start": "2024-01-01"})
    store.enqueue(strategy.id, {"start": "2024-02-01"})
    assert store.claim_next().id == first.id
    assert store.claim_next() is None
    store.finish(first.id, "succeeded")
    assert store.claim_next().config["start"] == "2024-02-01"
    assert store.claim_next() is None


def test_progress_finish_cancel_and_restart_recovery(store):
    strategy = store.create_strategy("demo", SOURCE)
    running = store.enqueue(strategy.id, {})
    store.claim_next()
    store.progress(running.id, 3, 10, "2024-01-03")
    assert store.job(running.id).percent == 30
    store.recover_interrupted()
    assert store.job(running.id).status == "interrupted"
    queued = store.enqueue(strategy.id, {})
    assert store.cancel(queued.id)
    assert store.job(queued.id).status == "cancelled"


def test_dedicated_queue_recovers_before_serving(store, tmp_path, monkeypatch):
    queue = BacktestQueue(store, tmp_path / "warehouse", tmp_path / "runs")
    events = []
    monkeypatch.setattr(store, "recover_interrupted", lambda: events.append("recover"))
    monkeypatch.setattr(queue, "_loop", lambda: events.append("loop"))

    queue.serve_forever()

    assert events == ["recover", "loop"]
