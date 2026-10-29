"""服务质检：期望值只取直取源与运行记录；缺什么要说出具体缺哪些。"""

from datetime import date, datetime, time, timedelta, timezone

import polars as pl
import pytest

from qsys.ops import service_checks as sc
from qsys.ops import storage as sc_storage
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
            schema={"symbol": pl.Utf8, "dt": pl.Date, "suspend_type": pl.Utf8,
                    "suspend_timing": pl.Utf8}))
        return frame.filter(pl.col("dt") == start)

    def limit_list(self, symbols, start, end):
        return self._dated("limit_list", start)

    def daily_flags(self, start, end):
        return self._dated("flags", start)

    def valuation(self, start, end):
        return self._dated("valuation", start)

    def share_capital_history(self):
        return self.frames.get("capital", pl.DataFrame(schema={
            "symbol": pl.Utf8, "effective_date": pl.Date,
            "announcement_date": pl.Date, "total_shares": pl.Float64,
            "circulating_shares": pl.Float64,
        }))

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


def test_share_capital_history_must_match_current_daily_basic_values():
    current = daily(DAY, ["000001.SZ", "600000.SH"]).with_columns(
        pl.Series("total_shares", [100.0, 200.0]),
        pl.Series("circulating_shares", [80.0, 150.0]),
    )
    history = pl.DataFrame({
        "symbol": ["000001.SZ", "600000.SH"],
        "effective_date": [PREV, PREV],
        "announcement_date": [None, None],
        "total_shares": [100.0, 199.0],
        "circulating_shares": [80.0, 150.0],
    })
    bad = sc._share_capital_matches_daily(ctx(FakeQuery(daily=current, capital=history)))
    assert bad.status == sc.BAD and "600000.SH" in bad.detail
    history = history.with_columns(
        pl.when(pl.col("symbol") == "600000.SH").then(200.0)
        .otherwise(pl.col("total_shares")).alias("total_shares")
    )
    good = sc._share_capital_matches_daily(ctx(FakeQuery(daily=current, capital=history)))
    assert good.status == sc.OK and "2 只全部一致" in good.actual


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
    suspend = pl.DataFrame({"symbol": ["000001.SZ"], "dt": [DAY],
                            "suspend_type": ["S"], "suspend_timing": [None]})
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


def test_premarket_suspension_receipt_accepts_a_successful_empty_snapshot():
    run = {"service": "premarket-limits", "session": DAY.isoformat(), "error": "",
           "steps": [{"table": "suspend_daily", "status": "succeeded", "rows": 0,
                      "finished_at": "2026-09-17T09:24:02",
                      "detail": "query_status=success; rows=0; content_hash=abc"}]}
    got = sc._premarket_suspensions(ctx(FakeQuery(), [run]))
    assert got.status == sc.OK and "0 条" in got.actual


def test_premarket_suspension_query_failure_cannot_masquerade_as_empty():
    run = {"service": "premarket-limits", "session": DAY.isoformat(), "error": "",
           "steps": [{"table": "suspend_daily", "status": "failed", "rows": 0,
                      "finished_at": "2026-09-17T09:24:02",
                      "detail": "TimeoutError"}]}
    assert sc._premarket_suspensions(ctx(FakeQuery(), [run])).status == sc.BAD


def minutes(symbol, n, volume_each=1):
    start = datetime.combine(DAY, time(9, 30))
    return pl.DataFrame({"symbol": [symbol] * n,
                         "dt": [start.replace(minute=30) for _ in range(n)],
                         "volume": [volume_each] * n})


def minute_series(symbol, volumes, first="09:30"):
    """逐分钟递增的分钟线；volumes 决定每根的成交量，首根时刻可指定。"""
    hh, mm = (int(x) for x in first.split(":"))
    base = datetime.combine(DAY, time(hh, mm))
    return pl.DataFrame({"symbol": [symbol] * len(volumes),
                         "dt": [base + timedelta(minutes=i) for i in range(len(volumes))],
                         "volume": list(volumes)})


