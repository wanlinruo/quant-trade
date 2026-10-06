from datetime import date, datetime

import polars as pl

from scripts.analyze_paid_minute_d20 import analyze


def test_attributes_daily_truth_rounding_and_bj_post_close(tmp_path):
    quality = tmp_path / "paid_minute_quality/year=2025"
    daily = tmp_path / "tushare_daily/year=2025"
    minute = tmp_path / "paid_minute_bars/year=2025/shard=0000"
    override = tmp_path / "paid_minute_bars/year=2025/date=20250102"
    qmt = tmp_path / "minute_bars/year=2025"
    quality.mkdir(parents=True)
    daily.mkdir(parents=True)
    minute.mkdir(parents=True)
    override.mkdir(parents=True)
    qmt.mkdir(parents=True)
    day = date(2025, 1, 2)
    pl.DataFrame({
        "symbol": ["600000.SH", "920001.BJ"], "dt": [day, day],
        "open": [10.01, 20.0], "high": [10.0, 20.0],
        "low": [10.0, 20.0], "close": [10.0, 20.0],
        "volume": [999.0, 1000.0], "amount": [10000.0, 10000.0],
        "paid_open": [10.0, 20.0], "paid_high": [10.0, 20.0],
        "paid_low": [10.0, 20.0], "paid_close": [10.0, 20.0],
        "paid_volume_lots": [10.0, 20.0], "paid_amount": [10000.0, 30000.0],
        "ohlc_mismatch": [True, False], "volume_mismatch": [True, True],
        "amount_mismatch_gt_100": [False, True],
    }).write_parquet(quality / "daily_discrepancies.parquet")
    pl.DataFrame({
        "symbol": ["600000.SH", "920001.BJ"], "dt": [day, day],
        "open": [10.0, 20.0], "high": [10.0, 20.0],
        "low": [10.0, 20.0], "close": [10.0, 20.0],
        "volume": [1000.0, 1000.0], "amount": [10000.0, 10000.0],
    }).write_parquet(quality / "daily_aggregate.parquet")
    pl.DataFrame({
        "symbol": ["600000.SH", "920001.BJ"], "dt": [day, day],
        "open": [10.0, 20.0], "high": [10.0, 20.0],
        "low": [10.0, 20.0], "close": [10.0, 20.0],
        "volume": [10.0, 20.0], "amount": [10000.0, 30000.0],
    }).write_parquet(daily / "part.parquet")
    pl.DataFrame({
        "symbol": ["920001.BJ", "920001.BJ"],
        "dt": [datetime(2025, 1, 2, 15, 0), datetime(2025, 1, 2, 15, 1)],
        "volume": [1000.0, 1000.0], "amount": [10000.0, 20000.0],
    }).write_parquet(minute / "part.parquet")
    pl.DataFrame({
        "symbol": ["600000.SH"], "dt": [datetime(2025, 1, 2, 15, 0)],
        "open": [10.0], "high": [10.0], "low": [10.0], "close": [10.0],
        "volume": [1000.0], "amount": [10000.0], "factor": [1.0],
    }).write_parquet(override / "part.parquet")
    pl.DataFrame({
        "symbol": ["600000.SH"], "dt": [datetime(2025, 1, 2, 15, 0)],
        "open": [10.0], "high": [10.0], "low": [10.0], "close": [10.0],
        "volume": [10.0], "amount": [10000.0],
    }).write_parquet(qmt / "part.parquet")

    result = analyze(
        tmp_path, [2025], verify_bj_post_close=True, verify_qmt_overlap=True,
        verify_overrides=True,
    )
    report = result["years"][0]

    assert report["ohlc_paid_daily_matches_independent"] == 1
    assert report["volume_rounding_le_one_lot"] == 1
    assert report["amount_bj"] == 1
    assert report["bj_post_close_verification"] == {
        "target_days": 1,
        "target_days_with_post_close_rows": 1,
        "sum_rows_resolved": 1,
        "last_row_resolved": 1,
        "max_row_resolved": 1,
    }
    assert report["qmt_daily_overlap"]["stock_days"] == 1
    assert report["qmt_daily_overlap"]["qmt_paid_ohlc_match_days"] == 1
    assert report["qmt_daily_overlap"]["qmt_paid_volume_within_one_lot_days"] == 1
    assert result["date_overrides"]["stock_days"] == 1
    assert result["totals"]["ohlc_days"] == 1
