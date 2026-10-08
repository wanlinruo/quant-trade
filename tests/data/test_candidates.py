"""候选股-日选择器：决定要下载哪些股在哪些日的分钟线。"""

from datetime import date

import pyarrow as pa

from qsys.data import schema
from qsys.data.pipeline.candidates import candidate_stock_days, write_manifest
from qsys.data.storage import Warehouse

D = [date(2024, 1, i) for i in (2, 3, 4, 5, 8)]


def seed_flags(warehouse: Warehouse, rows) -> None:
    """rows: [(symbol, dt, is_first_board)]"""
    n = len(rows)
    warehouse.write(
        "daily_flags",
        pa.table(
            {
                "dt": [r[1] for r in rows],
                "symbol": [r[0] for r in rows],
                "prev_close": [10.0] * n,
                "adj_prev_close": [10.0] * n,
                "limit_up": [11.0] * n,
                "limit_down": [9.0] * n,
                "is_limit_up": [r[2] for r in rows],
                "is_limit_down": [False] * n,
                "touched_limit_up": [r[2] for r in rows],
                "is_broken_board": [False] * n,
                "is_first_board": [r[2] for r in rows],
                "limit_pct": [0.10] * n,
                "is_st": [False] * n,
                "st_source": ["paid"] * n,
            },
            schema=schema.DAILY_FLAGS,
        ),
        year=2024,
    )


def test_candidate_is_the_session_after_a_first_board(warehouse):
    seed_flags(
        warehouse,
        [
            ("600001.SH", D[0], False),
            ("600001.SH", D[1], True),
            ("600001.SH", D[2], False),
        ],
    )
    out = candidate_stock_days(warehouse, D[0], D[-1])
    assert out.rows() == [("600001.SH", D[2])]


def test_window_extends_over_following_sessions(warehouse):
    seed_flags(
        warehouse,
        [
            ("600001.SH", D[0], True),
            ("600001.SH", D[1], False),
            ("600001.SH", D[2], False),
            ("600001.SH", D[3], False),
        ],
    )
    out = candidate_stock_days(warehouse, D[0], D[-1], window=3)
    assert out["dt"].to_list() == [D[1], D[2], D[3]]


def test_uses_trading_sessions_not_calendar_days(warehouse):
    """周末不是交易日。首板在周五，候选日是下周一。"""
    seed_flags(
        warehouse,
        [
            ("600001.SH", D[3], True),  # 1/5 周五
            ("600001.SH", D[4], False),  # 1/8 周一
        ],
    )
    out = candidate_stock_days(warehouse, D[0], D[-1])
    assert out.rows() == [("600001.SH", D[4])]


def test_last_first_board_has_no_following_session(warehouse):
    """区间最后一天首板，没有次日可给——不该造出不存在的日期。"""
    seed_flags(warehouse, [("600001.SH", D[-1], True)])
    assert candidate_stock_days(warehouse, D[0], D[-1]).height == 0


def test_non_first_boards_are_excluded(warehouse):
    seed_flags(
        warehouse,
        [("600001.SH", D[0], False), ("600001.SH", D[1], False)],
    )
    assert candidate_stock_days(warehouse, D[0], D[-1]).height == 0


def test_result_is_deduplicated_and_sorted(warehouse):
    """两次首板的窗口可能重叠，同一股-日只该出现一次。"""
    seed_flags(
        warehouse,
        [
            ("600001.SH", D[0], True),
            ("600001.SH", D[1], True),
            ("600001.SH", D[2], False),
            ("600001.SH", D[3], False),
        ],
    )
    out = candidate_stock_days(warehouse, D[0], D[-1], window=2)
    assert out.height == out.unique().height
    assert out["dt"].to_list() == sorted(out["dt"].to_list())


def test_separate_symbols_do_not_interfere(warehouse):
    seed_flags(
        warehouse,
        [
            ("600001.SH", D[0], True),
            ("600001.SH", D[1], False),
            ("600002.SH", D[0], False),
            ("600002.SH", D[1], False),
        ],
    )
    assert candidate_stock_days(warehouse, D[0], D[-1]).rows() == [("600001.SH", D[1])]


def test_window_must_be_positive(warehouse):
    import pytest

    seed_flags(warehouse, [("600001.SH", D[0], True)])
    with pytest.raises(ValueError):
        candidate_stock_days(warehouse, D[0], D[-1], window=0)


