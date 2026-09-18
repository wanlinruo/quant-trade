"""服务质检：期望值只取直取源与运行记录；缺什么要说出具体缺哪些。"""

from datetime import date, datetime, time

import polars as pl
import pytest

from qsys.ops import service_checks as sc
from qsys.ops.service_checks import CheckContext

DAY, PREV = date(2026, 9, 17), date(2026, 9, 16)
SESSIONS = [date(2026, 9, d) for d in (10, 11, 14, 15, 16, 17)]


class FakeQuery:
    def __init__(self, **frames):
        self.frames = frames

    def _dated(self, name, day):
        frame = self.frames.get(name, pl.DataFrame(schema={"symbol": pl.Utf8, "dt": pl.Date}))
        return frame.filter(pl.col("dt") == day)

    def tushare_daily(self, symbols, start, end, *, columns=None):
        return self._dated("daily", start)

    def price_limits(self, symbols, start, end):
        return self._dated("limits", start)

    def stock_st(self, symbols, start, end):
        return self._dated("st", start)

    def suspensions(self, symbols, start, end):
        frame = self.frames.get("suspend", pl.DataFrame(
            schema={"symbol": pl.Utf8, "dt": pl.Date, "suspend_type": pl.Utf8}))
        return frame.filter(pl.col("dt") == start)

    def limit_list(self, symbols, start, end):
        return self._dated("limit_list", start)

    def daily_flags(self, start, end):
        return self._dated("flags", start)

    def valuation(self, start, end):
        return self._dated("valuation", start)

    def name_history(self):
        return self.frames.get("history", pl.DataFrame(schema={
            "symbol": pl.Utf8, "change_reason": pl.Utf8, "start_date": pl.Date, "end_date": pl.Date}))

    def instruments(self):
        return self.frames.get("instruments", pl.DataFrame(
            schema={"symbol": pl.Utf8, "delist_date": pl.Date}))

    def delisting_pool(self, start, end):
        return self._dated("pool", start)

    def metric_checks(self):
        return self.frames.get("metric_checks", pl.DataFrame())

    def minute_bars(self, symbols, start, end):
        frame = self.frames["minutes"]
        return frame.filter(pl.col("symbol").is_in(symbols)
                            & (pl.col("dt").dt.date() == start.date()))


def daily(day, symbols, volume=100.0):
    return pl.DataFrame({"symbol": symbols, "dt": [day] * len(symbols),
                         "volume": [volume] * len(symbols)})


def ctx(query, runs=(), *, shadow_state=None, workbench=None):
    return CheckContext(None, query, DAY, PREV, SESSIONS, list(runs),
                        shadow_state=shadow_state, workbench=workbench)


def symbols(n, suffix="SZ", start=1):
    return [f"{i:06d}.{suffix}" for i in range(start, start + n)]


def test_daily_arrived_compares_with_previous_session():
    today, before = symbols(99), symbols(100)
    got = sc._daily_arrived(ctx(FakeQuery(daily=pl.concat([daily(DAY, today), daily(PREV, before)]))))
    assert got.status == sc.OK
    got = sc._daily_arrived(ctx(FakeQuery(daily=pl.concat([daily(DAY, symbols(90)), daily(PREV, before)]))))
    assert got.status == sc.BAD and "90" in got.actual
    assert sc._daily_arrived(ctx(FakeQuery(daily=daily(PREV, before)))).status == sc.BAD


def test_limits_must_cover_every_daily_symbol_and_name_the_missing():
    query = FakeQuery(daily=daily(DAY, ["000001.SZ", "600000.SH"]),
                      limits=daily(DAY, ["000001.SZ"]))
    got = sc._limits_cover_daily(ctx(query))
    assert got.status == sc.BAD and "600000.SH" in got.detail


def test_flags_are_only_expected_for_production_boards():
    query = FakeQuery(daily=daily(DAY, ["000001.SZ", "920077.BJ"]), flags=daily(DAY, ["000001.SZ"]))
    assert sc._flags_cover_daily(ctx(query)).status == sc.OK


