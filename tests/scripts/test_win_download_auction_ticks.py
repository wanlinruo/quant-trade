"""D-7：续传账本必须区分同一标的的不同日期。"""

from scripts.win_download_auction_ticks import ledger_key


def test_same_symbol_with_other_dates_is_not_done():
    today = ledger_key("600036.SH", ["20260916"])
    backfill = ledger_key("600036.SH", ["20260820", "20260916"])
    assert today != backfill


def test_key_is_stable_for_identical_request():
    assert ledger_key("600036.SH", ["20260820"]) == ledger_key("600036.SH", ["20260820"])


# --- 落盘之后才记账 -------------------------------------------------------

from datetime import date, datetime  # noqa: E402

import pyarrow.parquet as pq  # noqa: E402
import pytest  # noqa: E402

from scripts.win_download_auction_ticks import read_done, run_batch  # noqa: E402

GROUPED = {f"60000{i}.SH": ["20260818"] for i in range(5)}


def _row(code):
    return {"symbol": code, "dt": date(2026, 8, 18), "ts": datetime(2026, 8, 18, 9, 15, 3),
            "bid1": 10.0, "ask1": 10.0, "bid_vol1": 1, "ask_vol1": 1,
            "last_price": 0.0, "volume": 0, "amount": 0.0}


class Crash(BaseException):
    """模拟进程级退出：不是 Exception，单只失败的 except 接不住。"""


def test_a_crash_never_leaves_unwritten_symbols_marked_done(tmp_path):
    def fetch(code, stamps):
        if code == "600003.SH":
            raise Crash
        return [_row(code)]

    with pytest.raises(Crash):
        run_batch(fetch, GROUPED, sorted(GROUPED), tmp_path, flush_every=2)

    done = read_done(tmp_path / "done.csv")
    written = {r["symbol"] for f in tmp_path.glob("auction_*.parquet")
               for r in pq.read_table(f).to_pylist()}
    assert {key.split(":")[0] for key in done} == written == {"600000.SH", "600001.SH"}


def test_a_rerun_in_the_same_directory_does_not_overwrite_earlier_files(tmp_path):
    run_batch(lambda c, s: [_row(c)], {"600000.SH": ["20260818"]}, ["600000.SH"], tmp_path)
    run_batch(lambda c, s: [_row(c)], {"600001.SH": ["20260818"]}, ["600001.SH"], tmp_path)
    names = sorted(p.name for p in tmp_path.glob("auction_*.parquet"))
    assert names == ["auction_00000.parquet", "auction_00001.parquet"]


def test_a_failed_symbol_is_not_marked_done(tmp_path):
    def fetch(code, stamps):
        if code == "600001.SH":
            raise RuntimeError("xtdata 超时")
        return [_row(code)]

    stats = run_batch(fetch, GROUPED, sorted(GROUPED), tmp_path, flush_every=10)
    assert stats["failed"] == 1 and stats["ok"] == 4
    assert "600001.SH:20260818" not in read_done(tmp_path / "done.csv")
