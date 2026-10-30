"""QMT 区间查询会把首笔成交前的分钟用昨收价填满（见
docs/reference/qmt-range-query-padding.md）。导出时必须丢弃那些行。"""

import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts import win_download_minutes as downloader
from scripts.win_download_minutes import pending_manifest, read_done, to_rows


def _frames(stamps, opens, highs, lows, closes, volumes, amounts):
    """造 xtdata.get_market_data 的返回形状：{字段: DataFrame(index=[symbol])}。"""
    def frame(values):
        return pd.DataFrame([values], index=["513100.SH"], columns=stamps)
    return {"open": frame(opens), "high": frame(highs), "low": frame(lows),
            "close": frame(closes), "volume": frame(volumes), "amount": frame(amounts)}


def test_pads_before_first_trade_are_dropped():
    stamps = ["20260929093000", "20260929093100", "20260929093200"]
    data = _frames(stamps,
                   opens=[2.304, 2.304, 2.310], highs=[2.304, 2.304, 2.315],
                   lows=[2.304, 2.304, 2.308], closes=[2.304, 2.304, 2.312],
                   volumes=[0, 0, 500], amounts=[0.0, 0.0, 1156.0])
    rows = to_rows("513100.SH", data, ["20260929"])
    assert [r[1].strftime("%H:%M") for r in rows] == ["09:32"]


def test_zero_volume_after_first_trade_is_kept():
    """首笔成交之后的 0 量分钟是真实的无成交分钟，不是填充。"""
    stamps = ["20260929093000", "20260929093100", "20260929093200"]
    data = _frames(stamps,
                   opens=[2.30, 2.31, 2.31], highs=[2.30, 2.31, 2.31],
                   lows=[2.30, 2.31, 2.31], closes=[2.30, 2.31, 2.31],
                   volumes=[100, 0, 300], amounts=[230.0, 0.0, 693.0])
    rows = to_rows("513100.SH", data, ["20260929"])
    assert [r[1].strftime("%H:%M") for r in rows] == ["09:30", "09:31", "09:32"]


def test_day_without_any_trade_is_dropped_entirely():
    stamps = ["20260929093000", "20260929093100"]
    data = _frames(stamps,
                   opens=[2.304, 2.304], highs=[2.304, 2.304],
                   lows=[2.304, 2.304], closes=[2.304, 2.304],
                   volumes=[0, 0], amounts=[0.0, 0.0])
    assert to_rows("513100.SH", data, ["20260929"]) == []


def test_each_day_is_judged_on_its_own_first_trade():
    stamps = ["20260928093000", "20260928093100",
              "20260929093000", "20260929093100"]
    data = _frames(stamps,
                   opens=[2.20, 2.21, 2.30, 2.31], highs=[2.20, 2.21, 2.30, 2.31],
                   lows=[2.20, 2.21, 2.30, 2.31], closes=[2.20, 2.21, 2.30, 2.31],
                   volumes=[0, 400, 700, 0], amounts=[0.0, 884.0, 1610.0, 0.0])
    rows = to_rows("513100.SH", data, ["20260928", "20260929"])
    assert [r[1].strftime("%m-%d %H:%M") for r in rows] == [
        "09-28 09:31", "09-29 09:30", "09-29 09:31"]


def test_resume_ledger_is_keyed_by_symbol_and_day(tmp_path):
    ledger = tmp_path / "done.csv"
    ledger.write_text(
        "600000.SH,20260928\n600000.SH,2026-09-29\n600001.SH\n",
        encoding="utf-8",
    )
    assert read_done(str(ledger)) == {
        ("600000.SH", "20260928"),
        ("600000.SH", "20260929"),
    }


def test_a_completed_day_does_not_skip_the_same_symbol_on_another_day():
    wanted = {"600000.SH": ["20260928", "20260929"]}
    pending = pending_manifest(wanted, {("600000.SH", "20260928")})
    assert pending == {"600000.SH": ["20260929"]}


