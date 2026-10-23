"""迁移后新交易日的直接SQL消费者，不能误读保留的旧库。"""

import sqlite3
from datetime import datetime, time
from pathlib import Path
from types import SimpleNamespace

import pytest

from qsys.live import state_binding
from qsys.ops.daily_digest_cli import strategy_runs
from qsys.ops.health import DOWN, HEALTHY
from qsys.ops.service_checks import OK, _shadow_events
from qsys.ops.trading_chain import assess_shadow, recorded_auction_time


def database(path, day, auction="09:25:10", status="completed"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE live_events(event_key TEXT,phase TEXT,status TEXT,detail TEXT)")
        con.execute("CREATE TABLE live_strategy_runs(event_key TEXT,session TEXT,phase TEXT,"
                    "summary TEXT,orders INT,rejects INT,error TEXT)")
        for phase, at in [("session_start", "09:15:00"), ("auction", auction)]:
            key = f"{day}:{phase}:{at}"
            con.execute("INSERT INTO live_events VALUES (?,?,?,?)",
                        (key, phase, status, "" if status == "completed" else "actual failure"))
            con.execute("INSERT INTO live_strategy_runs VALUES (?,?,?,?,0,0,'')",
                        (key, day, phase, f"source:{day}"))


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    old = Path("var/live-state.sqlite3")
    bound = state_binding.LiveStateBinding(tmp_path / "private")
    database(old, "2026-10-22", "09:30:00")
    database(bound.state, "2026-10-23")
    selected = [None]
    monkeypatch.setattr(state_binding, "current_binding", lambda: selected[0])
    return old, bound, selected


def test_existing_sql_readers_follow_binding_for_new_session(migrated, tmp_path):
    old, bound, selected = migrated
    now = datetime(2026, 10, 23, 9, 26)
    assert assess_shadow(old, now).state == DOWN
    assert recorded_auction_time(old) == time(9, 30)
    selected[0] = bound
    assert recorded_auction_time(old) == time(9, 25, 10)
    assessment = assess_shadow(old, now, auction_at=recorded_auction_time(old))
    assert assessment.state == HEALTHY and assessment.completed == 2
    runs = strategy_runs(old, tmp_path / "absent-workbench", now.date(), core_name="实盘")
    assert len(runs) == 2 and {r["summary"] for r in runs} == {"source:2026-10-23"}
    result = _shadow_events(SimpleNamespace(shadow_state=old, day=now.date()))
    assert result.status == OK and result.rows == 2


def test_binding_preserves_real_failure_and_custom_state(migrated, tmp_path):
    old, bound, selected = migrated
    selected[0] = bound
    with sqlite3.connect(bound.state) as con:
        con.execute("UPDATE live_events SET status='failed',detail='real error' WHERE phase='auction'")
    now = datetime(2026, 10, 23, 9, 26)
    failed = assess_shadow(old, now, auction_at=recorded_auction_time(old))
    assert failed.state == DOWN and "real error" in failed.detail
    custom = tmp_path / "custom-shadow.sqlite3"
    database(custom, "2026-10-23", "09:30:00")
    assert recorded_auction_time(custom) == time(9, 30)
    assert assess_shadow(custom, datetime(2026, 10, 23, 9, 31), auction_at=time(9, 30)).state == HEALTHY


def test_broken_binding_does_not_read_old_state(migrated, monkeypatch):
    old, _, _ = migrated
    monkeypatch.setattr(state_binding, "current_binding",
                        lambda: (_ for _ in ()).throw(ValueError("invalid binding")))
    result = assess_shadow(old, datetime(2026, 10, 23, 9, 26))
    assert result.state == DOWN and result.reason_code == "strategy_state_unavailable"