def test_empty_warehouse_yields_nothing(warehouse):
    assert candidate_stock_days(warehouse, D[0], D[-1]).height == 0


def test_manifest_is_written_as_csv(warehouse, tmp_path):
    seed_flags(
        warehouse,
        [("600001.SH", D[0], True), ("600001.SH", D[1], False)],
    )
    out = candidate_stock_days(warehouse, D[0], D[-1])
    path = tmp_path / "manifest.csv"
    assert write_manifest(out, path) == 1
    text = path.read_text()
    assert "symbol,dt" in text
    assert "600001.SH,2024-01-03" in text


# ---- 持仓期股-日：退出要的是持仓每一天的日内报价，不是候选日那几天 ----

def _fills(rows):
    """rows: [(symbol, dt, side)]"""
    import polars as pl

    return pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "dt": [r[1] for r in rows],
            "side": [r[2] for r in rows],
        },
        schema={"symbol": pl.Utf8, "dt": pl.Date, "side": pl.Utf8},
    )


def test_holding_days_start_the_session_after_the_buy():
    """T+1：买入当日不可卖，那天不需要退出报价。"""
    from qsys.data.pipeline.candidates import holding_stock_days

    got = holding_stock_days(_fills([("600001.SH", D[1], "buy")]), D, window=2)

    assert got.to_dicts() == [
        {"symbol": "600001.SH", "dt": D[2]},
        {"symbol": "600001.SH", "dt": D[3]},
    ]


def test_holding_days_walk_sessions_not_calendar_days():
    """D[3]=1/5 是周五，下一个交易日是 D[4]=1/8。"""
    from qsys.data.pipeline.candidates import holding_stock_days

    got = holding_stock_days(_fills([("600001.SH", D[3], "buy")]), D, window=1)

    assert got["dt"].to_list() == [D[4]]


def test_holding_days_ignore_sells():
    from qsys.data.pipeline.candidates import holding_stock_days

    got = holding_stock_days(
        _fills([("600001.SH", D[0], "sell"), ("600002.SH", D[0], "buy")]),
        D,
        window=1,
    )

    assert got["symbol"].to_list() == ["600002.SH"]


def test_holding_days_stop_at_the_last_session():
    from qsys.data.pipeline.candidates import holding_stock_days

    got = holding_stock_days(_fills([("600001.SH", D[4], "buy")]), D, window=5)

    assert got.height == 0


def test_holding_days_deduplicate_overlapping_windows():
    """同一标的连买两次，重叠的那几天只出现一次。"""
    from qsys.data.pipeline.candidates import holding_stock_days

    got = holding_stock_days(
        _fills([("600001.SH", D[0], "buy"), ("600001.SH", D[1], "buy")]),
        D,
        window=2,
    )

    assert got["dt"].to_list() == [D[1], D[2], D[3]]


def test_holding_days_reject_zero_window():
    import pytest

    from qsys.data.pipeline.candidates import holding_stock_days

    with pytest.raises(ValueError, match="window must be >= 1"):
        holding_stock_days(_fills([("600001.SH", D[0], "buy")]), D, window=0)


def test_candidates_can_use_an_external_calendar(warehouse):
    """竞价 tick 要在当日日线入库前就采，那时 T 日还不在 daily_flags 里。

    D[3] 首板，flags 只到 D[3]；显式传入含 D[4] 的日历，候选日才算得出来。
    """
    seed_flags(warehouse, [("600001.SH", D[3], True)])

    without = candidate_stock_days(warehouse, D[0], D[3])
    with_calendar = candidate_stock_days(warehouse, D[0], D[3], sessions=D)

    assert without.height == 0
    assert with_calendar.to_dicts() == [{"symbol": "600001.SH", "dt": D[4]}]


def test_external_calendar_missing_a_flag_day_is_skipped_not_fatal(warehouse):
    seed_flags(warehouse, [("600001.SH", D[1], True)])

    got = candidate_stock_days(warehouse, D[0], D[4], sessions=[D[3], D[4]])

    assert got.height == 0


# ---- 模拟盘持仓日：按真实成交还原持有区间 ----