def test_checkpoint_never_advances_ledger_before_parquet_lands(monkeypatch, tmp_path):
    events = []

    def land(rows, out, index):
        events.append("parquet")
        return str(tmp_path / "minutes_00000.parquet")

    def account(path, keys):
        events.append("ledger")

    monkeypatch.setattr(downloader, "flush", land)
    monkeypatch.setattr(downloader, "append_done", account)
    downloader.flush_checkpoint(
        [("600000.SH",)], [("600000.SH", "20260928")],
        str(tmp_path), 0, str(tmp_path / "done.csv"),
    )
    assert events == ["parquet", "ledger"]


def test_failed_parquet_write_does_not_advance_ledger(monkeypatch, tmp_path):
    def boom(rows, out, index):
        raise OSError("disk full")

    monkeypatch.setattr(downloader, "flush", boom)

    def should_not_run(path, keys):
        raise AssertionError("ledger advanced")

    monkeypatch.setattr(downloader, "append_done", should_not_run)
    with pytest.raises(OSError, match="disk full"):
        downloader.flush_checkpoint(
            [("600000.SH",)], [("600000.SH", "20260928")],
            str(tmp_path), 0, str(tmp_path / "done.csv"),
        )


def test_main_writes_atomic_result_and_empty_is_not_a_process_failure(
    monkeypatch, tmp_path, capsys,
):
    manifest = tmp_path / "manifest.csv"
    with manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol", "dt"])
        writer.writeheader()
        writer.writerows([
            {"symbol": "600001.SH", "dt": "2026-09-29"},
            {"symbol": "600002.SH", "dt": "2026-09-29"},
        ])

    class XtData:
        @staticmethod
        def download_history_data(*args, **kwargs):
            return None

        @staticmethod
        def get_local_data(*, stock_list, **kwargs):
            assert kwargs["fill_data"] is False
            assert kwargs["count"] == -1
            assert kwargs["dividend_type"] == "none"
            assert kwargs["field_list"] == ["time", *downloader.COLUMNS[2:]]
            if stock_list == ["600002.SH"]:
                return {stock_list[0]: _local_frame([])}
            return {stock_list[0]: _local_frame(["20260929093000"], volumes=[100])}

        @staticmethod
        def get_market_data(**kwargs):
            pytest.fail("native local reader must never fall back to RPC")

    monkeypatch.setitem(sys.modules, "xtquant", SimpleNamespace(xtdata=XtData()))
    monkeypatch.setattr(sys, "argv", [
        "win_download_minutes.py", "--manifest", str(manifest),
        "--out", str(tmp_path), "--window-start", "20260929",
        "--window-end", "20260929", "--flush-rows", "1",
        "--progress-every", "1", "--run-id", "run-1",
    ])

    assert downloader.main() == 0
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["run_id"] == "run-1"
    assert result["source_api"] == "xtdata.get_local_data" and result["fill_data"] is False
    assert result["status"] == "completed_with_empty"
    assert result["downloaded"] == 1
    assert result["empty"] == [{"symbol": "600002.SH", "dt": "20260929"}]
    assert result["failed"] == []
    assert downloader.read_done(str(tmp_path / "done.csv")) == {
        ("600001.SH", "20260929")
    }
    assert downloader.PROGRESS_PREFIX in capsys.readouterr().out


def _local_frame(stamps, *, volumes=None):
    """实际本地API容器；浮点/空值测试另行显式改列。"""
    zone = timezone(timedelta(hours=8))
    return pd.DataFrame({
        "time": [int(datetime.strptime(s, "%Y%m%d%H%M%S").replace(tzinfo=zone).timestamp())
                 * 1000 for s in stamps],
        **{k: [10.0] * len(stamps) for k in ["open", "high", "low", "close"]},
        "volume": volumes if volumes is not None else [0] * len(stamps),
        "amount": [0.0] * len(stamps),
    }, index=stamps)


