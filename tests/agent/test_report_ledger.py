from qsys.agent.report_ledger import SQLiteReportLedger


def test_sqlite_ledger_survives_restart(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    first = SQLiteReportLedger(path)
    first.remember_trade("T1")
    first.remember_closed("c-1")
    first.remember_order("c-1", "B1")
    first.remember_status("c-1", "filled:100:0")

    restored = SQLiteReportLedger(path)
    assert restored.seen_trade_ids() == frozenset({"T1"})
    assert restored.closed_order_ids() == frozenset({"c-1"})
    assert restored.broker_order_id("c-1") == "B1"
    assert restored.last_status("c-1") == "filled:100:0"
