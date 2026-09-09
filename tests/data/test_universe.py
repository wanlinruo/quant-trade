"""全市场批量采集：分批落盘、断点续传、单只失败不中断。"""

from datetime import date

import polars as pl

from qsys.data.pipeline.universe import completed_symbols, ingest_universe
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

BAR_SCHEMA = {
    "dt": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Int64,
    "amount": pl.Float64,
}
SESSIONS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]


class CountingProvider:
    name = "counting"

    def __init__(self, failing: set[str] | None = None, empty: set[str] | None = None):
        self.failing = failing or set()
        self.empty = empty or set()
        self.calls: list[str] = []

    def fetch_daily_bars(self, symbol, start, end):
        self.calls.append(symbol)
        if symbol in self.failing:
            raise RuntimeError("source unavailable")
        if symbol in self.empty:
            return pl.DataFrame(schema=BAR_SCHEMA)
        n = len(SESSIONS)
        return pl.DataFrame(
            {
                "dt": SESSIONS,
                "open": [10.0] * n,
                "high": [10.5] * n,
                "low": [9.5] * n,
                "close": [10.2] * n,
                "volume": [1000] * n,
                "amount": [10200.0] * n,
            },
            schema=BAR_SCHEMA,
        )


def symbols(n: int) -> list[str]:
    return [f"{600000 + i}.SH" for i in range(n)]


def test_ingests_every_symbol(warehouse):
    p = CountingProvider()
    progress = ingest_universe(p, warehouse, symbols(5), SESSIONS[0], SESSIONS[-1])
    assert progress.done == 5
    assert progress.failed == 0
    df = WarehouseQuery(warehouse).daily_bars(None, SESSIONS[0], SESSIONS[-1])
    assert df["symbol"].n_unique() == 5


def test_writes_in_batches_rather_than_all_at_once(warehouse):
    """分批落盘是内存约束下的硬要求，不是优化。"""
    written = []

    class SpyWarehouse(Warehouse):
        def write(self, dataset, table, *, year=None):
            written.append(dataset)
            return super().write(dataset, table, year=year)

    spy = SpyWarehouse(warehouse.root)
    ingest_universe(
        CountingProvider(), spy, symbols(10), SESSIONS[0], SESSIONS[-1], batch_size=3
    )
    assert written.count("bars_daily") >= 4, "10 只 / 每批 3 只应至少写 4 次"


def test_failures_do_not_abort_the_run(warehouse):
    p = CountingProvider(failing={"600002.SH"})
    progress = ingest_universe(p, warehouse, symbols(5), SESSIONS[0], SESSIONS[-1])
    assert progress.done == 4
    assert progress.failed == 1
    assert len(p.calls) == 5


def test_empty_results_count_as_done_not_failed(warehouse):
    """停牌整段或未上市返回空，不是失败。"""
    p = CountingProvider(empty={"600001.SH"})
    progress = ingest_universe(p, warehouse, symbols(3), SESSIONS[0], SESSIONS[-1])
    assert progress.failed == 0


def test_progress_callback_reports_each_batch(warehouse):
    seen = []
    ingest_universe(
        CountingProvider(),
        warehouse,
        symbols(10),
        SESSIONS[0],
        SESSIONS[-1],
        batch_size=4,
        on_progress=seen.append,
    )
    assert len(seen) >= 3
    assert seen[-1].done + seen[-1].failed == 10
    assert seen[-1].total == 10


def test_resume_skips_already_completed_symbols(warehouse):
    """进程被杀后重跑要能接着跑，不是从头再来。"""
    first = CountingProvider()
    ingest_universe(first, warehouse, symbols(5), SESSIONS[0], SESSIONS[-1])

    second = CountingProvider()
    ingest_universe(second, warehouse, symbols(8), SESSIONS[0], SESSIONS[-1])
    assert set(second.calls) == set(symbols(8)) - set(symbols(5))


def test_skip_done_false_forces_a_full_refetch(warehouse):
    first = CountingProvider()
    ingest_universe(first, warehouse, symbols(3), SESSIONS[0], SESSIONS[-1])
    second = CountingProvider()
    ingest_universe(
        second, warehouse, symbols(3), SESSIONS[0], SESSIONS[-1], skip_done=False
    )
    assert len(second.calls) == 3


def test_completed_symbols_reflects_what_was_ingested(warehouse):
    ingest_universe(CountingProvider(), warehouse, symbols(4), SESSIONS[0], SESSIONS[-1])
    assert completed_symbols(warehouse, SESSIONS[0], SESSIONS[-1]) == set(symbols(4))


def test_failed_symbols_are_retried_on_the_next_run(warehouse):
    first = CountingProvider(failing={"600001.SH"})
    ingest_universe(first, warehouse, symbols(3), SESSIONS[0], SESSIONS[-1])
    second = CountingProvider()
    ingest_universe(second, warehouse, symbols(3), SESSIONS[0], SESSIONS[-1])
    assert "600001.SH" in second.calls


def test_empty_symbol_list_is_a_no_op(warehouse):
    progress = ingest_universe(
        CountingProvider(), warehouse, [], SESSIONS[0], SESSIONS[-1]
    )
    assert progress.total == 0
