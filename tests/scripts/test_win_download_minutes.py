"""D-18/D-20：分钟线下载脚本——落盘后才记账、账本按标的 + 日期、文件编号接续。

全市场每日约 5,550 只，任何一次中途退出都不能让已记账的标的丢数据。
"""

from datetime import datetime

import pyarrow.parquet as pq
import pytest

from scripts.win_download_minutes import ledger_key, read_done, run_batch

GROUPED = {f"60000{i}.SH": ["20260917"] for i in range(5)}


def _rows(code):
    return [(code, datetime(2026, 9, 17, 9, 30), 10.0, 10.0, 10.0, 10.0, 100, 1000.0)]


class Crash(BaseException):
    """进程级退出：单只失败的 except 接不住。"""


def test_same_symbol_on_another_day_is_a_different_ledger_entry():
    assert ledger_key("600000.SH", ["20260916"]) != ledger_key("600000.SH", ["20260917"])


def test_a_crash_leaves_no_symbol_marked_done_without_its_rows_on_disk(tmp_path):
    def fetch(code, stamps):
        if code == "600003.SH":
            raise Crash
        return _rows(code)

    with pytest.raises(Crash):
        run_batch(fetch, GROUPED, sorted(GROUPED), str(tmp_path), flush_every=2)
    done = {key.split(":")[0] for key in read_done(str(tmp_path / "done.csv"))}
    written = {r["symbol"] for f in tmp_path.glob("minutes_*.parquet") for r in pq.read_table(f).to_pylist()}
    assert done == written == {"600000.SH", "600001.SH"}


def test_empty_symbols_are_recorded_so_reruns_skip_them(tmp_path):
    stats = run_batch(lambda c, s: [], GROUPED, sorted(GROUPED), str(tmp_path), flush_every=2)
    assert stats["empty"] == 5 and len(read_done(str(tmp_path / "done.csv"))) == 5


def test_failures_are_not_recorded(tmp_path):
    def fetch(code, stamps):
        raise RuntimeError("xtdata timeout")

    stats = run_batch(fetch, GROUPED, sorted(GROUPED), str(tmp_path), flush_every=2)
    assert stats["failed"] == 5 and read_done(str(tmp_path / "done.csv")) == set()


def test_file_numbering_continues_across_runs(tmp_path):
    run_batch(lambda c, s: _rows(c), {"600000.SH": ["20260917"]}, ["600000.SH"], str(tmp_path))
    run_batch(lambda c, s: _rows(c), {"600001.SH": ["20260917"]}, ["600001.SH"], str(tmp_path))
    assert sorted(p.name for p in tmp_path.glob("minutes_*.parquet")) == [
        "minutes_00000.parquet", "minutes_00001.parquet"]
