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
