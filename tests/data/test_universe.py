"""全市场批量采集：分批落盘、断点续传、单只失败不中断。"""

import time
from datetime import date

import polars as pl

from qsys.data.pipeline.universe import (
    completed_symbols,
    ingest_universe,
    source_is_serving,
)
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
    progress = ingest_universe(
        p, warehouse, symbols(5), SESSIONS[0], SESSIONS[-1],
        complete_through=SESSIONS[-1],
    )
    assert progress.done == 5
    assert progress.failed == 0
    df = WarehouseQuery(warehouse).legacy_daily_bars(None, SESSIONS[0], SESSIONS[-1])
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
        CountingProvider(), spy, symbols(10), SESSIONS[0], SESSIONS[-1],
        complete_through=SESSIONS[-1], batch_size=3,
    )
    assert written.count("bars_daily") >= 4, "10 只 / 每批 3 只应至少写 4 次"


def test_failures_do_not_abort_the_run(warehouse):
    p = CountingProvider(failing={"600002.SH"})
    progress = ingest_universe(
        p, warehouse, symbols(5), SESSIONS[0], SESSIONS[-1],
        complete_through=SESSIONS[-1],
    )
    assert progress.done == 4
    assert progress.failed == 1
    assert len(p.calls) == 5


def test_empty_results_count_as_done_not_failed(warehouse):
    """停牌整段或未上市返回空，不是失败。"""
    p = CountingProvider(empty={"600001.SH"})
    progress = ingest_universe(
        p, warehouse, symbols(3), SESSIONS[0], SESSIONS[-1],
        complete_through=SESSIONS[-1],
    )
    assert progress.failed == 0


def test_progress_callback_reports_each_batch(warehouse):
    seen = []
    ingest_universe(
        CountingProvider(),
        warehouse,
        symbols(10),
        SESSIONS[0],
        SESSIONS[-1],
        complete_through=SESSIONS[-1],
        batch_size=4,
        on_progress=seen.append,
    )
    assert len(seen) >= 3
    assert seen[-1].done + seen[-1].failed == 10
    assert seen[-1].total == 10


def test_resume_skips_already_completed_symbols(warehouse):
    """进程被杀后重跑要能接着跑，不是从头再来。"""
    first = CountingProvider()
    ingest_universe(
        first, warehouse, symbols(5), SESSIONS[0], SESSIONS[-1],
        complete_through=SESSIONS[-1],
    )

    second = CountingProvider()
    ingest_universe(
        second, warehouse, symbols(8), SESSIONS[0], SESSIONS[-1],
        complete_through=SESSIONS[-1],
    )
    assert set(second.calls) == set(symbols(8)) - set(symbols(5))


def test_skip_done_false_forces_a_full_refetch(warehouse):
    first = CountingProvider()
    ingest_universe(
        first, warehouse, symbols(3), SESSIONS[0], SESSIONS[-1],
        complete_through=SESSIONS[-1],
    )
    second = CountingProvider()
    ingest_universe(
        second, warehouse, symbols(3), SESSIONS[0], SESSIONS[-1],
        complete_through=SESSIONS[-1], skip_done=False,
    )
    assert len(second.calls) == 3


def test_completed_symbols_reflects_what_was_ingested(warehouse):
    ingest_universe(
        CountingProvider(), warehouse, symbols(4), SESSIONS[0], SESSIONS[-1],
        complete_through=SESSIONS[-1],
    )
    assert completed_symbols(warehouse, SESSIONS[0], SESSIONS[-1]) == set(symbols(4))


def test_failed_symbols_are_retried_on_the_next_run(warehouse):
    first = CountingProvider(failing={"600001.SH"})
    ingest_universe(
        first, warehouse, symbols(3), SESSIONS[0], SESSIONS[-1],
        complete_through=SESSIONS[-1],
    )
    second = CountingProvider()
    ingest_universe(
        second, warehouse, symbols(3), SESSIONS[0], SESSIONS[-1],
        complete_through=SESSIONS[-1],
    )
    assert "600001.SH" in second.calls


def test_empty_symbol_list_is_a_no_op(warehouse):
    progress = ingest_universe(
        CountingProvider(), warehouse, [], SESSIONS[0], SESSIONS[-1],
        complete_through=SESSIONS[-1],
    )
    assert progress.total == 0


