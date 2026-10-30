from datetime import date, datetime, timedelta

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
    assert rows[0]["summary"] == "候选 12 只 → 闸门通过 0 只，未下单"
    assert rows[0]["reasons"] == {"估值不达标": 12}
    assert store.strategy_runs("acct-b", day) == []
    assert store.run_sessions("acct-a") == [day]


# ---- 收益曲线：日线历史与分钟分时 ----

def _snap(store, ident, at, total, benchmark=None):
    portfolio = Portfolio(total)
    store.save_snapshot(ident, portfolio, {}, {}, at=at, benchmark_value=benchmark)


def _run_event(store, ident, at):
    key = f"{at.date()}:auction:{at:%H:%M:%S}"
    store.begin_event(ident, key, at, "auction")
    store.commit_event(ident, key, Portfolio(100_000), {}, {}, at=at)


def _chart_account(tmp_path):
    store, job = seeded(tmp_path)
    # 09-16 创建，09-21 恢复后生效日被改写：生效日不能作为历史起点
    account = store.create(job.id, "模拟", 100_000, date(2026, 9, 21))
    ident = account.id
    _snap(store, ident, datetime(2026, 9, 17, 0, 10), 100_000)        # 凌晨杂散记录，当天无事件
    _run_event(store, ident, datetime(2026, 9, 18, 9, 30))
    _snap(store, ident, datetime(2026, 9, 18, 15, 0), 100_000, 100_000)
    _snap(store, ident, datetime(2026, 9, 18, 15, 30), 100_000, 100_000)
    _run_event(store, ident, datetime(2026, 9, 21, 9, 30))
    for at, total in ((datetime(2026, 9, 21, 9, 31, 10), 99_000),
                      (datetime(2026, 9, 21, 9, 31, 50), 98_000),   # 同一分钟取最后一个
                      (datetime(2026, 9, 21, 12, 0), 98_000),        # 午休内部压缩掉
                      (datetime(2026, 9, 21, 13, 1), 101_000),
                      (datetime(2026, 9, 21, 15, 30), 100_500)):
        _snap(store, ident, at, total, 100_200)
    return store, ident


def test_chart_history_starts_at_the_first_session_that_ran_a_strategy_event(tmp_path):
    store, ident = _chart_account(tmp_path)

    daily = store.equity_daily(ident)

    assert [row["session"] for row in daily] == ["2026-09-18", "2026-09-21"]
    assert [row["total_value"] for row in daily] == [100_000, 100_500]
    assert daily[-1]["at"] == "2026-09-21T15:30:00"


def test_daily_benchmark_uses_the_last_observed_value_of_the_session(tmp_path):
    """15:30 收盘结算的快照不带基准：取当天最后一个有值的观测，而不是空值或推算。"""
    store, ident = _chart_account(tmp_path)
    _run_event(store, ident, datetime(2026, 9, 21, 15, 31))   # commit_event 写入无基准快照

    daily = store.equity_daily(ident)

    assert daily[-1]["benchmark_value"] is None
    assert daily[-1]["benchmark_close"] == 100_200
    assert daily[0]["benchmark_close"] == 100_000


def test_chart_history_falls_back_to_activation_before_any_event(tmp_path):
    store, job = seeded(tmp_path)
    ident = store.create(job.id, "模拟", 100_000, date(2026, 9, 21)).id
    _snap(store, ident, datetime(2026, 9, 18, 15, 0), 100_000)
    _snap(store, ident, datetime(2026, 9, 21, 9, 20), 100_000)

    assert [row["session"] for row in store.equity_daily(ident)] == ["2026-09-21"]


def test_chart_intraday_keeps_the_last_point_per_minute_and_skips_lunch(tmp_path):
    store, ident = _chart_account(tmp_path)

    points = store.equity_intraday(ident, date(2026, 9, 21))

    minutes = [row["at"][11:16] for row in points]
    assert "12:00" not in minutes
    assert minutes == ["09:30", "09:31", "13:01", "15:30"]
    assert points[1]["total_value"] == 98_000


def test_summary_drawdown_uses_the_full_history(tmp_path):
    store, job = seeded(tmp_path)
    ident = store.create(job.id, "模拟", 100_000, date(2026, 9, 21)).id
    _run_event(store, ident, datetime(2026, 9, 21, 9, 30))
    _snap(store, ident, datetime(2026, 9, 21, 9, 31), 90_000)   # 早盘最深回撤 10%
    for second in range(12_000):                                  # 超过旧的 10,000 条截断
        _snap(store, ident, datetime(2026, 9, 21, 10, 0) + timedelta(seconds=second), 95_000)

    summary = store.summary(ident)

    assert summary["max_drawdown_pct"] == pytest.approx(10.0)
    assert summary["total_value"] == 95_000


