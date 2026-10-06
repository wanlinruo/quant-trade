"""B 类规则必须能报告系统性空值与到货异常。"""

from datetime import date, datetime
import polars as pl
import pytest
from qsys.data import schema
from qsys.data.catalog import REGISTRY
from qsys.data.catalog import checks as C
from qsys.data.catalog import rules_complete  # noqa: F401
from qsys.data.query import WarehouseQuery

D = [date(2026, 9, day) for day in (10, 11)]
NOW = datetime(2026, 9, 15, 19, 30)

@pytest.fixture
def ctx(warehouse):
    return C.CheckContext(WarehouseQuery(warehouse), warehouse, D[0], D[-1])

def _flags(rows, warehouse):
    frame = pl.DataFrame(rows)
    bools = {"is_limit_up", "is_limit_down", "touched_limit_up", "is_broken_board", "is_first_board", "is_st"}
    frame = frame.with_columns([pl.lit(None, dtype=pl.Boolean if n in bools else pl.Utf8 if n == "st_source" else pl.Float64).alias(n)
                                for n in schema.DAILY_FLAGS.names if n not in frame.columns])
    warehouse.write("daily_flags", frame.select(schema.DAILY_FLAGS.names).to_arrow().cast(schema.DAILY_FLAGS), year=2026)

def _run(cid, ctx, metric=None):
    """metric：多指标规则要指明看哪个指标的结论，不然默认第一个。

    B4.arrival 一条规则管好几张表，改成按数据集点名后，没被点到的指标会正常通过
    ——所以断言必须冲着真正相关的那个指标去（2026-09-30）。
    """
    rows = C.run_checks(ctx, "complete", NOW, only=[C.CHECKS[cid]],
                        artifacts=ctx.warehouse.root/"samples")
    if metric is not None:
        rows = rows.filter(pl.col("metric_id") == metric)
    return rows.row(0, named=True)

def test_null_rate_has_both_denominators(ctx, warehouse):
    rows = [{"dt": D[0], "symbol": f"{i:06d}.SZ", "is_st": True} for i in range(97)]
    rows += [{"dt": D[0], "symbol": "000999.SZ", "is_st": None}] * 3
    _flags(rows, warehouse)
    detail = _run("B3.null_rate", ctx)["detail"]
    assert "按行" in detail and "按标的" in detail

def test_zero_limit_up_is_failure(ctx, warehouse):
    warehouse.write("limit_list_daily", schema.LIMIT_LIST_DAILY.empty_table(), year=2026)
    _flags([{"dt": D[0], "symbol": "600036.SH", "is_limit_up": False}], warehouse)
    row = _run("B4.zero_limit_up", ctx)
    assert row["passed"] is False and "零涨停" in row["detail"]
    assert row["severity"] == "warning"
    assert row["known_issue"] == "D-12"


def test_today_zero_limit_is_na_before_release_sla(ctx, warehouse, monkeypatch):
    today = date(2026, 9, 15)
    current = C.CheckContext(ctx.query, warehouse, today, today)
    warehouse.write("limit_list_daily", schema.LIMIT_LIST_DAILY.empty_table(), year=2026)
    _flags([{"dt": today, "symbol": "600036.SH", "is_limit_up": False}], warehouse)
    monkeypatch.setattr(rules_complete, "_now", lambda: datetime(2026, 9, 15, 18, 59))
    row = _run("B4.zero_limit_up", current)
    assert row["passed"] is True
    assert row["checked_rows"] == 0
    assert "19:00" in row["detail"] and "不适用" in row["detail"]


def test_today_zero_limit_is_failure_after_release_sla(ctx, warehouse, monkeypatch):
    today = date(2026, 9, 15)
    current = C.CheckContext(ctx.query, warehouse, today, today)
    warehouse.write("limit_list_daily", schema.LIMIT_LIST_DAILY.empty_table(), year=2026)
    _flags([{"dt": today, "symbol": "600036.SH", "is_limit_up": False}], warehouse)
    monkeypatch.setattr(rules_complete, "_now", lambda: datetime(2026, 9, 15, 19, 0))
    row = _run("B4.zero_limit_up", current)
    assert row["passed"] is False
    assert row["known_issue"] == "D-12"

