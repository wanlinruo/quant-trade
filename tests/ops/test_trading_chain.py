import sqlite3
from datetime import datetime

from qsys.ops.health import DEGRADED, DOWN, HEALTHY
from qsys.ops.trading_chain import assess_paper, assess_shadow


DAY = "2026-09-21"


def shadow_db(path, events=(), runs=()):
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE live_events (event_key TEXT, status TEXT, detail TEXT)")
        con.execute("CREATE TABLE live_strategy_runs (event_key TEXT, session TEXT, error TEXT)")
        con.executemany("INSERT INTO live_events VALUES (?,?,?)", events)
        con.executemany("INSERT INTO live_strategy_runs VALUES (?,?,?)", runs)
    return path


def paper_db(path, events=(), runs=()):
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE paper_accounts (id TEXT,name TEXT,status TEXT,activation_date TEXT)")
        con.execute("CREATE TABLE paper_events (account_id TEXT,event_key TEXT,status TEXT,detail TEXT)")
        con.execute("CREATE TABLE paper_strategy_runs (account_id TEXT,event_key TEXT,session TEXT,error TEXT)")
        con.execute("INSERT INTO paper_accounts VALUES ('a','模拟一号','running','2026-09-21')")
        con.executemany("INSERT INTO paper_events VALUES (?,?,?,?)", events)
        con.executemany("INSERT INTO paper_strategy_runs VALUES (?,?,?,?)", runs)
    return path


def test_shadow_chain_warns_then_fails_when_a_due_event_is_missing(tmp_path):
    key = f"{DAY}:session_start:09:15:00"
    path = shadow_db(tmp_path / "shadow.sqlite3", [(key, "completed", "")])
    assert assess_shadow(path, datetime(2026, 9, 21, 9, 30, 9)).state == HEALTHY
    delayed = assess_shadow(path, datetime(2026, 9, 21, 9, 30, 15))
    assert delayed.state == DEGRADED and "auction" in delayed.detail
    failed = assess_shadow(path, datetime(2026, 9, 21, 9, 30, 31))
    assert failed.state == DOWN and failed.reason_code == "strategy_event_failed"


def test_shadow_chain_fails_immediately_on_recorded_attempt_error(tmp_path):
    start = f"{DAY}:session_start:09:15:00"
    auction = f"{DAY}:auction:09:30:00"
    path = shadow_db(
        tmp_path / "shadow.sqlite3",
        [(start, "completed", ""), (auction, "running", "QMT 行情超时")],
        [(auction, DAY, "QMT 行情超时")],
    )
    got = assess_shadow(path, datetime(2026, 9, 21, 9, 30, 15))
    assert got.state == DOWN and "QMT 行情超时" in got.detail


def test_paper_chain_checks_every_active_account_and_run_error(tmp_path):
    start = f"{DAY}:session_start:09:15:00"
    auction = f"{DAY}:auction:09:30:00"
    path = paper_db(
        tmp_path / "paper.sqlite3",
        [("a", start, "completed", ""), ("a", auction, "completed", "")],
        [("a", auction, DAY, "行情响应不完整")],
    )
    got = assess_paper(path, datetime(2026, 9, 21, 9, 30, 15))
    assert got.state == DOWN and "模拟一号" in got.detail