def test_chart_queries_stay_fast_on_a_full_session_of_seconds(tmp_path):
    """写成 JOIN 时 SQLite 会逐行重算子查询：生产 3.8 万点跑了 5 分钟仍未返回。"""
    import sqlite3
    import time as clock

    store, job = seeded(tmp_path)
    ident = store.create(job.id, "模拟", 100_000, date(2026, 9, 21)).id
    _run_event(store, ident, datetime(2026, 9, 18, 9, 30))
    start = datetime(2026, 9, 18, 9, 15)
    rows = [(ident, (start + timedelta(seconds=i)).isoformat(),
             str((start + timedelta(seconds=i)).date()), 1.0, 0.0, 100_000.0, None)
            for i in range(30_000)]
    with sqlite3.connect(store.path) as con:
        con.executemany("INSERT OR REPLACE INTO paper_equity VALUES (?,?,?,?,?,?,?)", rows)

    began = clock.monotonic()
    store.equity_intraday(ident, date(2026, 9, 18))
    store.equity_daily(ident)
    store.summary(ident)

    assert clock.monotonic() - began < 5


# ---- 当日盈亏要有昨天做基准（2026-10-01 收盘消息加盈亏）----

def test_previous_total_value_skips_today_and_takes_yesterdays_close(tmp_path):
    store, job = seeded(tmp_path)
    account = store.create(job.id, "模拟一号", 100_000, date(2026, 9, 17))
    for day, at, cash in ((date(2026, 9, 28), 9, 100_000.0),
                          (date(2026, 9, 28), 15, 101_000.0),
                          (date(2026, 9, 29), 15, 102_000.0)):
        store.save_snapshot(account.id, Portfolio(cash), {}, {},
                            at=datetime(day.year, day.month, day.day, at, 0))

    assert store.previous_total_value(account.id, date(2026, 9, 29)) == 101_000.0
    assert store.previous_total_value(account.id, date(2026, 9, 30)) == 102_000.0


def test_the_first_day_has_no_yesterday(tmp_path):
    """没有昨天时不显示盈亏，比拿初始资金凑一个数诚实。"""
    store, job = seeded(tmp_path)
    account = store.create(job.id, "模拟一号", 100_000, date(2026, 9, 17))

    assert store.previous_total_value(account.id, date(2026, 9, 28)) is None


def test_connections_close_without_gc_and_keep_transactions_atomic(tmp_path):
    import gc
    import sqlite3

    store = PaperStore(tmp_path / 'lifetime.sqlite3')
    enabled = gc.isenabled()
    gc.disable()
    try:
        connections = []
        for _ in range(100):
            with store._connect() as con:
                assert con.execute('SELECT count(*) FROM paper_accounts').fetchone()[0] == 0
            connections.append(con)  # Even a retained Python object must be closed.
        for con in connections:
            with pytest.raises(sqlite3.ProgrammingError, match='closed'):
                con.execute('SELECT 1')
        with store._connect() as con:
            con.execute('CREATE TABLE lifetime_test(value TEXT)')
            con.execute("INSERT INTO lifetime_test VALUES('committed')")
        with pytest.raises(RuntimeError, match='abort'):
            with store._connect() as con:
                con.execute("INSERT INTO lifetime_test VALUES('rolled back')")
                raise RuntimeError('abort')
        with store._connect() as con:
            assert con.execute('SELECT value FROM lifetime_test').fetchall()[0][0] == 'committed'
            assert con.execute('SELECT count(*) FROM lifetime_test').fetchone()[0] == 1
    finally:
        store.close()
        if enabled:
            gc.enable()


def test_reader_anchor_survives_gc_sees_new_commits_and_closes(tmp_path):
    import gc
    import sqlite3
    from contextlib import closing

    path = tmp_path / 'sidecars.sqlite3'
    store = PaperStore(path)
    anchor = store._reader_anchor
    assert anchor is not None and not anchor.in_transaction
    gc.collect()
    for _ in range(2):
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as reader:
            assert reader.execute('SELECT count(*) FROM paper_accounts').fetchone()[0] == 0
        with store._connect() as con:
            con.execute('CREATE TABLE IF NOT EXISTS anchor_test(value INTEGER)')
            con.execute('INSERT INTO anchor_test VALUES(1)')
        gc.collect()
        assert path.with_name(path.name + '-wal').exists()
        assert path.with_name(path.name + '-shm').exists()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as reader:
        assert reader.execute('SELECT count(*) FROM anchor_test').fetchone()[0] == 2
    store.close()
    store.close()
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        anchor.execute('SELECT 1')


def test_setup_failure_closes_connection_and_store_finalizer_closes_anchor(tmp_path, monkeypatch):
    import gc
    import sqlite3
    import weakref

    store = PaperStore(tmp_path / 'failure.sqlite3')
    anchor = store._reader_anchor
    real_connect = sqlite3.connect
    opened = []

    class SetupFailure(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == 'PRAGMA foreign_keys=ON':
                raise sqlite3.OperationalError('setup failed')
            return super().execute(sql, *args, **kwargs)

    def connect(*args, **kwargs):
        con = real_connect(*args, **kwargs, factory=SetupFailure)
        opened.append(con)
        return con

    monkeypatch.setattr(sqlite3, 'connect', connect)
    with pytest.raises(sqlite3.OperationalError, match='setup failed'):
        store.accounts()
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        opened[0].execute('SELECT 1')
    ref = weakref.ref(store)
    del store
    gc.collect()
    assert ref() is None
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        anchor.execute('SELECT 1')
