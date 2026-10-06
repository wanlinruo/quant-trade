"""D-7：竞价 tick 采集失败必须让归档以失败退出，而不是只打日志。"""

from __future__ import annotations

from argparse import Namespace
from datetime import date

import polars as pl

from qsys.data.pipeline.auction import ImportResult
from scripts import daily_archive

DAY = date(2026, 9, 16)
ARGS = Namespace(win_root="C:/qsys/daily", win_host="Administrator@win",
                 win_python="python", win_auction_script="auction.py")
WANTED = pl.DataFrame({"symbol": ["600036.SH"], "dt": [DAY]})


def _archive(monkeypatch, tmp_path, *, missing=WANTED, run=None, imported=ImportResult(1, 10, 0)):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(daily_archive, "missing_auction_days", lambda wh, wanted: missing)
    monkeypatch.setattr(daily_archive, "run", run or (lambda *a, **k: "完成"))
    monkeypatch.setattr(daily_archive, "import_auction_ticks", lambda *a, **k: imported)
    return daily_archive._archive_auction(ARGS, None, WANTED, ["ssh"], "key", "20260916")


def test_nothing_missing_is_ok(monkeypatch, tmp_path):
    assert _archive(monkeypatch, tmp_path, missing=WANTED.clear()) is True


def test_successful_download_and_import_is_ok(monkeypatch, tmp_path):
    assert _archive(monkeypatch, tmp_path) is True


def test_windows_failure_is_reported(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise RuntimeError("Windows 下载竞价 tick 失败 (rc=1)")
    assert _archive(monkeypatch, tmp_path, run=boom) is False


def test_import_errors_are_reported(monkeypatch, tmp_path):
    assert _archive(monkeypatch, tmp_path, imported=ImportResult(1, 0, 1)) is False


def test_auction_failure_turns_success_into_failure():
    assert daily_archive._exit_code(0, auction_ok=False) == 1
    assert daily_archive._exit_code(0, auction_ok=True) == 0
    assert daily_archive._exit_code(1, auction_ok=True) == 1


def test_minute_manifest_includes_paper_holdings(tmp_path):
    import sqlite3

    store = tmp_path / "workbench.sqlite3"
    with sqlite3.connect(store) as conn:
        conn.execute("CREATE TABLE paper_fills (id INTEGER PRIMARY KEY, account_id TEXT, "
                     "order_id TEXT, symbol TEXT, side TEXT, qty INTEGER, price REAL, "
                     "fee REAL, filled_at TEXT)")
        conn.execute("INSERT INTO paper_fills (account_id, order_id, symbol, side, qty, price, "
                     "fee, filled_at) VALUES ('a', 'o1', '001399.SZ', 'buy', 2000, 23.84, "
                     "14.78, '2026-09-15T09:30:03')")
    fills = daily_archive._paper_fills(store)
    held = daily_archive.held_stock_days(fills, DAY, DAY, [DAY])
    wanted = daily_archive._minute_wanted(WANTED.clear(), WANTED, held)
    assert sorted(wanted["symbol"].to_list()) == ["001399.SZ", "600036.SH"]


def test_missing_paper_store_is_not_an_error(tmp_path):
    assert daily_archive._paper_fills(tmp_path / "absent.sqlite3").height == 0


def test_full_market_stock_days_respect_board_and_listing_boundaries():
    instruments = pl.DataFrame({
        "symbol": ["600001.SH", "300001.SZ", "920001.BJ", "200001.SZ"],
        "board": ["main", "gem", "bse", "b_share"],
        "list_date": [date(2026, 9, 15), date(2026, 9, 17), None, None],
        "delist_date": [None, None, date(2026, 9, 16), None],
    })
    got = daily_archive._full_market_stock_days(
        instruments,
        [date(2026, 9, 16), date(2026, 9, 17)],
        {"main", "gem", "bse"},
    )
    assert got.rows() == [
        ("600001.SH", date(2026, 9, 16)),
        ("920001.BJ", date(2026, 9, 16)),
        ("300001.SZ", date(2026, 9, 17)),
        ("600001.SH", date(2026, 9, 17)),
    ]


def test_no_missing_rerun_republishes_the_whole_wanted_range():
    wanted = pl.DataFrame({
        "symbol": ["600001.SH", "600001.SH"],
        "dt": [date(2026, 9, 21), date(2026, 9, 30)],
    })
    assert daily_archive._wanted_date_range(wanted, DAY) == (
        date(2026, 9, 21), date(2026, 9, 30)
    )


def test_known_minute_difference_does_not_hide_blocking_issues():
    from qsys.data.quality.minute_rules import MinuteIssue

    known = MinuteIssue("920055.BJ", date(2026, 9, 21), "high_vs_daily", "x", "D-24")
    blocking = MinuteIssue("600001.SH", DAY, "high_vs_daily", "y")
    got_blocking, got_known = daily_archive._split_minute_issues([known, blocking])
    assert got_blocking == [blocking]
    assert got_known == [known]


def test_shared_minute_gate_blocks_unknown_but_not_d24(monkeypatch):
    from qsys.data.quality.minute_rules import MinuteIssue

    known = MinuteIssue("920055.BJ", date(2026, 9, 21), "high_vs_daily", "x", "D-24")
    blocking = MinuteIssue("600001.SH", DAY, "high_vs_daily", "y")
    monkeypatch.setattr(
        daily_archive, "check_minute_bars",
        lambda warehouse, day: [known, blocking],
    )

    class Run:
        def __init__(self):
            self.steps = []

        def add_step(self, *args, **kwargs):
            self.steps.append((args, kwargs))

    run = Run()
    assert daily_archive._check_minute_days(None, [DAY], run) == 1
    assert run.steps[0][1]["status"] == daily_archive.FAILED
    assert "已知差异 1" in run.steps[0][1]["detail"]
