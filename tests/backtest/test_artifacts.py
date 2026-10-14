"""回测落盘：文件名与列名要能直接与聚宽的导出对上。"""

import json
import zipfile
from datetime import date, datetime

import polars as pl
import pytest

from qsys.backtest.artifacts import (
    ClosedLot,
    closed_lots,
    to_joinquant_code,
    write_run,
)
from qsys.core.events import Reject
from qsys.core.trading import Fill, Side
from qsys.core.types import Symbol

D = [date(2024, 1, d) for d in (2, 3, 4, 5)]
SYM = Symbol.parse("600001.SH")


def fill(day, side, qty, price, symbol=SYM, hour=9):
    return Fill(
        order_id="o1", symbol=symbol, side=side, qty=qty, price=price,
        fee=1.0, filled_at=datetime.combine(day, datetime.min.time()).replace(hour=hour),
    )


class FakeResult:
    def __init__(self, fills=(), rejects=(), equity=None, state=None):
        self.fills = tuple(fills)
        self.rejects = tuple(rejects)
        self.run_id = "abc123"
        self.params = {}
        self.state = state or {}
        self.equity = equity if equity is not None else pl.DataFrame(
            {"dt": D[:2], "cash": [100.0, 90.0],
             "market_value": [0.0, 10.0], "total_value": [100.0, 100.0]},
            schema={"dt": pl.Date, "cash": pl.Float64,
                    "market_value": pl.Float64, "total_value": pl.Float64},
        )

    @property
    def final_value(self):
        # 与真的 BacktestResult 一样：空净值返回 0.0 而不是抛
        if self.equity.height == 0:
            return 0.0
        return float(self.equity["total_value"].to_list()[-1])


# ---- 代码格式 ----

@pytest.mark.parametrize("ours,theirs", [
    ("002139.SZ", "002139.XSHE"),
    ("600036.SH", "600036.XSHG"),
])
def test_codes_convert_to_the_joinquant_form(ours, theirs):
    assert to_joinquant_code(ours) == theirs


def test_an_unknown_market_is_left_alone():
    assert to_joinquant_code("XYZ") == "XYZ"


# ---- lot 配对 ----

def test_a_buy_and_a_sell_become_one_lot():
    lots = closed_lots(
        [fill(D[0], Side.BUY, 100, 10.0), fill(D[1], Side.SELL, 100, 11.0)],
        {"600001.SH": "HIGH_MAIN"}, D,
    )
    assert len(lots) == 1
    lot = lots[0]
    assert lot.strategy_tag == "HIGH_MAIN"
    assert lot.return_pct == pytest.approx(10.0)
    assert lot.realized_pnl_gross == pytest.approx(100.0)
    assert lot.holding_trade_days == 1
    assert lot.entry_notional == pytest.approx(1000.0)


def test_a_partial_sell_leaves_the_rest_open():
    lots = closed_lots(
        [fill(D[0], Side.BUY, 300, 10.0), fill(D[1], Side.SELL, 100, 11.0)],
        {}, D,
    )
    assert [x.qty for x in lots] == [100]


def test_two_buys_are_matched_first_in_first_out():
    lots = closed_lots(
        [fill(D[0], Side.BUY, 100, 10.0), fill(D[1], Side.BUY, 100, 20.0),
         fill(D[2], Side.SELL, 200, 30.0)],
        {}, D,
    )
    assert [x.entry_price for x in lots] == [10.0, 20.0]
    assert [x.holding_trade_days for x in lots] == [2, 1]


def test_a_sell_without_a_matching_buy_is_dropped_not_crashed():
    assert closed_lots([fill(D[1], Side.SELL, 100, 11.0)], {}, D) == []


def test_holding_days_fall_back_to_calendar_days_off_calendar():
    lots = closed_lots(
        [fill(D[0], Side.BUY, 100, 10.0), fill(D[2], Side.SELL, 100, 11.0)],
        {}, sessions=(),
    )
    assert lots[0].holding_trade_days == 2


# ---- 落盘 ----

def _write(tmp_path, **kw):
    result = kw.pop("result", None) or FakeResult(
        fills=[fill(D[0], Side.BUY, 100, 10.0), fill(D[1], Side.SELL, 100, 11.0)],
        state={"tags": {"600001.SH": "HIGH_MAIN"}},
    )
    return write_run(
        result, tmp_path / "run1",
        strategy=kw.pop("strategy", "StructuralStrategy"),
        start=D[0], end=D[-1], initial_cash=100_000.0,
        config=kw.pop("config", {"cost": "jq-compat"}),
        sessions=D, **kw,
    )


