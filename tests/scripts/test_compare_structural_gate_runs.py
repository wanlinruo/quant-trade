from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from scripts.compare_structural_gate_runs import compare_run


def test_compare_run_uses_pre_split_nav_and_attributes_trades(tmp_path: Path) -> None:
    pl.DataFrame({
        "date": ["2025-12-31", "2026-01-02", "2026-06-30", "2026-07-01"],
        "total_value": [100.0, 110.0, 121.0, 133.1],
    }).write_csv(tmp_path / "daily_nav.csv")
    pl.DataFrame({
        "filled_at": ["2026-01-02T09:30:00", "2026-07-01T09:30:00"],
        "side": ["buy", "sell"],
        "fee": [1.0, 2.0],
    }).write_csv(tmp_path / "fills.csv")
    pl.DataFrame({
        "entry_time": ["2026-01-02T09:30:00"],
        "return_pct": [5.0],
    }).write_csv(tmp_path / "closed_lots.csv")
    pl.DataFrame({
        "date": ["2026-01-02", "2026-07-01"],
        "real_allow": [True, False],
    }).write_csv(tmp_path / "gate_decisions.csv")

    rows = {row["split"]: row for row in compare_run("candidate", tmp_path)}

    assert rows["VALID"]["total_return"] == pytest.approx(0.21)
    assert rows["VALID"]["fills"] == 1
    assert rows["VALID"]["buys"] == 1
    assert rows["VALID"]["closed_lots_by_entry"] == 1
    assert rows["VALID"]["win_rate_by_entry"] == 1.0
    assert rows["VALID"]["gate_allowed"] == 1
    assert rows["OBS"]["total_return"] == pytest.approx(0.10)
    assert rows["OBS"]["sells"] == 1