def test_calendar_has_a_real_denominator(ctx, warehouse):
    _flags([{"dt": D[0], "symbol": "600036.SH"}], warehouse)
    assert _run("B1.calendar", ctx)["checked_rows"] > 0

def test_all_completeness_rules_are_registered():
    ids = {cid for cid in C.CHECKS if cid.startswith("B")}
    assert ids == {
        "B1.calendar", "B2.holes", "B2.paid_minute_override_structure",
        "B2.canonical_minute_override_inclusion",
        "B2.canonical_qmt_leading_padding",
        "B2.paid_minute_known_symbol_gap", "B2.paid_minute_structure",
        "B2.paid_minute_symbol_coverage",
        "B3.null_rate", "B4.arrival", "B4.zero_limit_up", "B5.span",
    }
    assert all(C.CHECKS[cid].category is C.Category.COMPLETENESS for cid in ids)


def test_d18_arrival_ownership_has_no_nominal_coverage():
    assert C.CHECKS["B4.arrival"].metrics == (
        "live_recon.is_consistent", "instruments.symbol", "auction_ticks.dt",
    )
    withdrawn = {
        "minute_bars.dt", "suspend_daily.dt", "name_snapshots.dt",
        *(f"ingest_log.{name}" for name in schema.INGEST_LOG.names),
    }
    for metric_id in withdrawn:
        checks = REGISTRY[metric_id].checks
        assert not isinstance(checks, tuple) or "B4.arrival" not in checks, metric_id


# --- D-7：竞价 tick 到货 ------------------------------------------------------

BOARD = "600036.SH"


def _auction(warehouse, symbol, day):
    frame = pl.DataFrame({
        "symbol": [symbol], "dt": [day], "ts": [datetime(day.year, day.month, day.day, 9, 15, 3)],
        "bid1": [41.07], "ask1": [41.07], "bid_vol1": [100], "ask_vol1": [100],
        "last_price": [0.0], "volume": [0], "amount": [0.0],
    })
    warehouse.write("auction_ticks", frame.to_arrow().cast(schema.AUCTION_TICKS), year=day.year)


def _board_then_next_session(warehouse):
    """D[0] 首板，D[1] 是它的次日——竞价 tick 应在 D[1] 到货。"""
    _flags([{"dt": D[0], "symbol": BOARD, "is_first_board": True},
            {"dt": D[1], "symbol": BOARD, "is_first_board": False}], warehouse)


def _arrival_misses(row, warehouse):
    if row["sample_path"] is None:
        return pl.DataFrame()
    return pl.read_parquet(row["sample_path"]).filter(pl.col("dataset") == "auction_ticks")


def test_arrival_reports_first_board_without_next_day_auction(ctx, warehouse):
    _board_then_next_session(warehouse)
    row = _run("B4.arrival", ctx, metric="auction_ticks.dt")
    assert row["passed"] is False
    missed = _arrival_misses(row, warehouse)
    assert missed.select("symbol", "dt").rows() == [(BOARD, D[1])]


def test_arrival_passes_when_auction_arrived(ctx, warehouse):
    _board_then_next_session(warehouse)
    _auction(warehouse, BOARD, D[1])
    row = _run("B4.arrival", ctx)
    assert row["passed"] is True
    assert row["checked_rows"] > 0


def test_arrival_does_not_expect_auction_for_suspended_candidate(ctx, warehouse):
    _board_then_next_session(warehouse)
    suspended = pl.DataFrame({"symbol": [BOARD], "dt": [D[1]], "suspend_type": ["S"],
                              "suspend_timing": [None]}, schema_overrides={"suspend_timing": pl.Utf8})
    warehouse.write("suspend_daily", suspended.to_arrow().cast(schema.SUSPEND_DAILY), year=2026)
    assert _run("B4.arrival", ctx)["passed"] is True


def test_arrival_waits_for_todays_auction_archive(warehouse, monkeypatch):
    today = D[1]
    current = C.CheckContext(WarehouseQuery(warehouse), warehouse, today, today)
    _board_then_next_session(warehouse)
    monkeypatch.setattr(rules_complete, "_now", lambda: datetime(2026, 9, 11, 15, 59))
    assert _arrival_misses(_run("B4.arrival", current), warehouse).height == 0
    monkeypatch.setattr(rules_complete, "_now", lambda: datetime(2026, 9, 11, 16, 0))
    assert _arrival_misses(_run("B4.arrival", current), warehouse).height == 1