def sync_run(status="succeeded", *, with_step=True, error=""):
    steps = [{"name": "停牌", "table": "suspend_daily", "status": status, "rows": 0,
              "detail": "", "error": error}] if with_step else []
    return {"service": "tushare-sync", "session": DAY.isoformat(), "steps": steps,
            "error": "", "run_id": "r"}


def test_suspend_arrival_is_judged_by_the_sync_record_not_by_rows():
    query = FakeQuery()
    assert sc._suspend_receipt(ctx(query, [sync_run(with_step=False)])).status == sc.BAD
    failed = sc._suspend_receipt(ctx(query, [sync_run("failed", error="超时")]))
    assert failed.status == sc.BAD and "超时" in failed.detail
    ok = sc._suspend_receipt(ctx(query, [sync_run()]))
    assert ok.status == sc.OK and "0 只" in ok.actual


def test_empty_limit_list_fails():
    assert sc._limit_list_arrived(ctx(FakeQuery())).status == sc.BAD


def test_blocking_metric_checks_warn():
    frame = pl.DataFrame({"run_id": ["a", "a"], "scope_end": [DAY, DAY], "passed": [False, True],
                          "severity": ["blocking", "blocking"], "check_id": ["A1.x", "A2.y"]})
    got = sc._metric_checks(ctx(FakeQuery(metric_checks=frame)))
    assert got.status == sc.WARN and "A1.x" in got.detail and "1 条规则" in got.actual
    assert sc._metric_checks(ctx(FakeQuery())).status == sc.BAD


def test_unrun_sync_passes_on_suspend_rows_but_not_on_an_empty_table():
    empty = sc._suspend_receipt(ctx(FakeQuery()))
    assert empty.status == sc.BAD and "服务未执行" in empty.actual
    suspend = pl.DataFrame({"symbol": ["000001.SZ"], "dt": [DAY], "suspend_type": ["S"]})
    got = sc._suspend_receipt(ctx(FakeQuery(suspend=suspend)))
    assert got.status == sc.OK and got.detail == sc.NOT_RUN_NOTE and "1 只" in got.actual


def test_premarket_without_run_record_passes_when_limits_cover_daily():
    names = symbols(3)
    query = FakeQuery(daily=daily(DAY, names), limits=daily(DAY, names))
    got = sc._premarket_rows(ctx(query))
    assert got.status == sc.OK and got.detail == sc.NOT_RUN_NOTE
    assert "服务未执行" in got.actual and got.rows == 3


def test_premarket_without_run_record_fails_when_limits_are_incomplete():
    names = symbols(3)
    query = FakeQuery(daily=daily(DAY, names), limits=daily(DAY, names[:2]))
    got = sc._premarket_rows(ctx(query))
    assert got.status == sc.BAD and names[2] in got.detail


def test_premarket_failed_run_stays_failed_even_if_limits_arrived_later():
    names = symbols(3)
    query = FakeQuery(daily=daily(DAY, names), limits=daily(DAY, names))
    run = {"service": "premarket-limits", "session": DAY.isoformat(), "error": "",
           "steps": [{"table": "price_limits_daily", "status": "failed", "rows": 0,
                      "finished_at": "2026-09-17T09:25:00", "detail": "09:25 仍未取到"}]}
    assert sc._premarket_rows(ctx(query, [run])).status == sc.BAD


def test_premarket_reads_its_own_run_record():
    run = {"service": "premarket-limits", "session": DAY.isoformat(), "error": "",
           "steps": [{"table": "price_limits_daily", "status": "succeeded", "rows": 5644,
                      "finished_at": "2026-09-17T09:01:02", "detail": ""}]}
    got = sc._premarket_rows(ctx(FakeQuery(), [run]))
    assert got.status == sc.OK and "5644" in got.actual


def minutes(symbol, n, volume_each=1):
    start = datetime.combine(DAY, time(9, 30))
    return pl.DataFrame({"symbol": [symbol] * n,
                         "dt": [start.replace(minute=30) for _ in range(n)],
                         "volume": [volume_each] * n})