def _one_candidate(monkeypatch, symbol="000001.SZ"):
    monkeypatch.setattr(sc, "candidate_stock_days",
                        lambda *a, **k: pl.DataFrame({"symbol": [symbol], "dt": [DAY]}))


def test_first_bar_without_volume_is_padding_residue(monkeypatch):
    """首根 volume=0 是 QMT 区间查询的昨收价填充残留，不是行情。"""
    _one_candidate(monkeypatch)
    result = sc._minutes_first_bar_traded(ctx(FakeQuery(
        daily=daily(DAY, ["000001.SZ"]),
        minutes=minute_series("000001.SZ", [0, 0, 500]))))
    assert result.status == sc.BAD and "000001.SZ" in result.detail


def test_late_first_trade_is_not_an_error(monkeypatch):
    """513100.SH 那类首笔成交晚的标的是正确数据——旧的「241 根」判据会误报它。"""
    _one_candidate(monkeypatch)
    assert sc._minutes_first_bar_traded(ctx(FakeQuery(
        daily=daily(DAY, ["000001.SZ"]),
        minutes=minute_series("000001.SZ", [800, 0, 300], first="10:31")))).status == sc.OK


def test_first_bar_is_found_regardless_of_row_order(monkeypatch):
    """判据不能依赖 parquet 读回的行顺序：打乱后结论必须不变。"""
    _one_candidate(monkeypatch)
    shuffled = (minute_series("000001.SZ", [0, 500, 300, 700])
                .sample(fraction=1.0, shuffle=True, seed=7))
    assert sc._minutes_first_bar_traded(ctx(FakeQuery(
        daily=daily(DAY, ["000001.SZ"]), minutes=shuffled))).status == sc.BAD


def test_first_bar_check_skips_without_candidates(monkeypatch):
    monkeypatch.setattr(sc, "candidate_stock_days",
                        lambda *a, **k: pl.DataFrame(schema={"symbol": pl.Utf8, "dt": pl.Date}))
    assert sc._minutes_first_bar_traded(ctx(FakeQuery(daily=daily(DAY, [])))).status == sc.SKIP


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
        suspend=pl.DataFrame({"symbol": ["000004.SZ"], "dt": [DAY],
                              "suspend_type": ["S"], "suspend_timing": [None]}),
        minutes=pl.concat([minutes("000001.SZ", 241), minutes("000002.SZ", 240)]),
    )


def test_archive_checks(archive):
    c = ctx(archive)
    auction = sc._auction_arrived(c)
    assert auction.status == sc.BAD and "000002.SZ" in auction.detail and "候选 3" in auction.expected
    arrived = sc._minutes_arrived(c)
    assert arrived.status == sc.BAD and "000003.SZ" in arrived.detail   # 停牌的 000004 不算缺
    # 这批的首根都有成交，首根判据应当放行；填充残留另有专测
    assert sc._minutes_first_bar_traded(c).status == sc.OK
    volume = sc._minutes_volume(c)
    assert volume.status == sc.BAD and "000002.SZ" in volume.detail and "000001.SZ" not in volume.detail


def test_bj_closing_bar_volume_explains_daily_difference():
    query = FakeQuery(
        daily=daily(DAY, ["920161.BJ"], volume=44_619.3),
        minutes=pl.concat([
            minute_series("920161.BJ", [44_619], first="09:30"),
            pl.DataFrame({"symbol": ["920161.BJ"],
                          "dt": [datetime.combine(DAY, time(15, 0))],
                          "volume": [8_000]}),
        ]),
    )
    assert sc._minutes_volume(ctx(query)).status == sc.OK


def test_bj_volume_outside_closing_bar_band_still_fails():
    query = FakeQuery(
        daily=daily(DAY, ["920161.BJ"], volume=20_000.0),
        minutes=pl.concat([
            minute_series("920161.BJ", [44_619], first="09:30"),
            pl.DataFrame({"symbol": ["920161.BJ"],
                          "dt": [datetime.combine(DAY, time(15, 0))],
                          "volume": [8_000]}),
        ]),
    )
    assert sc._minutes_volume(ctx(query)).status == sc.BAD