def _write(dataset, arrow_schema, rows, warehouse):
    frame = pl.DataFrame(rows)
    frame = frame.with_columns([pl.lit(None).alias(n) for n in arrow_schema.names
                                if n not in frame.columns])
    warehouse.write(dataset, frame.select(arrow_schema.names).to_arrow().cast(arrow_schema),
                    year=2026)


def test_holes_explained_by_direct_suspension_are_not_reported(warehouse):
    """2026 年生产 1593 个「空洞」全部是 suspend_d 有记录的停牌日，不是缺数据。"""
    days = [date(2026, 9, d) for d in (7, 8, 9, 10)]
    daily = [{"symbol": s, "dt": d, "close": 10.0} for s in ("000001.SZ", "000002.SZ")
             for d in days]
    # 000001 停牌 09-08（有直取记录）；000002 缺 09-09 却没有停牌记录
    daily = [r for r in daily if (r["symbol"], r["dt"]) not in {
        ("000001.SZ", days[1]), ("000002.SZ", days[2])}]
    _write("tushare_daily", schema.TUSHARE_DAILY, daily, warehouse)
    _write("suspend_daily", schema.SUSPEND_DAILY, [
        {"symbol": "000001.SZ", "dt": days[1], "suspend_type": "S"},
        {"symbol": "000002.SZ", "dt": days[2], "suspend_type": "R"},   # 复牌记录不能解释缺 bar
    ], warehouse)
    result = C.CHECKS["B2.holes"].run(C.CheckContext(WarehouseQuery(warehouse), warehouse,
                                                    days[0], days[-1]))
    assert result.violations == 1
    assert result.sample.to_dicts() == [{"symbol": "000002.SZ", "dt": days[2]}]
    assert "停牌" in result.detail


# ---- 多指标规则要点名，不能连坐（2026-09-30 维护者截图指出）----

def test_the_arrival_rule_blames_only_the_dataset_that_failed():
    """10,382 条违规全来自 live_recon，页面却把 auction_ticks 与 instruments
    也标成失败——看上去像三个数据源同时出事。"""
    from qsys.data.catalog.rules_complete import _blame

    assert _blame([{"dataset": "live_recon"}]) == ("live_recon.is_consistent",)
    assert _blame([{"dataset": "auction_ticks"}]) == ("auction_ticks.dt",)
    assert _blame([{"dataset": "live_recon"}, {"dataset": "instruments"}]) == (
        "instruments.symbol", "live_recon.is_consistent",
    )


def test_no_violation_means_no_blame():
    from qsys.data.catalog.rules_complete import _blame

    assert _blame([]) is None


def test_an_unattributable_dataset_falls_back_to_blaming_everything():
    """daily_flags / market_regime 不对应本规则声明的任何指标。
    说不清是谁的时候，全判失败才是诚实的——别为了页面好看而漏报。"""
    from qsys.data.catalog.rules_complete import _blame

    assert _blame([{"dataset": "daily_flags"}]) is None
    assert _blame([{"dataset": "live_recon"}, {"dataset": "market_regime"}]) is None


def test_an_unrelated_metric_is_not_dragged_down_with_it(ctx, warehouse):
    """竞价 tick 缺货时，live_recon 与 instruments 不该跟着标成失败。

    2026-09-30 维护者截图：指标字典上三个指标都红着，而违规其实全来自一处。
    """
    _board_then_next_session(warehouse)
    rows = C.run_checks(ctx, "complete", NOW, only=[C.CHECKS["B4.arrival"]],
                        artifacts=ctx.warehouse.root / "samples")

    verdict = {r["metric_id"]: r for r in rows.iter_rows(named=True)}
    assert verdict["auction_ticks.dt"]["passed"] is False
    assert verdict["live_recon.is_consistent"]["passed"] is True
    assert verdict["live_recon.is_consistent"]["violations"] == 0
    assert "其它指标" in verdict["live_recon.is_consistent"]["detail"]


# ---- 对账违规按运行模式分级（#134，2026-10-06）--------------------------------