@pytest.fixture
def archive(monkeypatch):
    wanted = pl.DataFrame({"symbol": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"],
                           "dt": [DAY] * 4})
    monkeypatch.setattr(sc, "candidate_stock_days", lambda *a, **k: wanted)
    monkeypatch.setattr(sc, "missing_auction_days",
                        lambda wh, frame: frame.filter(pl.col("symbol") == "000002.SZ"))
    return FakeQuery(
        daily=pl.DataFrame({"symbol": ["000001.SZ", "000002.SZ", "000003.SZ"], "dt": [DAY] * 3,
                            "volume": [241.0, 250.0, 241.0]}),
        suspend=pl.DataFrame({"symbol": ["000004.SZ"], "dt": [DAY], "suspend_type": ["S"]}),
        minutes=pl.concat([minutes("000001.SZ", 241), minutes("000002.SZ", 240)]),
    )


def test_archive_checks(archive):
    c = ctx(archive)
    auction = sc._auction_arrived(c)
    assert auction.status == sc.BAD and "000002.SZ" in auction.detail and "候选 3" in auction.expected
    arrived = sc._minutes_arrived(c)
    assert arrived.status == sc.BAD and "000003.SZ" in arrived.detail   # 停牌的 000004 不算缺
    complete = sc._minutes_complete(c)
    assert complete.status == sc.BAD and "000002.SZ(240)" in complete.detail
    volume = sc._minutes_volume(c)
    assert volume.status == sc.BAD and "000002.SZ" in volume.detail and "000001.SZ" not in volume.detail


def test_archive_checks_pass_without_candidates(monkeypatch):
    monkeypatch.setattr(sc, "candidate_stock_days",
                        lambda *a, **k: pl.DataFrame(schema={"symbol": pl.Utf8, "dt": pl.Date}))
    c = ctx(FakeQuery())
    assert sc._minutes_arrived(c).status == sc.OK
    assert sc._minutes_complete(c).status == sc.SKIP


def test_step_names_round_trip_and_every_check_belongs_to_a_timer():
    from qsys.ops.services import SERVICE_BY_ID

    for check in sc.CHECKS:
        assert sc.parse_step_name(sc.step_name(check)) == (check.service, check.name)
        assert check.service in SERVICE_BY_ID
    assert sc.parse_step_name("日线") is None


def test_delisting_pool_is_recomputed_independently_from_namechange():
    history = pl.DataFrame({
        "symbol": ["300344.SZ", "600001.SH", "600002.SH"],
        "change_reason": ["退市整理期", "退市整理期", "*ST"],
        "start_date": [date(2026, 9, 1), date(2026, 8, 1), date(2026, 1, 1)],
        "end_date": [None, date(2026, 9, 10), None],
    })
    good = FakeQuery(history=history, pool=daily(DAY, ["300344.SZ"]))
    # 早已退市、namechange 没登记结束日的不算在池里
    stale = pl.DataFrame({"symbol": ["300344.SZ"], "delist_date": [date(2026, 9, 1)]})
    assert sc._delisting_pool(ctx(FakeQuery(history=history, instruments=stale,
                                            pool=daily(DAY, [])))).status == sc.OK
    assert sc._delisting_pool(ctx(good)).status == sc.OK
    bad = FakeQuery(history=history, pool=daily(DAY, ["600001.SH"]))
    got = sc._delisting_pool(ctx(bad))
    assert got.status == sc.BAD and "缺 300344.SZ" in got.detail and "多出 600001.SH" in got.detail


def checks_run(started, steps):
    return {"service": "service-checks", "session": DAY.isoformat(), "started_at": started,
            "run_id": f"service-checks-{started[11:13]}", "steps": steps}


def test_earlier_failure_turns_a_passing_check_into_repaired():
    name = "daily-archive｜分钟线到货"
    runs = [checks_run("2026-09-17T20:00:01", [
                {"name": name, "status": "failed", "detail": "期望：候选 79 只；实际：缺 79 只"},
                {"name": "tushare-sync｜日线到货", "status": "succeeded", "detail": ""}]),
            checks_run("2026-09-17T23:10:00", [
                {"name": name, "status": "repaired", "detail": "…"}])]
    failures = sc.earlier_failures(runs, DAY)
    assert set(failures) == {name}
    ok = sc.CheckResult(sc.OK, "候选 79 只", "缺 0 只", rows=79)
    got = sc.mark_repaired(ok, failures.get(name))
    assert got.status == sc.REPAIRED and "20:00" in got.detail and "缺 79 只" in got.detail
    assert sc.mark_repaired(ok, None) == ok
    bad = sc.CheckResult(sc.BAD, "候选 79 只", "缺 3 只")
    assert sc.mark_repaired(bad, failures[name]) == bad
    other_day = [{**runs[0], "session": "2026-09-16"}]
    assert sc.earlier_failures(other_day, DAY) == {}