def test_small_qmt_minute_source_drift_warns_without_hiding_the_evidence():
    query = FakeQuery(
        daily=daily(DAY, ["000001.SZ"], volume=100_000.0),
        minutes=minute_series("000001.SZ", [99_500]),
    )

    got = sc._minutes_volume(ctx(query))

    assert got.status == sc.WARN
    assert "小幅源间差异" in got.actual
    assert "000001.SZ(分钟 99500 / 日线 100000)" in got.detail


def test_qmt_minute_source_drift_above_one_percent_still_fails():
    query = FakeQuery(
        daily=daily(DAY, ["000001.SZ"], volume=100_000.0),
        minutes=minute_series("000001.SZ", [98_000]),
    )

    got = sc._minutes_volume(ctx(query))

    assert got.status == sc.BAD
    assert "超过源间容差" in got.actual


def test_archive_checks_pass_without_candidates(monkeypatch):
    monkeypatch.setattr(sc, "candidate_stock_days",
                        lambda *a, **k: pl.DataFrame(schema={"symbol": pl.Utf8, "dt": pl.Date}))
    c = ctx(FakeQuery(daily=daily(DAY, [])))
    assert sc._minutes_arrived(c).status == sc.BAD
    assert sc._minutes_first_bar_traded(c).status == sc.SKIP


def test_full_market_arrival_does_not_shrink_to_candidate_set(monkeypatch):
    _one_candidate(monkeypatch, "000001.SZ")
    query = FakeQuery(
        daily=daily(DAY, ["000001.SZ", "600000.SH"]),
        minutes=minute_series("000001.SZ", [100]),
    )
    got = sc._minutes_arrived(ctx(query))
    assert got.status == sc.BAD
    assert "有成交股票 2 只" in got.expected
    assert "600000.SH" in got.detail


def test_step_names_round_trip_and_every_check_belongs_to_a_timer():
    from qsys.ops.services import SERVICE_BY_ID

    for check in sc.CHECKS:
        assert sc.parse_step_name(sc.step_name(check)) == (check.service, check.name)
        assert check.service in SERVICE_BY_ID
    assert sc.parse_step_name("日线") is None


def test_d18_service_checks_own_minutes_and_suspend_receipt():
    owned = {(check.table, check.name) for check in sc.CHECKS}
    assert {
        ("minute_bars", "分钟线到货"),
        ("minute_bars", "分钟线首根有成交"),
        ("minute_bars", "分钟线成交量对日线"),
        ("suspend_daily", "停牌采集成功"),
    } <= owned


def test_strategy_checks_are_separate_from_data_checks():
    strategy = {check.service for check in sc.CHECKS if check.domain == "strategy"}
    assert strategy == {"shadow-core", "paper"}
    assert all(check.domain == "data" for check in sc.CHECKS
               if check.service not in strategy)


def test_strategy_event_check_follows_active_deployment_mode():
    from scripts.service_checks import checks_for_mode

    def strategy_services(mode):
        return {check.service for check in checks_for_mode(sc.CHECKS, mode)
                if check.domain == "strategy"}

    assert strategy_services("shadow") == {"shadow-core", "paper"}
    assert strategy_services("live") == {"live-core", "paper"}
    assert strategy_services("disabled") == {"paper"}


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


def test_unknown_source_value_in_data_is_bad(monkeypatch):
    """表里出现目录不认识的源值就报——这是「有人加了源却没登记」的唯一信号。"""
    monkeypatch.setattr(sc, "_minute_source_values", lambda ctx: {"paid", "qmt", "newvendor"})
    result = sc._source_aliases_resolve(ctx(FakeQuery()))
    assert result.status == sc.BAD and "newvendor" in result.detail