def _recon(warehouse, rows):
    frame = pl.DataFrame(rows, schema={
        "dt": pl.Date, "at": pl.Datetime("us"), "layer": pl.Utf8, "mode": pl.Utf8,
        "is_consistent": pl.Boolean, "cash_diff": pl.Float64,
        "n_mismatches": pl.Int64, "detail": pl.Utf8,
    })
    warehouse.write("live_recon", frame.to_arrow().cast(schema.LIVE_RECON), year=D[0].year)


def _row(mode, *, consistent=False, layer="intraday"):
    return {"dt": D[-1], "at": datetime(D[-1].year, D[-1].month, D[-1].day, 11, 9),
            "layer": layer, "mode": mode, "is_consistent": consistent,
            "cash_diff": 49_998.69, "n_mismatches": 0, "detail": "INCONSISTENT"}


def test_shadow_drift_is_not_an_arrival_violation(ctx, warehouse):
    """影子盘与账户对不上是正当的：维护者自己要转账、买逆回购，而影子一张单都发不出去。

    设计上影子模式只告警不停机（`runtime._assert_reconciled`），可 B4.arrival 却把
    任何 `is_consistent=false` 当阻断违规——2026-09-28 起生产每天多出一条假阻断，
    真正的持仓不一致被它掩盖。
    """
    _recon(warehouse, [_row("shadow")])

    row = _run("B4.arrival", ctx, metric="live_recon.is_consistent")

    assert row["passed"] is True
    assert row["violations"] == 0
    assert "影子" in row["detail"]


def test_a_live_mode_inconsistency_still_blocks(ctx, warehouse):
    """实盘核心是账户的唯一写入方，对不上就是事故，必须照旧阻断。"""
    _recon(warehouse, [_row("live")])

    row = _run("B4.arrival", ctx, metric="live_recon.is_consistent")

    assert row["passed"] is False
    assert row["violations"] == 1


def test_a_reconciliation_without_a_mode_is_reported_as_undetermined(ctx, warehouse):
    """2026-10-06 之前的行没有 mode。不知道就说不知道，别替它假设成影子。"""
    _recon(warehouse, [_row(None)])

    row = _run("B4.arrival", ctx, metric="live_recon.is_consistent")

    assert row["passed"] is True
    assert "模式未记录" in row["detail"]


def test_a_shadow_position_mismatch_is_still_a_violation(ctx, warehouse):
    """影子豁免只豁免现金那一类，不能把持仓差一起放过。

    `ShadowBroker.poll_fills` 恒为空、`on_market_data` 不撮合——影子盘永远不会
    有自己的成交，所以它的持仓差只可能来自「账户里出现了核心不知道的持仓」。
    那是真信号，必须照旧阻断。
    """
    row = dict(_row("shadow"))
    row["n_mismatches"] = 1
    row["detail"] = "600036.SH: local=0 remote=500"
    _recon(warehouse, [row])

    verdict = _run("B4.arrival", ctx, metric="live_recon.is_consistent")

    assert verdict["passed"] is False
    assert verdict["violations"] == 1


def test_old_and_new_recon_partitions_can_coexist(ctx, warehouse):
    """`journal._merge` 只重写它写入的那一年，所以 2026 分区会一直没有 `mode`，
    而 2027 分区有。直接 `pl.concat` 两个宽度不同的表会抛 ShapeError，
    规则从跨年后第一次写入起就整条挂掉——而它是唯一的到货阻断规则。
    """
    old = pl.DataFrame({
        "dt": [D[0]], "at": [datetime(D[0].year, D[0].month, D[0].day, 10, 0)],
        "layer": ["intraday"], "is_consistent": [True], "cash_diff": [0.0],
        "n_mismatches": [0], "detail": [""],
    })
    (ctx.warehouse.root / "live_recon").mkdir(parents=True, exist_ok=True)
    old_dir = ctx.warehouse.root / "live_recon" / "year=2025"
    old_dir.mkdir(parents=True, exist_ok=True)
    old.write_parquet(old_dir / "part.parquet")
    _recon(warehouse, [_row("live")])

    verdict = _run("B4.arrival", ctx, metric="live_recon.is_consistent")

    assert verdict["violations"] == 1
