"""QMT 区间查询会把首笔成交前的分钟用昨收价填满（见
docs/reference/qmt-range-query-padding.md）。导出时必须丢弃那些行。"""

import pandas as pd

from scripts.win_download_minutes import read_done, to_rows


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