def test_bars_after_the_last_closed_session_are_dropped(warehouse):
    """盘中采集时「今日」的 bar 是残缺的，绝不能落盘。"""
    p = CountingProvider()
    ingest_universe(
        p, warehouse, symbols(1), SESSIONS[0], SESSIONS[-1],
        complete_through=SESSIONS[-2],
    )
    df = WarehouseQuery(warehouse).legacy_daily_bars(None, SESSIONS[0], SESSIONS[-1])
    assert df["dt"].max() == SESSIONS[-2]


def test_a_symbol_with_only_incomplete_bars_still_counts_as_done(warehouse):
    """全部被丢弃不等于失败——下次重跑不该再抓一遍。"""
    p = CountingProvider()
    progress = ingest_universe(
        p, warehouse, symbols(1), SESSIONS[0], SESSIONS[-1],
        complete_through=date(2023, 12, 31),
    )
    assert progress.failed == 0
    assert progress.done == 1


def test_empty_results_are_not_recorded_as_done(warehouse):
    """空结果不记账。

    记了账，续传就会跳过它——上游整体失效那天会把 5000 多只全部记成已完成，
    之后再也不会回补。空结果重取的代价是一次请求，记错账的代价是永久缺数据。
    """
    p = CountingProvider(empty={"600001.SH"})
    ingest_universe(
        p, warehouse, symbols(3), SESSIONS[0], SESSIONS[-1],
        complete_through=SESSIONS[-1],
    )
    done = completed_symbols(warehouse, SESSIONS[0], SESSIONS[-1])
    assert "600001.SH" not in done
    assert {"600000.SH", "600002.SH"} <= done


def test_progress_counts_empty_apart_from_done(warehouse):
    """空和有数据必须分开计数，否则「5219 成功 / 0 失败」会掩盖零行入库。"""
    p = CountingProvider(empty={"600001.SH", "600002.SH"})
    progress = ingest_universe(
        p, warehouse, symbols(4), SESSIONS[0], SESSIONS[-1],
        complete_through=SESSIONS[-1],
    )
    assert progress.done == 2
    assert progress.empty == 2
    assert progress.failed == 0


def test_source_is_serving_is_false_when_every_probe_comes_back_empty(warehouse):
    """探针全空 = 上游失效。高流动性标的不会同时整段停牌。"""
    p = CountingProvider(empty={"600000.SH", "600001.SH", "600002.SH"})
    assert not source_is_serving(p, symbols(3), SESSIONS[0], SESSIONS[-1])


def test_source_is_serving_is_true_when_any_probe_has_rows(warehouse):
    p = CountingProvider(empty={"600000.SH", "600001.SH"})
    assert source_is_serving(p, symbols(3), SESSIONS[0], SESSIONS[-1])


def test_source_is_serving_treats_exceptions_as_outage(warehouse):
    p = CountingProvider(failing={"600000.SH", "600001.SH", "600002.SH"})
    assert not source_is_serving(p, symbols(3), SESSIONS[0], SESSIONS[-1])


def test_stops_at_the_deadline_instead_of_running_over(warehouse):
    """日线跑超时不能把后面的步骤一起拖死。

    分钟线和竞价 tick 是滚动窗口，漏一天补不回来；日线漏了可以明天再采。
    所以日线要有自己的时间预算，到点就交还控制权，而不是等 systemd 把整个
    归档杀掉。
    """
    p = CountingProvider()
    progress = ingest_universe(
        p, warehouse, symbols(10), SESSIONS[0], SESSIONS[-1],
        complete_through=SESSIONS[-1], batch_size=3,
        deadline=time.monotonic() - 1,
    )
    assert p.calls == []
    assert progress.done == 0


def test_a_deadline_in_the_future_does_not_stop_the_run(warehouse):
    p = CountingProvider()
    progress = ingest_universe(
        p, warehouse, symbols(6), SESSIONS[0], SESSIONS[-1],
        complete_through=SESSIONS[-1], batch_size=3,
        deadline=time.monotonic() + 600,
    )
    assert progress.done == 6