def test_all_known_source_values_pass(monkeypatch):
    monkeypatch.setattr(sc, "_minute_source_values", lambda ctx: {"paid", "qmt", "joinquant"})
    assert sc._source_aliases_resolve(ctx(FakeQuery())).status == sc.OK


def test_no_minute_partitions_found_is_a_warning_not_a_pass(monkeypatch):
    """`bar_minute_1m_sources()` 扫不到分区就返回空表 ⇒ seen=∅ ⇒ unknown=∅
    ⇒ 旧实现判 OK、rows=0。生产那张表有 1636 个分区文件，rows=0 在真值上
    不可能，只可能是仓库根指错或分区布局变了。"""
    monkeypatch.setattr(sc, "_minute_source_values", lambda ctx: set())
    result = sc._source_aliases_resolve(ctx(FakeQuery()))
    assert result.status == sc.WARN
    assert result.rows == 0
    assert "没扫到" in result.actual


def test_the_empty_source_frame_really_does_reach_that_branch(tmp_path):
    """真正走一遍 `_minute_source_values`：空仓库里它必须是空集合。

    只 monkeypatch 那个函数证不了「空仓库会触发 WARN」——这条把两段接上。
    """
    from qsys.data.query import WarehouseQuery
    from qsys.data.storage import Warehouse

    query = WarehouseQuery(Warehouse(tmp_path))
    assert query.bar_minute_1m_sources().height == 0
    assert sc._minute_source_values(ctx(query)) == set()


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


# ---- 节点存储 ----

def _storage_ctx(base, scratch=None):
    return CheckContext(None, None, DAY, PREV, SESSIONS, [],
                        storage_base=base, scratch=scratch)


def test_storage_checks_skip_without_a_base():
    assert sc._disk_capacity(_storage_ctx(None)).status == sc.SKIP
    assert sc._retention(_storage_ctx(None)).status == sc.SKIP


def test_disk_capacity_levels(tmp_path, monkeypatch):
    gib = sc.GIB
    for free, status in ((50 * gib, sc.OK), (15 * gib, sc.WARN), (5 * gib, sc.BAD)):
        monkeypatch.setattr(sc, "disk_status",
                            lambda _p, free=free: sc_storage.DiskStatus(100 * gib, 100 * gib - free, free))
        assert sc._disk_capacity(_storage_ctx(tmp_path)).status == status


def test_system_disk_is_checked_without_data_base(monkeypatch):
    from pathlib import Path
    for free, expected in [(30, sc.OK), (15, sc.WARN), (5, sc.BAD)]:
        def probe(path):
            assert path == Path("/")
            return sc_storage.DiskStatus(120 * sc.GIB, (120 - free) * sc.GIB, free * sc.GIB)
        monkeypatch.setattr(sc, "disk_status", probe)
        assert sc._system_disk_capacity(_storage_ctx(None)).status == expected
    monkeypatch.setattr(sc, "disk_status", lambda _: (_ for _ in ()).throw(OSError("unavailable")))
    assert sc._system_disk_capacity(_storage_ctx(None)).status == sc.BAD


def test_retention_warns_on_expired_copies_and_unregistered_dirs(tmp_path):
    import os
    base = tmp_path / "qsys"
    old = base / "warehouse/backups/old"
    old.mkdir(parents=True)
    (old / "part.parquet").write_bytes(b"x" * 4096)
    stamp = datetime(2026, 1, 1).timestamp()
    os.utime(old / "part.parquet", (stamp, stamp))
    os.utime(old, (stamp, stamp))
    (base / "mystery").mkdir()

    result = sc._retention(_storage_ctx(base))

    assert result.status == sc.WARN
    assert "old" in result.detail and "mystery" in result.detail


def test_retention_passes_when_everything_is_registered_and_fresh(tmp_path):
    base = tmp_path / "qsys"
    (base / "warehouse").mkdir(parents=True)
    (base / "baidupan").mkdir()

    assert sc._retention(_storage_ctx(base)).status == sc.OK


# ---- 站点证书 ----

