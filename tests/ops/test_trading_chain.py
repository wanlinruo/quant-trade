import sqlite3
from datetime import datetime

import pytest

from qsys.ops.health import DEGRADED, DOWN, HEALTHY
from qsys.ops.trading_chain import assess_paper, assess_shadow, recorded_auction_time


DAY = "2026-09-21"


def shadow_db(path, events=(), runs=()):
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE live_events (event_key TEXT, status TEXT, detail TEXT)")
        con.execute("CREATE TABLE live_strategy_runs (event_key TEXT, session TEXT, error TEXT)")
        con.executemany("INSERT INTO live_events VALUES (?,?,?)", events)
        con.executemany("INSERT INTO live_strategy_runs VALUES (?,?,?)", runs)
    return path


def paper_db(path, events=(), runs=(), accounts=None):
    """accounts: (id, 名称, config_json)；不给就是一个不设 auction_at 的账户。"""
    accounts = accounts or [("a", "模拟一号", None)]
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE paper_accounts (id TEXT,name TEXT,status TEXT,"
                    "activation_date TEXT,config_json TEXT)")
        con.execute("CREATE TABLE paper_events (account_id TEXT,event_key TEXT,status TEXT,detail TEXT)")
        con.execute("CREATE TABLE paper_strategy_runs (account_id TEXT,event_key TEXT,session TEXT,error TEXT)")
        con.executemany(
            "INSERT INTO paper_accounts VALUES (?,?,'running','2026-09-21',?)",
            [(i, n, c) for i, n, c in accounts],
        )
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


@pytest.mark.parametrize("hour", [0, 9, 21])
@pytest.mark.parametrize("with_running", [False, True])
def test_paper_error_accounts_are_unhealthy_even_with_no_running_accounts(
    tmp_path, hour, with_running,
):
    path = paper_db(tmp_path / "paper.sqlite3", accounts=[
        ("failed", "失败账户", None), ("other", "另一个账户", None),
    ])
    with sqlite3.connect(path) as con:
        con.execute("UPDATE paper_accounts SET status='error' WHERE id='failed'")
        if not with_running:
            con.execute("UPDATE paper_accounts SET status='ended' WHERE id='other'")
    before = path.read_bytes()

    got = assess_paper(path, datetime(2026, 9, 21, hour))

    assert got.state == DOWN and got.reason_code == "strategy_account_error"
    assert "1 个" in got.detail
    assert path.read_bytes() == before


@pytest.mark.parametrize("posture", ["paused", "ended", "scheduled", "future-error", "empty"])
def test_paper_inactive_accounts_do_not_become_false_business_failures(tmp_path, posture):
    path = paper_db(tmp_path / "paper.sqlite3")
    with sqlite3.connect(path) as con:
        if posture == "empty":
            con.execute("DELETE FROM paper_accounts")
        elif posture == "future-error":
            con.execute("UPDATE paper_accounts SET status='error',activation_date='2026-09-22'")
        else:
            con.execute("UPDATE paper_accounts SET status=?", (posture,))

    got = assess_paper(path, datetime(2026, 9, 21, 21))

    assert got.state == HEALTHY and got.reason_code == "strategy_accounts_inactive"



# ---- 竞价时刻是参数，不是常量（2026-09-28 误报）----

EARLY = '{"params": {"auction_at": "09:25:10"}}'


def test_an_account_that_decides_at_0925_is_not_reported_as_failed(tmp_path):
    """09-28 的误报：账户按 09:25:10 跑完了，监控却还在等 09:30:00。

    整整一上午「每日业务验收」都显示事件失败，而策略三套引擎跑得完全正确。
    """
    start = f"{DAY}:session_start:09:15:00"
    auction = f"{DAY}:auction:09:25:10"
    path = paper_db(
        tmp_path / "paper.sqlite3",
        [("a", start, "completed", ""), ("a", auction, "completed", "")],
        accounts=[("a", "09:25入场", EARLY)],
    )

    got = assess_paper(path, datetime(2026, 9, 21, 9, 31, 0))

    assert got.state == HEALTHY, got.detail


def test_an_early_auction_that_really_is_missing_still_fails(tmp_path):
    """跟着参数走，不等于放宽：09:25:10 过了还没落库，照样要报。"""
    start = f"{DAY}:session_start:09:15:00"
    path = paper_db(
        tmp_path / "paper.sqlite3",
        [("a", start, "completed", "")],
        accounts=[("a", "09:25入场", EARLY)],
    )

    got = assess_paper(path, datetime(2026, 9, 21, 9, 27, 0))

    assert got.state == DOWN and "09:25:10" in got.detail


def test_accounts_with_different_auction_times_are_judged_separately(tmp_path):
    """09-28 现场就是这样：旧账户 09:30 默认、新账户 09:25:10，两个同时在跑。"""
    start = f"{DAY}:session_start:09:15:00"
    path = paper_db(
        tmp_path / "paper.sqlite3",
        [("old", start, "completed", ""), ("old", f"{DAY}:auction:09:30:00", "completed", ""),
         ("new", start, "completed", ""), ("new", f"{DAY}:auction:09:25:10", "completed", "")],
        accounts=[("old", "旧账户", None), ("new", "09:25入场", EARLY)],
    )

    got = assess_paper(path, datetime(2026, 9, 21, 9, 31, 0))

    assert got.state == HEALTHY, got.detail
    assert got.expected == 4 and got.completed == 4


def test_the_shadow_core_follows_the_account_it_inherits_from(tmp_path):
    """影子核心不自己填参数，监控的预期时刻也得问同一个账户。"""
    start = f"{DAY}:session_start:09:15:00"
    from datetime import time as _time

    path = shadow_db(
        tmp_path / "shadow.sqlite3",
        [(start, "completed", ""), (f"{DAY}:auction:09:25:10", "completed", "")],
    )

    ok = assess_shadow(path, datetime(2026, 9, 21, 9, 31, 0), auction_at=_time(9, 25, 10))
    stale = assess_shadow(path, datetime(2026, 9, 21, 9, 31, 0))

    assert ok.state == HEALTHY, ok.detail
    # 不传参数就退回 09:30，正是修复前的错误行为——留着这条说明差别从哪来
    assert stale.state == DOWN and "09:30:00" in stale.detail


def test_live_core_monitor_uses_the_auction_time_recorded_by_runtime(tmp_path):
    from datetime import time as _time

    path = shadow_db(tmp_path / "live.sqlite3", [
        (f"{DAY}:auction:09:25:10", "completed", ""),
    ])

    assert recorded_auction_time(path) == _time(9, 25, 10)


def test_live_core_monitor_falls_back_if_no_auction_was_recorded(tmp_path):
    from qsys.ops.trading_chain import DEFAULT_AUCTION

    path = shadow_db(tmp_path / "new-live.sqlite3")

    assert recorded_auction_time(path) == DEFAULT_AUCTION


def test_unreadable_params_fall_back_to_the_default_instead_of_failing(tmp_path):
    """监控读不懂参数时退回默认值，不能因为解析失败就把整条链判死。"""
    from qsys.ops.trading_chain import DEFAULT_AUCTION, account_auction_time

    assert account_auction_time(None) == DEFAULT_AUCTION
    assert account_auction_time("这不是 json") == DEFAULT_AUCTION
    assert account_auction_time('{"params": {"auction_at": "09:00"}}') == DEFAULT_AUCTION
