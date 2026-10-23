from datetime import date, datetime
from pathlib import Path

import pytest

from qsys.live.messages import AccountSnapshot, PositionSnapshot
from qsys.live.store import LiveStore
from qsys.core.trading import Order, OrderType, Side
from qsys.core.types import Symbol


def test_pinned_producer_keeps_sidecars_and_readers_see_new_commits(tmp_path):
    import gc
    import sqlite3

    path = tmp_path / "state.sqlite3"
    store = LiveStore(path)
    store.pin_reader_sidecars()
    anchor = store._reader_anchor
    store.pin_reader_sidecars()
    assert store._reader_anchor is anchor
    assert not anchor.in_transaction
    try:
        for n in range(3):
            assert store.begin_event(f"event-{n}", datetime.now(), "checkpoint")
            gc.collect()
            assert Path(str(path) + "-wal").is_file()
            assert Path(str(path) + "-shm").is_file()
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as reader:
                assert reader.execute("SELECT count(*) FROM live_events").fetchone()[0] == n + 1
            assert not anchor.in_transaction
    finally:
        store.close()
    assert store._reader_anchor is None
    store.close()


def test_read_only_store_cannot_pin_or_create_sidecars(tmp_path):
    path = tmp_path / "absent.sqlite3"
    reader = LiveStore(path, read_only=True)
    with pytest.raises(RuntimeError, match="only a live state producer"):
        reader.pin_reader_sidecars()
    reader.close()
    assert list(tmp_path.iterdir()) == []


def test_read_only_store_reads_committed_wal_and_rejects_writes(tmp_path):
    import sqlite3

    path = tmp_path / "live.sqlite3"
    writer = LiveStore(path)
    writer.initialize(AccountSnapshot(at=datetime.now(), cash=50_000.0, positions={}))
    connection = writer._connect()
    try:
        connection.execute("UPDATE live_state SET cash=12345 WHERE id=1")
        connection.commit()
        reader = LiveStore(path, read_only=True)
        assert reader.load()[0].cash == 12345
        with pytest.raises(sqlite3.OperationalError):
            reader.set_day_start_value(1, at=datetime.now())
        assert writer.load()[3] == 50_000.0
    finally:
        connection.close()


def test_read_only_store_does_not_create_missing_database(tmp_path):
    import sqlite3

    path = tmp_path / "absent" / "live.sqlite3"
    reader = LiveStore(path, read_only=True)
    assert not path.parent.exists()
    with pytest.raises(sqlite3.OperationalError):
        reader.load()
    assert not path.exists()


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


def test_zero_quantity_broker_rows_are_not_economic_positions(tmp_path):
    store = LiveStore(tmp_path / "live.sqlite3")
    store.initialize(AccountSnapshot(
        at=datetime.now(), cash=100_000.0, total_asset=100_000.0,
        positions={
            "511380.SH": PositionSnapshot(
                total_qty=0, available_qty=0, avg_cost=12.9, market_value=0.0,
            ),
            # An impossible negative is evidence of a broker/data problem and
            # must remain visible instead of being silently treated as empty.
            "600036.SH": PositionSnapshot(
                total_qty=-100, available_qty=0, avg_cost=10.0, market_value=0.0,
            ),
        },
    ))

    portfolio, _, _, _ = store.load()
    assert set(map(str, portfolio.positions)) == {"600036.SH"}
    assert portfolio.position(Symbol.parse("600036.SH")).total_qty == -100


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


def test_starting_value_never_falls_back_to_cost_inference(tmp_path):
    """`total_asset` 缺失时原本按 `qty × avg_cost` 兜底算起始资金——正是本次
    判定为错的那个反推（逆回购 1000×100 = 20 万，直取市值只有 10 万）。

    而且这条兜底在生产里真的会触发：`xtquant_adapter.query_account_details` 把
    缺失的 `total_asset` 转成 `0.0`，`or` 判它为假。改成只用直取市值求和。
    """
    now = datetime(2026, 10, 6, 9, 30)
    store = LiveStore(tmp_path / "live.sqlite3")

    store.initialize(AccountSnapshot(
        at=now, cash=1.31, total_asset=0.0, positions={
            "131990.SZ": PositionSnapshot(
                total_qty=1000, available_qty=0, avg_cost=100.0,
                market_value=100_000.0),
            "600036.SH": PositionSnapshot(
                total_qty=500, available_qty=500, avg_cost=10.0,
                market_value=6_000.0),
        },
    ))
    _, _, _, initial = store.load()

    assert initial == pytest.approx(106_001.31)


def test_a_position_without_a_market_value_blocks_the_starting_value(tmp_path):
    """普通持仓缺市值同样算不出起始资金。按成本充当市值是另一种编数。"""
    now = datetime(2026, 10, 6, 9, 30)
    store = LiveStore(tmp_path / "live.sqlite3")

    with pytest.raises(ValueError, match="市值"):
        store.initialize(AccountSnapshot(
            at=now, cash=1.31, total_asset=0.0, positions={
                "600036.SH": PositionSnapshot(
                    total_qty=500, available_qty=500, avg_cost=10.0),
            },
        ))