def _tls_server(tmp_path, days_valid):
    """起一个真的 TLS 服务，证书用 openssl 现生成——取证书那段要真握手才算验过。"""
    import shutil
    import socket
    import ssl
    import subprocess
    import threading

    if shutil.which("openssl") is None:
        pytest.skip("环境里没有 openssl，无法生成测试证书")
    pem = tmp_path / f"cert-{days_valid}.pem"
    key = tmp_path / f"key-{days_valid}.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(pem), "-days", str(days_valid),
         "-subj", "/CN=qsys.test", "-addext", "subjectAltName=DNS:qsys.test"],
        check=True, capture_output=True,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(pem, key)
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)

    def serve():
        while True:
            try:
                raw, _ = listener.accept()
            except OSError:
                return
            try:
                with context.wrap_socket(raw, server_side=True):
                    pass
            except OSError:
                pass

    threading.Thread(target=serve, daemon=True).start()
    return listener.getsockname()[1], listener, str(pem)


def _cert_ctx(port=8443, site="qsys.test"):
    return CheckContext(None, None, DAY, PREV, SESSIONS, [], tls_site=site, tls_port=port)


def test_serving_not_after_reads_the_certificate_off_a_real_handshake(tmp_path):
    """真起一个 TLS 服务来取：解析日期格式、SNI、校验都只有真握手才验得到。"""
    port, listener, cafile = _tls_server(tmp_path, 90)
    try:
        not_after = sc.serving_not_after("127.0.0.1", port, "qsys.test", cafile=cafile)
    finally:
        listener.close()

    left = (not_after - datetime.now(timezone.utc).replace(tzinfo=None)).days
    assert 88 <= left <= 90


def test_certificate_check_skips_without_a_site():
    assert sc._certificate(_cert_ctx(site=None)).status == sc.SKIP


@pytest.mark.parametrize("left,status", [(90, "OK"), (15, "WARN"), (3, "BAD")])
def test_certificate_status_follows_the_days_left(monkeypatch, left, status):
    monkeypatch.setattr(sc, "serving_not_after",
                        lambda *a, **k: datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=left, hours=1))

    result = sc._certificate(_cert_ctx())

    assert result.status == getattr(sc, status)
    assert f"剩余 {left} 天" in result.actual


def test_certificate_check_fails_when_the_site_cannot_be_reached(tmp_path):
    """取不到就得判失败，不能因为探测本身出错而静默放过。"""
    port, listener, _ = _tls_server(tmp_path, 90)
    listener.close()

    result = sc._certificate(_cert_ctx(port))

    assert result.status == sc.BAD
    assert "取不到证书" in result.actual


@pytest.mark.parametrize('relation_failed', [False, True])
def test_actual_catalog_warning_record_is_executed_and_keeps_real_task_failure(tmp_path, relation_failed):
    import json
    from qsys.ops.services import ServiceRun
    with ServiceRun.open(tmp_path,'catalog-verify') as run:
        run.session=DAY
        with run.step('指标字典验证',table='metric_checks') as step:
            step.rows=638;step.warn('阻断0，既有警告28')
        with run.step('表级五维健康',table='dataset_checks') as step:step.rows=12
        with run.step('关系完整性',table='relation_checks') as step:
            step.rows=7
            if relation_failed:step.fail('真实关系red1')
    raw=json.loads(next(tmp_path.rglob('catalog-verify-*.json')).read_text())
    got=sc._catalog_verify_ran(ctx(FakeQuery(),[raw]))
    assert got.rows==638 and '已执行' in got.actual
    assert got.status==(sc.BAD if relation_failed else sc.WARN)
    if relation_failed:assert '关系完整性' in got.detail and 'red1' in got.detail
    else:assert '有警告' in got.actual


def test_interrupted_catalog_is_not_reported_healthy_after_field_completion():
    run={**verify_run(), 'status':'interrupted'}
    got=sc._catalog_verify_ran(ctx(FakeQuery(),[run]))
    assert got.status==sc.BAD and got.rows==364 and 'interrupted' in got.actual
