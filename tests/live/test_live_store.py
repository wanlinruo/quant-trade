from datetime import date, datetime

import pytest

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
    assert rows[0]["summary"] == "候选 37 只 → 闸门通过 2 只 → 下单 2 笔"
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


# ---- 停在逆回购里的钱是现金，不是持仓（#134，2026-10-06）----

def test_money_parked_in_reverse_repo_starts_as_cash_not_as_a_position(tmp_path):
    """维护者把闲置资金买成逆回购，账户快照里现金只剩 1.31 元。

    照搬快照会让核心以为自己只有 1.31 元可用，而那 10 万明明是随时可取的现金
    等价物；逆回购也会被当成策略持仓带进本地账本，对账两侧口径就此分叉。
    """
    now = datetime(2026, 10, 6, 9, 30)
    store = LiveStore(tmp_path / "live.sqlite3")

    store.initialize(AccountSnapshot(
        at=now, cash=1.31, total_asset=100_001.31, positions={
            "131990.SZ": PositionSnapshot(
                total_qty=1000, available_qty=0, avg_cost=100.0,
                market_value=100_000.0),
        },
    ))
    portfolio, _, _, initial = store.load()

    assert portfolio.cash == pytest.approx(100_001.31)
    assert portfolio.positions == {}
    assert initial == pytest.approx(100_001.31)


def test_a_repo_without_a_market_value_refuses_to_set_a_starting_balance(tmp_path):
    """市值取不到就不知道起始资金是多少。按 1.31 元开张等于把 10 万当没有，
    按成本反推又是编数——此时必须明确失败，让人先去修代理上报。"""
    now = datetime(2026, 10, 6, 9, 30)
    store = LiveStore(tmp_path / "live.sqlite3")

    with pytest.raises(ValueError, match="市值"):
        store.initialize(AccountSnapshot(
            at=now, cash=1.31, total_asset=100_001.31, positions={
                "131990.SZ": PositionSnapshot(
                    total_qty=1000, available_qty=0, avg_cost=100.0),
            },
        ))