@pytest.mark.parametrize("volumes", [[0, 0, 100], [0, 0, 0], [100, 0, 100]])
def test_native_rows_preserve_every_real_zero_volume_bar(volumes):
    stamps = ["20261026093000", "20261026093100", "20261026093200"]
    rows = downloader.to_local_rows("301139.SZ", {"301139.SZ": _local_frame(stamps, volumes=volumes)}, ["20261026"])
    assert [r[1].strftime("%Y%m%d%H%M%S") for r in rows] == stamps
    assert [r[6] for r in rows] == volumes


def test_native_missing_values_reach_arrow_as_null_without_fill(tmp_path):
    import pyarrow.parquet as pq
    frame = _local_frame(["20261026093000"])
    frame["open"] = frame["amount"] = float("nan")
    frame["volume"] = pd.Series([None], index=frame.index, dtype="Int64")
    rows = downloader.to_local_rows("301139.SZ", {"301139.SZ": frame}, ["20261026"])
    assert rows[0][2] is None and rows[0][6] is None and rows[0][7] is None
    table = pq.read_table(downloader.flush(rows, str(tmp_path), 0))
    assert table.schema.equals(downloader._arrow_schema())
    assert table["open"].null_count == table["volume"].null_count == table["amount"].null_count == 1


@pytest.mark.parametrize("fault", ["fractional_volume", "duplicate_index", "time_mismatch", "missing_time",
                                  "missing_column", "duplicate_column", "infinity", "numeric_string"])
def test_native_invalid_source_is_rejected_without_guessing(fault):
    frame = _local_frame(["20261026093000"])
    if fault == "fractional_volume":
        frame["volume"] = [1.5]
    elif fault == "duplicate_index":
        frame = pd.concat([frame, frame])
    elif fault == "time_mismatch":
        frame["time"] += 60_000
    elif fault == "missing_time":
        frame["time"] = [None]
    elif fault == "missing_column":
        frame = frame.drop(columns="low")
    elif fault == "duplicate_column":
        frame = pd.concat([frame, frame[["low"]]], axis=1)
    elif fault == "infinity":
        frame["open"] = float("inf")
    elif fault == "numeric_string":
        frame["open"] = "10.00"
    with pytest.raises(ValueError):
        downloader.to_local_rows("301139.SZ", {"301139.SZ": frame}, ["20261026"])


def test_native_empty_cache_is_empty_and_extra_days_are_not_exported():
    assert downloader.to_local_rows("301139.SZ", {"301139.SZ": _local_frame([])}, ["20261026"]) == []
    frame = _local_frame(["20261026093000", "20261027093000"])
    rows = downloader.to_local_rows("301139.SZ", {"301139.SZ": frame}, ["20261027"])
    assert len(rows) == 1 and rows[0][1].strftime("%Y%m%d") == "20261027"


def test_main_read_failure_does_not_leak_private_message_or_advance_ledger(monkeypatch, tmp_path, capsys):
    manifest = tmp_path / "manifest.csv"
    manifest.write_text("symbol,dt\n301139.SZ,2026-10-26\n")
    class XtData:
        @staticmethod
        def download_history_data(*args, **kwargs):
            return None

        @staticmethod
        def get_local_data(**kwargs):
            raise ValueError("SYNTHETIC_PRIVATE_CONFIG_VALUE")

    monkeypatch.setitem(sys.modules, "xtquant", SimpleNamespace(xtdata=XtData()))
    monkeypatch.setattr(sys, "argv", ["win_download_minutes.py", "--manifest", str(manifest),
        "--out", str(tmp_path), "--window-start", "20261026", "--window-end", "20261026"])
    assert downloader.main() == 2
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["status"] == "failed" and result["failed"] == [{"symbol": "301139.SZ", "error": "ValueError"}]
    assert not (tmp_path / "done.csv").exists() and not list(tmp_path.glob("*.parquet"))
    assert "SYNTHETIC_PRIVATE_CONFIG_VALUE" not in capsys.readouterr().out
    assert all("SYNTHETIC_PRIVATE_CONFIG_VALUE" not in p.read_text() for p in tmp_path.glob("*.json"))
    assert "SYNTHETIC_PRIVATE_CONFIG_VALUE" not in (tmp_path / "failed.csv").read_text()
