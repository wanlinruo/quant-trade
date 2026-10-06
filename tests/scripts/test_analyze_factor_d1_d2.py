from datetime import date, datetime

import polars as pl

from scripts.analyze_factor_d1_d2 import analyze
from scripts.record_factor_d1_d2_evidence import record
from qsys.data.query import WarehouseQuery


def test_qmt_factor_attributes_each_divergence_shape(tmp_path, warehouse):
    verification = tmp_path / "verification.parquet"
    dividends = tmp_path / "dividends.parquet"
    pl.DataFrame({
        "symbol": ["000001.SZ", "000002.SZ", "920001.BJ", "600001.SH"],
        "dt": [date(2024, 1, 2), date(2024, 1, 3),
               date(2024, 1, 4), date(2005, 1, 5)],
        "direct": [1.0, 1.2, 1.3, 1.4],
        "computed": [1.1, 1.0, 1.1, 1.2],
        "rel_diff": [0.1, 1 / 6, 2 / 13, 1 / 7],
    }).write_parquet(verification)
    pl.DataFrame({
        "symbol": ["000001.SZ", "000002.SZ", "920001.BJ"],
        "ex_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
        "factor": [1.1, 1.2, 1.2],
    }).write_parquet(dividends)

    details = tmp_path / "details.parquet"
    feedback = tmp_path / "feedback.csv"
    report = analyze(
        verification, [dividends], details_path=details,
        feedback_path=feedback,
    )

    d1 = report["scopes"]["d1_non_reform_a_share"]
    assert d1["rows"] == 2
    assert d1["attribution"] == {
        "qmt_supports_price_gap": 1,
        "qmt_supports_tushare_factor": 1,
    }
    assert report["scopes"]["d2_bj_92"]["attribution"] == {
        "qmt_matches_neither": 1,
    }
    assert report["scopes"]["share_reform_2005_2006"]["attribution"] == {
        "qmt_symbol_missing": 1,
    }
    assert pl.read_csv(feedback).height == 1

    added, total = record(warehouse, details, datetime(2026, 10, 6, 15, 0))
    assert (added, total) == (3, 3)
    evidence = WarehouseQuery(warehouse).metric_evidence().sort("symbol")
    assert evidence["verdict"].to_list() == ["mismatch", "match", "inconclusive"]
    assert record(warehouse, details, datetime(2026, 10, 6, 15, 1)) == (0, 3)
