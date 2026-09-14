"""写 bars_daily 的模块必须读 bars_daily。

换底之后 daily_bars() 读的是付费源。这三个模块仍然维护旧表，
若继续读 daily_bars()，合并结果会把付费数据写进冻结的旧表，
而 Task 5 的回归对账正是拿旧表当参照物。
"""

import inspect

from qsys.data.pipeline import bars, daily_backfill, universe


def test_universe_reads_the_table_it_writes():
    src = inspect.getsource(universe)
    assert "legacy_daily_bars" in src
    assert ".daily_bars(" not in src


def test_bars_reads_the_table_it_writes():
    src = inspect.getsource(bars)
    assert "legacy_daily_bars" in src
    assert ".daily_bars(" not in src


def test_daily_backfill_reads_the_table_it_writes():
    src = inspect.getsource(daily_backfill)
    assert "legacy_daily_bars" in src
    assert ".daily_bars(" not in src