def verify_run(status="succeeded", *, session=DAY, rows=364):
    return {"service": "catalog-verify", "session": session.isoformat(), "error": "",
            "run_id": "catalog-verify-194000-1", "started_at": "2026-09-17T19:40:00",
            "steps": [{"name": "指标字典验证", "table": "metric_checks", "status": status,
                       "rows": rows, "finished_at": "2026-09-17T19:41:30", "detail": ""}]}


def _metric_frame(rows):
    return pl.DataFrame(rows, orient="row", schema={"metric_id": pl.Utf8, "check_id": pl.Utf8,
                                      "scope_end": pl.Date, "run_id": pl.Utf8,
                                      "passed": pl.Boolean, "severity": pl.Utf8})


def test_catalog_verify_arrival_reads_its_run_record():
    query = FakeQuery(metric_checks=_metric_frame([
        ("tushare_daily.close", "A3.pct_change_identity", DAY, "r", True, "blocking")]))
    got = sc._catalog_verify_ran(ctx(query, [verify_run()]))
    assert got.status == sc.OK and "364" in got.actual
    assert sc._catalog_verify_ran(ctx(query, [verify_run("failed")])).status == sc.BAD
    # 没有运行记录：改验它该产出的结果（完整性由「覆盖全部指标」单独判）
    unrun = sc._catalog_verify_ran(ctx(query))
    assert unrun.status == sc.OK and unrun.detail == sc.NOT_RUN_NOTE
    assert sc._catalog_verify_ran(ctx(FakeQuery())).status == sc.BAD


def test_catalog_verify_skipped_for_memory_needs_the_sync_results_to_count():
    """与同步撞车时主动跳过：同步那次的增量结果得补上，否则不算数。"""
    query = FakeQuery(metric_checks=_metric_frame([
        ("tushare_daily.close", "A3.pct_change_identity", DAY, "r", True, "blocking")]))
    got = sc._catalog_verify_ran(ctx(query, [verify_run("skipped")]))
    assert got.status == sc.OK and "验证跳过" in got.actual
    assert sc._catalog_verify_ran(ctx(FakeQuery(), [verify_run("skipped")])).status == sc.BAD


def test_catalog_verify_counts_metrics_and_requires_the_full_scope_rules(monkeypatch):
    monkeypatch.setattr(sc, "EXPECTED_METRIC_IDS", {"a.x", "b.y"})
    monkeypatch.setattr(sc, "FULL_SCOPE_CHECK_IDS", {"B2.holes"})
    partial = FakeQuery(metric_checks=_metric_frame([
        ("a.x", "B2.holes", DAY, "r", True, "blocking")]))
    missing = sc._catalog_verify_covers_metrics(ctx(partial))
    assert missing.status == sc.BAD and "b.y" in missing.detail

    full = FakeQuery(metric_checks=_metric_frame([
        ("a.x", "B2.holes", DAY, "r", True, "blocking"),
        ("b.y", "A3.pct_change_identity", DAY, "r", True, "blocking")]))
    assert sc._catalog_verify_covers_metrics(ctx(full)).status == sc.OK
    assert sc._catalog_verify_ran_full_scope(ctx(full)).status == sc.OK

    incremental_only = FakeQuery(metric_checks=_metric_frame([
        ("a.x", "A3.pct_change_identity", DAY, "r", True, "blocking")]))
    only = sc._catalog_verify_ran_full_scope(ctx(incremental_only))
    assert only.status == sc.BAD and "B2.holes" in only.detail