def _paper_fills(rows):
    """rows: [(account, symbol, side, qty, filled_at)]"""
    import polars as pl

    return pl.DataFrame(rows, schema={"account_id": pl.Utf8, "symbol": pl.Utf8,
                                      "side": pl.Utf8, "qty": pl.Int64,
                                      "filled_at": pl.Utf8}, orient="row")


def test_held_days_cover_buy_day_through_the_day_the_position_is_closed():
    from qsys.data.pipeline.candidates import held_stock_days

    fills = _paper_fills([
        ("a", "600001.SH", "buy", 1000, f"{D[1]}T09:30:03"),
        ("a", "600001.SH", "sell", 400, f"{D[2]}T11:25:00"),   # 部分卖出仍持有
        ("a", "600001.SH", "sell", 600, f"{D[3]}T14:50:00"),   # 清仓日也要
    ])
    got = held_stock_days(fills, D[0], D[-1], D)
    assert got["dt"].to_list() == [D[1], D[2], D[3]]


def test_open_positions_are_held_through_the_end_and_accounts_are_separate():
    from qsys.data.pipeline.candidates import held_stock_days

    fills = _paper_fills([
        ("a", "600001.SH", "buy", 1000, f"{D[1]}T09:30:03"),
        ("a", "600001.SH", "sell", 1000, f"{D[1]}T14:50:00"),
        # 另一个账户仍持有同一只：不能被 a 的清仓抵消
        ("b", "600001.SH", "buy", 500, f"{D[3]}T09:30:03"),
        ("b", "000001.SZ", "buy", 500, f"{D[0]}T09:30:03"),
    ])
    got = held_stock_days(fills, D[2], D[4], D)
    assert sorted(got.rows()) == sorted(
        [("000001.SZ", d) for d in D[2:5]] + [("600001.SH", D[3]), ("600001.SH", D[4])]
    )


def test_no_fills_means_no_held_days():
    from qsys.data.pipeline.candidates import held_stock_days

    assert held_stock_days(_paper_fills([]), D[0], D[-1], D).height == 0


# --- 扣掉直取停牌 -----------------------------------------------------------
# 2025 年质检扣了停牌而下载没扣，QMT 对停牌股返回 241 根前收盘价，42 个整日虚构的
# 标的日因此进了仓库。见 docs/reference/qmt-range-query-padding.md。

import polars as pl  # noqa: E402

from qsys.data.pipeline.candidates import drop_suspended  # noqa: E402

DAYS = pl.DataFrame({"symbol": ["600000.SH", "600001.SH"],
                     "dt": [date(2026, 6, 15), date(2026, 6, 15)]})


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def suspensions(self, symbols, start, end):
        return pl.DataFrame(self._rows,
                            schema={"symbol": pl.Utf8, "dt": pl.Date,
                                    "suspend_type": pl.Utf8, "suspend_timing": pl.Utf8},
                            orient="row")


def test_suspended_symbol_day_is_dropped():
    q = _Query([("600000.SH", date(2026, 6, 15), "S", None)])
    assert drop_suspended(q, DAYS)["symbol"].to_list() == ["600001.SH"]


def test_intraday_suspension_is_not_dropped():
    q = _Query([("600000.SH", date(2026, 6, 15), "S", "9:30-9:40")])
    assert sorted(drop_suspended(q, DAYS)["symbol"].to_list()) == [
        "600000.SH", "600001.SH"
    ]


def test_resumption_rows_are_not_dropped():
    """suspend_type='R' 是复牌，当天照常交易，不能扣。"""
    q = _Query([("600000.SH", date(2026, 6, 15), "R", None)])
    assert sorted(drop_suspended(q, DAYS)["symbol"].to_list()) == ["600000.SH", "600001.SH"]


def test_same_day_resumption_prevents_an_all_day_drop():
    q = _Query([
        ("600000.SH", date(2026, 6, 15), "S", None),
        ("600000.SH", date(2026, 6, 15), "R", None),
    ])
    assert sorted(drop_suspended(q, DAYS)["symbol"].to_list()) == [
        "600000.SH", "600001.SH",
    ]


def test_suspension_on_another_day_does_not_leak():
    q = _Query([("600000.SH", date(2026, 6, 12), "S", None)])
    assert sorted(drop_suspended(q, DAYS)["symbol"].to_list()) == ["600000.SH", "600001.SH"]


def test_empty_input_returns_empty():
    assert drop_suspended(_Query([]), DAYS.clear()).height == 0
