from datetime import date, datetime

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


def test_strategy_runs_round_trip_and_rerun_overwrites(tmp_path):
    from qsys.ops.strategy_log import RunReport, build_record

    store = LiveStore(tmp_path / "state.sqlite3")
    day = date(2026, 9, 18)
    report = RunReport()
    report.report(candidates=37, passed=2, reasons={"高开超过阈值": 12})
    base = dict(
        event_key=f"{day}:auction:09:30:00",
        session=day,
        phase="auction",
        started_at=datetime(2026, 9, 18, 9, 30),
        cash=50_000.0,
        market_value=0.0,
        position_count=0,
        rejects=0,
    )
    store.record_run(
        build_record(
            report,
            finished_at=datetime(2026, 9, 18, 9, 30, 1),
            orders=2,
            **base,
        )
    )

    rows = store.strategy_runs(day)
    assert len(rows) == 1
    assert rows[0]["summary"] == "候选 37 只 → 通过 2 只 → 下单 2 笔"
    assert rows[0]["reasons"] == {"高开超过阈值": 12}
    assert rows[0]["candidates"] == 37 and rows[0]["phase"] == "auction"

    store.record_run(
        build_record(
            RunReport(),
            finished_at=datetime(2026, 9, 18, 9, 30, 2),
            orders=0,
            **base,
        )
    )
    rows = store.strategy_runs(day)
    assert len(rows) == 1 and rows[0]["orders"] == 0
    assert rows[0]["candidates"] is None
    assert store.run_sessions() == [day]
    assert store.strategy_runs(date(2026, 9, 17)) == []