def _live_state(path, rows):
    import sqlite3
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE live_events (event_key TEXT, scheduled_at TEXT, phase TEXT,"
                " status TEXT, detail TEXT DEFAULT '', completed_at TEXT DEFAULT '')")
    con.executemany("INSERT INTO live_events VALUES (?,?,?,?,?,?)", rows)
    con.commit(); con.close()
    return path


def test_shadow_events_flag_expired_and_unfinished_events(tmp_path):
    path = _live_state(tmp_path / "shadow.sqlite3", [
        (f"{DAY}:session_start:09:15:00", f"{DAY}T09:15:00", "session_start",
         "completed", "", f"{DAY}T09:15:00"),
        (f"{DAY}:auction:09:30:00", f"{DAY}T09:30:00", "auction",
         "completed", "事件已过期，未补跑", f"{DAY}T10:42:45"),
    ])
    got = sc._shadow_events(ctx(FakeQuery(), shadow_state=path))
    assert got.status == sc.BAD and "auction" in got.detail and "已过期" in got.detail

    ok = _live_state(tmp_path / "ok.sqlite3", [
        (f"{DAY}:session_start:09:15:00", f"{DAY}T09:15:00", "session_start",
         "completed", "", f"{DAY}T09:15:00"),
        (f"{DAY}:auction:09:30:00", f"{DAY}T09:30:00", "auction",
         "completed", "", f"{DAY}T09:30:01"),
    ])
    assert sc._shadow_events(ctx(FakeQuery(), shadow_state=ok)).status == sc.OK


def test_shadow_events_need_the_session_to_have_started(tmp_path):
    empty = _live_state(tmp_path / "empty.sqlite3", [])
    assert sc._shadow_events(ctx(FakeQuery(), shadow_state=empty)).status == sc.BAD
    missing = tmp_path / "nope.sqlite3"
    assert sc._shadow_events(ctx(FakeQuery(), shadow_state=missing)).status == sc.BAD


def test_shadow_events_skip_when_no_state_configured():
    assert sc._shadow_events(ctx(FakeQuery())).status == sc.SKIP


def _workbench(path, accounts, events):
    import sqlite3
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE paper_accounts (id TEXT, name TEXT, status TEXT,"
                " activation_date TEXT)")
    con.execute("CREATE TABLE paper_events (account_id TEXT, event_key TEXT,"
                " scheduled_at TEXT, phase TEXT, status TEXT, detail TEXT DEFAULT '')")
    con.executemany("INSERT INTO paper_accounts VALUES (?,?,?,?)", accounts)
    con.executemany("INSERT INTO paper_events VALUES (?,?,?,?,?,?)", events)
    con.commit(); con.close()
    return path


def test_paper_events_only_cover_running_accounts(tmp_path):
    path = _workbench(
        tmp_path / "wb.sqlite3",
        [("acct-run", "跑着的", "running", "2026-09-01"),
         ("acct-done", "已结束的", "finished", "2026-09-01")],
        [("acct-run", f"{DAY}:auction:09:30:00", f"{DAY}T09:30:00", "auction",
          "completed", "")],
    )
    assert sc._paper_events(ctx(FakeQuery(), workbench=path)).status == sc.OK

    idle = _workbench(tmp_path / "idle.sqlite3",
                      [("acct-run", "跑着的", "running", "2026-09-01")], [])
    got = sc._paper_events(ctx(FakeQuery(), workbench=idle))
    assert got.status == sc.BAD and "跑着的" in got.detail

    nobody = _workbench(tmp_path / "none.sqlite3",
                        [("acct-done", "已结束的", "finished", "2026-09-01")], [])
    assert sc._paper_events(ctx(FakeQuery(), workbench=nobody)).status == sc.SKIP


def test_paper_events_ignore_accounts_that_were_not_live_yet(tmp_path):
    """生产 2026-09-18 起效的账户，在 09-17 本来就不该有事件（本条检查曾误报）。"""
    path = _workbench(tmp_path / "future.sqlite3",
                      [("acct-new", "明天才生效的", "running", "2026-09-18")], [])
    assert sc._paper_events(ctx(FakeQuery(), workbench=path)).status == sc.SKIP
