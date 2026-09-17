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


def ctx(query, runs=()):
    return CheckContext(None, query, DAY, PREV, SESSIONS, list(runs))


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
        assert SERVICE_BY_ID[check.service].kind == "timer"
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
