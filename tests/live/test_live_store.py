from datetime import datetime

from qsys.live.messages import AccountSnapshot, PositionSnapshot
from qsys.live.store import LiveStore
from qsys.core.trading import Order, OrderType, Side
from qsys.core.types import Symbol


def test_store_initializes_from_broker_and_recovers_state(tmp_path):
    store = LiveStore(tmp_path / "live.sqlite3")
    remote = AccountSnapshot(
        at=datetime.now(), cash=50_000.0, total_asset=60_000.0,
        positions={
            "600036.SH": PositionSnapshot(
                total_qty=1000, available_qty=1000, avg_cost=10.0,
            )
        },
    )
    store.initialize(remote)
    portfolio, state, prices, initial = store.load()
    assert portfolio.cash == 50_000.0
    assert portfolio.position(next(iter(portfolio.positions))).total_qty == 1000
    assert list(prices.values()) == [10.0]
    assert state == {}
    assert initial == 60_000.0


def test_completed_event_is_not_run_again(tmp_path):
    store = LiveStore(tmp_path / "live.sqlite3")
    now = datetime.now()
    store.initialize(AccountSnapshot(at=now, cash=100_000.0, positions={}))
    portfolio, state, prices, _ = store.load()
    assert store.begin_event("e1", now, "session_start") is True
    store.commit_event("e1", portfolio, state, prices, at=now)
    assert store.begin_event("e1", now, "session_start") is False


def test_pending_orders_are_recovered_without_resubmission(tmp_path):
    store = LiveStore(tmp_path / "live.sqlite3")
    now = datetime.now()
    store.initialize(AccountSnapshot(at=now, cash=100_000.0, positions={}))
    order = Order(
        "q1", Symbol.parse("600036.SH"), Side.BUY, 500,
        OrderType.LIMIT, 10.02, now,
    )
    store.record_orders("auction", [order])
    recovered = store.pending_orders()
    assert recovered == ((order, 500),)
    assert store.order_transitions("q1") == ("created", "risk_passed", "sent")


def test_day_start_value_survives_a_midday_restart(tmp_path):
    store = LiveStore(tmp_path / "live.sqlite3")
    now = datetime.now()
    store.initialize(AccountSnapshot(at=now, cash=100_000.0, positions={}))
    store.set_day_start_value(98_765.0, at=now)
    assert store.load()[3] == 98_765.0


def test_locally_rejected_order_never_gets_a_sent_transition(tmp_path):
    store = LiveStore(tmp_path / "live.sqlite3")
    now = datetime.now()
    store.initialize(AccountSnapshot(at=now, cash=100_000.0, positions={}))
    order = Order(
        "blocked", Symbol.parse("600036.SH"), Side.BUY, 100,
        OrderType.LIMIT, 10.0, now,
    )
    store.record_orders("auction", [order], sent_ids=frozenset())
    assert store.order_transitions("blocked") == ("created",)
