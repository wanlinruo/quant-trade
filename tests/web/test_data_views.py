"""数据台视图模型。"""

from datetime import date

import polars as pl
import pyarrow as pa
import pytest

from qsys.data import schema
from qsys.data.storage import Warehouse
from qsys.web.data_views import DataService

D = [date(2024, 1, d) for d in (2, 3, 4)]


@pytest.fixture
def seeded(warehouse: Warehouse) -> Warehouse:
    n = len(D)
    warehouse.write(
        "bars_daily",
        pa.table(
            {
                "symbol": ["600001.SH"] * n, "dt": list(D),
                "open": [10.0] * n, "high": [10.0] * n, "low": [10.0] * n,
                "close": [10.0] * n, "volume": [100] * n, "amount": [0.0] * n,
            },
            schema=schema.BARS_DAILY,
        ),
        year=2024,
    )
    warehouse.write(
        "market_regime",
        pa.table(
            {
                "dt": list(D), "limit_up_count": [10, 20, 30],
                "limit_down_count": [1] * n, "limit_up_down_ratio": [1.0] * n,
                "touched_limit_up_count": [15, 25, 35], "broken_board_count": [5] * n,
                "broken_board_rate": [0.3] * n, "first_board_count": [7] * n,
                "market_advance_ratio": [0.5] * n,
                "market_median_return_1d": [0.01] * n,
                "market_total_money": [1e10] * n,
                "prior_limitup_nextday_return_mean": [0.02] * n,
                "prior_limitup_nextday_return_median": [0.02] * n,
                "valid_stock_count": [100] * n,
                "index_ma20_slope_5d": [0.001, 0.002, None],
            },
            schema=schema.MARKET_REGIME,
        ),
        year=2024,
    )
    return warehouse


def write_selection(tmp_path, rows):
    path = tmp_path / "selection.csv"
    pl.DataFrame(
        {
            "dt": [r[0] for r in rows], "symbol": [r[1] for r in rows],
            "tag": [r[2] for r in rows], "state": [r[3] for r in rows],
            "reason": ["x"] * len(rows), "allow": [r[4] for r in rows],
        }
    ).write_csv(path)
    return path


def test_coverage_lists_written_tables(seeded):
    names = {c.dataset for c in DataService(seeded).coverage()}
    assert {"bars_daily", "market_regime"} <= names


def test_warehouse_totals(seeded):
    rows, symbols = DataService(seeded).warehouse_totals()
    assert rows > 0
    assert symbols >= 1


def test_no_selection_file_is_not_an_error(seeded):
    summary = DataService(seeded).selection_summary()
    assert summary.total == 0
    assert summary.allowed == 0
    assert DataService(seeded).selections() == []


def test_a_malformed_selection_file_is_not_an_error(seeded, tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("nope,not,a,selection\n1,2,3,4\n")
    assert DataService(seeded, path).selection_summary().total == 0


def test_selection_summary_counts_only_allowed(seeded, tmp_path):
    path = write_selection(
        tmp_path,
        [
            (D[0], "600001.SH", "LOW_MAIN", "CLEAN", True),
            (D[0], "600002.SH", "LOW_MAIN", "RISK", False),
            (D[1], "300001.SZ", "HIGH_CHINEXT", "FAVORABLE", True),
        ],
    )
    summary = DataService(seeded, path).selection_summary()
    assert summary.total == 3
    assert summary.allowed == 2
    assert summary.sessions == 2
    assert summary.symbols == 2
    assert dict(summary.by_tag) == {"LOW_MAIN": 1, "HIGH_CHINEXT": 1}


def test_selection_summary_keeps_rejected_states_visible(seeded, tmp_path):
    """被拒的也要能看到，否则说不清是没选中还是被 Gate 拦了。"""
    path = write_selection(
        tmp_path,
        [
            (D[0], "600001.SH", "LOW_MAIN", "CLEAN", True),
            (D[0], "600002.SH", "LOW_MAIN", "RISK", False),
        ],
    )
    states = DataService(seeded, path).selection_summary().by_state
    assert ("LOW_MAIN", "RISK", 1) in states


def test_selections_are_newest_first(seeded, tmp_path):
    path = write_selection(
        tmp_path,
        [
            (D[0], "600001.SH", "LOW_MAIN", "CLEAN", True),
            (D[2], "600003.SH", "LOW_MAIN", "CLEAN", True),
        ],
    )
    rows = DataService(seeded, path).selections()
    assert [r["symbol"] for r in rows] == ["600003.SH", "600001.SH"]


def test_selections_can_be_filtered_by_tag(seeded, tmp_path):
    path = write_selection(
        tmp_path,
        [
            (D[0], "600001.SH", "LOW_MAIN", "CLEAN", True),
            (D[1], "300001.SZ", "HIGH_CHINEXT", "FAVORABLE", True),
        ],
    )
    rows = DataService(seeded, path).selections(tag="LOW_MAIN")
    assert [r["symbol"] for r in rows] == ["600001.SH"]


def test_regime_returns_the_tail(seeded):
    frame = DataService(seeded).regime(limit=2)
    assert frame.height == 2
    assert frame["dt"].to_list() == [D[1], D[2]]


def test_regime_series_skips_nulls(seeded):
    """指数因子在数据不足的日子是 null，画图时跳过而不是当成 0。"""
    assert DataService(seeded).regime_series("index_ma20_slope_5d") == [0.001, 0.002]


def test_regime_series_of_an_unknown_column_is_empty(seeded):
    assert DataService(seeded).regime_series("nope") == []


def test_latest_session(seeded):
    assert DataService(seeded).latest_session() == D[2]


def test_everything_is_empty_on_a_fresh_warehouse(warehouse):
    service = DataService(warehouse)
    assert service.coverage() == []
    assert service.latest_session() is None
    assert service.regime().height == 0