def test_the_run_directory_holds_the_joinquant_file_set(tmp_path):
    run = _write(tmp_path)
    names = {p.name for p in run.glob("*")}
    for expected in ("daily_nav.csv", "daily_buy_details.csv", "closed_lots.csv",
                     "gate_decisions.csv", "run_status.csv", "branch_summary.csv"):
        assert expected in names, expected


def test_our_own_extras_are_written_too(tmp_path):
    names = {p.name for p in _write(tmp_path).glob("*")}
    assert {"fills.csv", "rejects.csv", "orders.csv", "daily_positions.csv",
            "monthly_returns.csv", "candidate_funnel.csv", "run.log",
            "metrics.csv", "config.json"} <= names


def test_nav_uses_the_joinquant_column_names(tmp_path):
    nav = pl.read_csv(_write(tmp_path) / "daily_nav.csv")
    assert nav.columns == ["date", "total_value", "available_cash",
                           "position_count", "daily_return"]


def test_buy_details_carry_both_code_formats(tmp_path):
    rows = pl.read_csv(_write(tmp_path) / "daily_buy_details.csv")
    assert rows["stock"].to_list() == ["600001.XSHG"]
    assert rows["symbol"].to_list() == ["600001.SH"]
    assert rows["strategy_tag"].to_list() == ["HIGH_MAIN"]


def test_the_same_stock_bought_twice_in_a_day_is_one_row(tmp_path):
    result = FakeResult(
        fills=[fill(D[0], Side.BUY, 100, 10.0, hour=9),
               fill(D[0], Side.BUY, 100, 10.5, hour=10)],
        state={"tags": {"600001.SH": "LOW_MAIN"}},
    )
    rows = pl.read_csv(_write(tmp_path, result=result) / "daily_buy_details.csv")
    assert rows.height == 1


def test_position_count_tracks_open_positions(tmp_path):
    nav = pl.read_csv(_write(tmp_path) / "daily_nav.csv")
    assert nav["position_count"].to_list() == [1, 0]


def test_rejects_record_the_reason(tmp_path):
    result = FakeResult(rejects=[Reject(
        order_id="o9", symbol=SYM, side=Side.BUY, qty=100,
        reason="volume cap 5%", at=datetime(2024, 1, 2, 9, 30),
    )])
    rows = pl.read_csv(_write(tmp_path, result=result) / "rejects.csv")
    assert rows["reason"].to_list() == ["volume cap 5%"]
    assert rows["order_id"].to_list() == ["o9"]


def test_config_records_what_the_run_was(tmp_path):
    cfg = json.loads((_write(tmp_path) / "config.json").read_text(encoding="utf-8"))
    assert cfg["run_id"] == "abc123"
    assert cfg["initial_cash"] == 100_000.0
    assert cfg["cost"] == "jq-compat"


def test_status_summarises_the_run(tmp_path):
    row = pl.read_csv(_write(tmp_path) / "run_status.csv").to_dicts()[0]
    assert row["buys"] == 1 and row["sells"] == 1
    assert row["closed_lots"] == 1
    assert row["cumulative_return"] == pytest.approx(-0.999)


def test_a_zip_of_everything_is_written(tmp_path):
    run = _write(tmp_path)
    archive = run / "run1.zip"
    assert archive.exists()
    with zipfile.ZipFile(archive) as zf:
        assert "daily_nav.csv" in zf.namelist()
        # utf-8-sig 让 Excel 直接打开不乱码
        assert zf.read("daily_nav.csv").startswith(b"\xef\xbb\xbf")


def test_regime_is_renamed_to_the_joinquant_column(tmp_path):
    regime = pl.DataFrame(
        {"dt": D[:2], "valid_stock_count": [4500, 4501],
         "market_advance_ratio": [0.5, 0.6]},
        schema={"dt": pl.Date, "valid_stock_count": pl.Int64,
                "market_advance_ratio": pl.Float64},
    )
    got = pl.read_csv(_write(tmp_path, regime=regime) / "regime_daily.csv")
    assert "source_market_date" in got.columns
    assert "market_valid_stock_count" in got.columns


def test_an_empty_run_still_writes_every_file(tmp_path):
    empty = FakeResult(equity=pl.DataFrame(
        schema={"dt": pl.Date, "cash": pl.Float64,
                "market_value": pl.Float64, "total_value": pl.Float64}))
    run = _write(tmp_path, result=empty)
    assert (run / "daily_nav.csv").exists()
    assert pl.read_csv(run / "run_status.csv").height == 1
