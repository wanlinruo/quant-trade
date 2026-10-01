"""日报投递的取数部分。

汇报内容本身在 `tests/ops/test_digest.py`（纯函数）；这里守的是「报哪一天」
和「休市日怎么算」——两个都是 2026-10-01 国庆期间踩出来的。
"""

from datetime import date
from pathlib import Path

from scripts.send_daily_digest import EXPECTED_SERVICES, checks_and_missing

EMPTY = Path("/nonexistent/service-runs")
DAY = date(2026, 10, 1)


def test_a_trading_day_names_every_job_that_left_no_record():
    _, missing = checks_and_missing(EMPTY, DAY, True)

    assert missing == list(EXPECTED_SERVICES)


def test_a_closed_day_does_not_expect_those_jobs_to_have_run():
    """休市日盘前取价、归档、同步、验证、质检本来就不排。

    照交易日的清单去点名，会在国庆七天里天天报五项「没有运行记录」。
    """
    _, missing = checks_and_missing(EMPTY, DAY, False)

    assert missing == []


def test_the_digest_reports_today_not_the_last_closed_session():
    """定时器按 Mon..Fri 触发、不认节假日；取「最近已收盘交易日」会在假期里
    把节前那封日报原样重发（国庆能发五遍）。"""
    source = Path("scripts/send_daily_digest.py").read_text(encoding="utf-8")

    assert "last_closed_session" not in source
    assert "datetime.now().date()" in source


# ---- 连续零错误：不用手填起点，每天都能重算 ----

import sqlite3  # noqa: E402

from scripts.send_daily_digest import clean_streak  # noqa: E402


def _runs(tmp_path, rows):
    path = tmp_path / "state.sqlite3"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE live_strategy_runs (session TEXT, error TEXT)")
        con.executemany("INSERT INTO live_strategy_runs VALUES (?, ?)", rows)
    return path


def test_the_streak_counts_back_to_the_last_bad_day(tmp_path):
    path = _runs(tmp_path, [
        ("2026-09-25", "reconcile: INCONSISTENT"),
        ("2026-09-28", None), ("2026-09-29", None), ("2026-09-30", None),
    ])

    assert clean_streak(path, "live_strategy_runs", date(2026, 9, 30)) == 3


def test_a_bad_day_today_resets_it_to_zero(tmp_path):
    path = _runs(tmp_path, [("2026-09-29", None), ("2026-09-30", "炸了")])

    assert clean_streak(path, "live_strategy_runs", date(2026, 9, 30)) == 0


def test_one_bad_event_spoils_the_whole_day(tmp_path):
    """同一天里只要有一条出错，这天就不算干净。"""
    path = _runs(tmp_path, [("2026-09-30", None), ("2026-09-30", "炸了")])

    assert clean_streak(path, "live_strategy_runs", date(2026, 9, 30)) == 0


def test_days_after_the_reporting_day_do_not_count(tmp_path):
    """补发历史日报时不能把后面的日子算进去。"""
    path = _runs(tmp_path, [("2026-09-29", None), ("2026-09-30", None)])

    assert clean_streak(path, "live_strategy_runs", date(2026, 9, 29)) == 1


def test_a_missing_state_database_is_a_zero_streak(tmp_path):
    assert clean_streak(tmp_path / "absent", "live_strategy_runs", DAY) == 0


# ---- systemd 时间戳（2026-10-01 首次实跑，日报上显示成 11:09:0）----

from scripts.send_daily_digest import _since  # noqa: E402


def test_a_systemd_timestamp_is_trimmed_to_the_minute():
    assert _since("Wed 2026-09-30 11:09:00 CST") == "2026-09-30 11:09"


def test_an_absent_timestamp_is_blank_not_a_crash():
    assert _since(None) == "" and _since("") == "" and _since("n/a") == ""


# ---- 运行记录按「它检查的那一天」归属，不按文件落在哪天 ----

import json  # noqa: E402


def _write_run(root: Path, started: str, session: str | None, steps):
    day = started[:10].replace("-", "/")
    folder = root / day
    folder.mkdir(parents=True, exist_ok=True)
    run = {"service": "service-checks", "started_at": started, "steps": steps}
    if session is not None:
        run["session"] = session
    (folder / f"service-checks-{started[11:].replace(':', '')}.json").write_text(
        json.dumps(run), encoding="utf-8")


def test_a_recheck_of_an_older_day_does_not_pollute_todays_digest(tmp_path):
    """在 10-01 复查 09-30，那次执行属于 09-30。

    按文件日期取会让休市日的日报一边说「不安排验证」、一边报出 09-30 的
    验证结论——同一封信自相矛盾（2026-10-01 实际发生）。
    """
    _write_run(tmp_path, "2026-10-01T20:00:00", "2026-09-30",
               [{"name": "catalog-verify｜阻断级规则全部通过", "status": "warning",
                 "detail": "1 条规则未通过"}])

    checks, _ = checks_and_missing(tmp_path, date(2026, 10, 1), False)

    assert checks == []


def test_a_next_morning_catch_up_counts_for_the_day_it_checked(tmp_path):
    """次日补跑的活属于它补的那个交易日，不属于启动那天。"""
    _write_run(tmp_path, "2026-10-02T00:30:00", "2026-10-01",
               [{"name": "tushare-sync｜日线到货", "status": "succeeded", "detail": "到齐"}])

    checks, _ = checks_and_missing(tmp_path, date(2026, 10, 1), True)

    assert [c["name"] for c in checks] == ["tushare-sync｜日线到货"]


def test_a_run_without_a_session_falls_back_to_its_start_date(tmp_path):
    _write_run(tmp_path, "2026-10-01T20:00:00", None,
               [{"name": "甲", "status": "succeeded", "detail": ""}])

    checks, _ = checks_and_missing(tmp_path, date(2026, 10, 1), False)

    assert [c["name"] for c in checks] == ["甲"]
