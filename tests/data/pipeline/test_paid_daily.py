from datetime import date
import json

import openpyxl
import polars as pl
import pytest

from qsys.data.pipeline.paid_daily import COLUMNS, read_day, import_year, audit


def write_day(root, stamp, records):
    path = root / stamp[:4] / f"{stamp}.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "data"
    sheet.append(["广告"])
    sheet.append([f"A股日线数据（{stamp}）"])
    sheet.append([c[0] for c in COLUMNS])
    for record in records:
        values = {"交易日期": stamp, "股票代码": "000001", "股票名称": "平安银行",
                  "开盘价": 10, "最高价": 11, "最低价": 9, "收盘价": 10,
                  "昨收价": 10, "成交量（手）": 1.23, "成交额（万元）": 0.123,
                  "复权因子": 1, "总股本（万股）": 100}
        values.update(record)
        sheet.append([values.get(c[0]) for c in COLUMNS])
    book.save(path)
    return path


def entry(path):
    return {"path": f"/daily/{path.parent.name}/{path.name}", "size": path.stat().st_size, "isdir": 0}


def test_units_nulls_name_and_symbol(tmp_path):
    frame = read_day(write_day(tmp_path, "20260910", [{"股票名称": "*ST历史名"}]))
    row = frame.row(0, named=True)
    assert row["symbol"] == "000001.SZ"
    assert row["name"] == "*ST历史名"
    assert row["volume"] == 1.23
    assert row["amount"] == pytest.approx(1230)
    assert row["total_shares"] == 1000000
    assert row["pe"] is None
    assert row["free_shares"] is None


@pytest.mark.parametrize("record,match", [
    ({"股票代码": "430001"}, "unknown exchange"),
    ({"交易日期": "20260909"}, "row date"),
    ({"开盘价": "not a number"}, None),
])
def test_reject_malformed_data(tmp_path, record, match):
    with pytest.raises(Exception, match=match):
        read_day(write_day(tmp_path, "20260910", [record]))


def test_duplicate_rejected(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        read_day(write_day(tmp_path, "20260910", [{}, {}]))


def test_complete_year_only_and_idempotence(tmp_path):
    incoming, wh = tmp_path / "incoming", tmp_path / "wh"
    path = write_day(incoming, "20260910", [{}])
    entries = [entry(path), {"path": "/daily/2026/20260911.xlsx", "size": 5, "isdir": 0}]
    assert import_year(incoming, wh, entries, "/daily", 2026)["ready"] is False
    assert not wh.exists()
    entries.pop()
    assert import_year(incoming, wh, entries, "/daily", 2026)["rows"] == 1
    assert import_year(incoming, wh, entries, "/daily", 2026)["skipped"]
    stored = pl.read_parquet(wh / "paid_daily/year=2026/part.parquet")
    assert stored["volume"].item() == 1.23


def test_cross_year_factor_audit_and_explicit_base(tmp_path):
    incoming, wh = tmp_path / "incoming", tmp_path / "wh"
    old = write_day(incoming, "20051230", [{}])
    new = write_day(incoming, "20060104", [{"复权因子": 2, "市盈率": None}])
    entries = [entry(old), entry(new)]
    for year in [2005, 2006]:
        import_year(incoming, wh, entries, "/daily", year)
    report = audit(wh, date(2006, 1, 5), 2)
    assert report["factor_anomalies_gt_1pct"] == 1
    assert report["anomalies_2005_2006"] == 1
    base = pl.read_parquet(wh / "paid_adjustment_bases/base_date=2006-01-05/part.parquet")
    assert base["base_date"].item() == date(2006, 1, 5)
    assert base["factor_date"].item() == date(2006, 1, 4)
    assert base["base_factor"].item() == 2
    assert report["null_counts"]["pe"] == 2


def test_query_explicit_anchor_and_no_null_fill(tmp_path):
    from qsys.data.pipeline.paid_daily import query_paid_daily
    incoming, wh = tmp_path / "incoming", tmp_path / "wh"
    first = write_day(incoming, "20260909", [{}])
    second = write_day(incoming, "20260910", [{"复权因子": 2}])
    entries = [entry(first), entry(second)]
    import_year(incoming, wh, entries, "/daily", 2026)
    audit(wh, date(2026, 9, 10), 2)
    frame = query_paid_daily(wh, date(2026, 9, 9), date(2026, 9, 10), ["000001.SZ"], base_date=date(2026, 9, 10))
    assert frame["qfq_close"].to_list() == [5.0, 10.0]
    assert frame["close"].to_list() == [10.0, 10.0]
    assert frame["pe"].null_count() == 2
    assert query_paid_daily(wh, date(2026, 9, 9), date(2026, 9, 10), []).height == 0


def test_audit_remembers_stock_across_absent_year(tmp_path):
    incoming, wh = tmp_path / "incoming", tmp_path / "wh"
    paths = [write_day(incoming, "20041231", [{}]),
             write_day(incoming, "20051230", [{"股票代码": "600001"}]),
             write_day(incoming, "20060104", [{"复权因子": 2}])]
    entries = [entry(p) for p in paths]
    for year in [2004, 2005, 2006]:
        import_year(incoming, wh, entries, "/daily", year)
    report = audit(wh, date(2006, 1, 4), 3)
    assert report["factor_anomalies_gt_1pct"] == 1
