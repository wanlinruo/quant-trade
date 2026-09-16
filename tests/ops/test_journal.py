import pyarrow as pa

from qsys.data import schema


def test_live_tables_are_registered():
    for name in ("live_equity", "live_fills", "live_rejects", "live_recon"):
        assert name in schema.ALL, f"{name} 未注册"
        assert name in schema.PARTITIONED, f"{name} 应按年分区"


def test_live_equity_columns():
    assert [f.name for f in schema.LIVE_EQUITY] == [
        "dt", "cash", "market_value", "total_value",
    ]


def test_live_fills_columns():
    assert [f.name for f in schema.LIVE_FILLS] == [
        "dt", "client_order_id", "broker_order_id", "broker_trade_id",
        "symbol", "side", "qty", "price", "fee", "filled_at",
    ]


def test_live_rejects_columns():
    assert [f.name for f in schema.LIVE_REJECTS] == [
        "dt", "client_order_id", "symbol", "qty", "reason", "at",
    ]


def test_live_recon_columns():
    assert [f.name for f in schema.LIVE_RECON] == [
        "dt", "at", "layer", "is_consistent", "cash_diff", "n_mismatches", "detail",
    ]


def test_live_equity_types():
    assert schema.LIVE_EQUITY.field("dt").type == pa.date32()
    assert schema.LIVE_EQUITY.field("total_value").type == pa.float64()


def test_recon_layer_is_a_string_tag():
    """layer 取值：intraday / eod / sim_vs_live。"""
    assert schema.LIVE_RECON.field("layer").type == pa.string()


# --- SessionJournal ---

from datetime import date, datetime

import pytest

from qsys.core.events import Reject
from qsys.core.trading import Fill, Side
from qsys.core.types import Market, Symbol
from qsys.ops.journal import SessionJournal

CMB = Symbol("600036", Market.SH)
D = date(2024, 1, 3)
T = datetime(2024, 1, 3, 9, 30)


@pytest.fixture
def journal(warehouse) -> SessionJournal:
    return SessionJournal(warehouse)


def fill(oid="c-1", qty=100, price=10.0) -> Fill:
    return Fill(oid, CMB, Side.BUY, qty, price, 5.01, T)


def reject(oid="c-1", reason="limit up") -> Reject:
    return Reject(oid, CMB, Side.BUY, 100, reason, T)


def test_record_equity_then_read_back(journal):
    journal.record_equity(D, cash=98_000.0, market_value=12_000.0)
    df = journal.equity()
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["cash"] == 98_000.0
    assert row["total_value"] == 110_000.0


def test_equity_is_idempotent_per_session(journal):
    """盘中多次记录同一天，只保留最后一次。"""
    journal.record_equity(D, cash=98_000.0, market_value=12_000.0)
    journal.record_equity(D, cash=97_000.0, market_value=13_000.0)
    df = journal.equity()
    assert df.height == 1
    assert df.row(0, named=True)["cash"] == 97_000.0


def test_equity_range_filter(journal):
    journal.record_equity(date(2024, 1, 2), cash=1.0, market_value=0.0)
    journal.record_equity(date(2024, 1, 3), cash=2.0, market_value=0.0)
    journal.record_equity(date(2024, 1, 4), cash=3.0, market_value=0.0)
    df = journal.equity(start=date(2024, 1, 3), end=date(2024, 1, 3))
    assert df["cash"].to_list() == [2.0]


def test_equity_is_sorted_by_date(journal):
    journal.record_equity(date(2024, 1, 4), cash=3.0, market_value=0.0)
    journal.record_equity(date(2024, 1, 2), cash=1.0, market_value=0.0)
    assert journal.equity()["cash"].to_list() == [1.0, 3.0]


def test_empty_equity_returns_typed_frame(journal):
    df = journal.equity()
    assert df.height == 0
    assert df.columns == ["dt", "cash", "market_value", "total_value"]


def test_record_fills_then_read_back(journal):
    journal.record_fills(D, [fill("a"), fill("b")])
    df = journal.fills(D)
    assert df.height == 2
    assert sorted(df["client_order_id"].to_list()) == ["a", "b"]


def test_fills_are_idempotent_by_client_order_id(journal):
    journal.record_fills(D, [fill("a")])
    journal.record_fills(D, [fill("a"), fill("b")])
    assert journal.fills(D).height == 2


def test_fills_of_another_session_are_excluded(journal):
    journal.record_fills(D, [fill("a")])
    journal.record_fills(date(2024, 1, 4), [fill("b")])
    assert journal.fills(D)["client_order_id"].to_list() == ["a"]


def test_recording_no_fills_is_a_no_op(journal):
    journal.record_fills(D, [])
    assert journal.fills(D).height == 0


def test_record_rejects_then_read_back(journal):
    journal.record_rejects(D, [reject("a", "limit up"), reject("b", "suspended")])
    df = journal.rejects(D)
    assert df.height == 2
    assert "limit up" in df["reason"].to_list()


def test_record_reconciliation(journal):
    journal.record_reconciliation(D, "intraday", is_consistent=True, at=T)
    df = journal.reconciliations(D)
    assert df.height == 1
    assert df.row(0, named=True)["layer"] == "intraday"
    assert df.row(0, named=True)["is_consistent"] is True


def test_reconciliation_records_mismatch_detail(journal):
    journal.record_reconciliation(
        D, "eod", is_consistent=False, cash_diff=1000.0, n_mismatches=2,
        detail="600036.SH: local=500 remote=400", at=T,
    )
    row = journal.reconciliations(D).row(0, named=True)
    assert row["is_consistent"] is False
    assert row["cash_diff"] == 1000.0
    assert row["n_mismatches"] == 2
    assert "600036.SH" in row["detail"]


def test_three_layers_coexist_in_one_session(journal):
    for layer in ("intraday", "eod", "sim_vs_live"):
        journal.record_reconciliation(D, layer, is_consistent=True, at=T)
    assert sorted(journal.reconciliations(D)["layer"].to_list()) == [
        "eod", "intraday", "sim_vs_live",
    ]


def test_repeated_intraday_reconciliation_keeps_every_run(journal):
    """盘中对账每 N 分钟一次，每次都要留痕——按时刻区分，不去重。"""
    journal.record_reconciliation(D, "intraday", is_consistent=True,
                                  at=datetime(2024, 1, 3, 10, 0))
    journal.record_reconciliation(D, "intraday", is_consistent=False,
                                  at=datetime(2024, 1, 3, 10, 5))
    assert journal.reconciliations(D).height == 2


def test_sessions_lists_recorded_days(journal):
    journal.record_equity(date(2024, 1, 2), cash=1.0, market_value=0.0)
    journal.record_equity(date(2024, 1, 4), cash=1.0, market_value=0.0)
    assert journal.sessions() == [date(2024, 1, 2), date(2024, 1, 4)]


def test_sessions_is_empty_on_a_fresh_journal(journal):
    assert journal.sessions() == []
