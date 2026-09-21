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


def test_equity_limit_keeps_the_newest_rows_in_chart_order(tmp_path):
    store, job = seeded(tmp_path)
    account = store.create(job.id, "模拟", 100_000, date.today())
    portfolio = Portfolio(100_000)
    for second, cash in enumerate((100_000, 100_100, 100_200), start=1):
        portfolio.cash = cash
        store.save_snapshot(
            account.id, portfolio, {}, {},
            at=datetime(2026, 9, 21, 10, 0, second),
        )

    rows = store.rows(account.id, "equity", limit=2)

    assert [row["total_value"] for row in rows] == [100_100, 100_200]
    assert [row["at"] for row in rows] == sorted(row["at"] for row in rows)
    assert store.summary(account.id)["total_value"] == 100_200


def test_lifecycle_is_validated_and_end_does_not_clear_account(tmp_path):
    store, job = seeded(tmp_path)
    account = store.create(job.id, "模拟", 100_000, date.today())
    assert store.transition(account.id, "pause").status == "paused"
    assert store.transition(account.id, "resume", activation=date.today()).status == "scheduled"
    assert store.transition(account.id, "end").status == "ended"
    with pytest.raises(ValueError):
        store.transition(account.id, "resume", activation=date.today())


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


def test_resume_uses_the_activation_the_caller_resolved_from_the_calendar(tmp_path):
    """C：生效日由调用方按交易日历算好传进来，存储层不再按星期几猜。"""
    store, job = seeded(tmp_path)
    account = store.create(job.id, "模拟", 100_000, date(2026, 9, 17))
    store.transition(account.id, "pause")
    resumed = store.transition(account.id, "resume", activation=date(2026, 10, 8))
    assert resumed.activation_date == "2026-10-08"


def test_resume_without_an_activation_is_refused(tmp_path):
    store, job = seeded(tmp_path)
    account = store.create(job.id, "模拟", 100_000, date(2026, 9, 17))
    store.transition(account.id, "pause")
    with pytest.raises(ValueError, match="生效日"):
        store.transition(account.id, "resume")


def test_strategy_runs_are_per_account(tmp_path):
    from qsys.ops.strategy_log import RunReport, build_record

    store = PaperStore(tmp_path / "workbench.sqlite3")
    day = date(2026, 9, 18)
    report = RunReport()
    report.report(candidates=12, passed=0, reasons={"估值不达标": 12})
    record = build_record(
        report,
        event_key=f"{day}:auction:09:30:00",
        session=day,
        phase="auction",
        started_at=datetime(2026, 9, 18, 9, 30),
        finished_at=datetime(2026, 9, 18, 9, 30, 1),
        cash=100_000.0,
        market_value=0.0,
        position_count=0,
        orders=0,
        rejects=0,
    )
    store.record_run("acct-a", record)

    rows = store.strategy_runs("acct-a", day)
    assert len(rows) == 1
    assert rows[0]["summary"] == "候选 12 只 → 通过 0 只，未下单"
    assert rows[0]["reasons"] == {"估值不达标": 12}
    assert store.strategy_runs("acct-b", day) == []
    assert store.run_sessions("acct-a") == [day]
