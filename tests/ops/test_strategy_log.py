"""策略运行记录：空仓也要说得清卡在哪，报错也要留痕。"""

from datetime import date, datetime

from qsys.ops.strategy_log import PHASE_LABELS, RunReport, build_record


DAY = date(2026, 9, 18)
T0 = datetime(2026, 9, 18, 9, 30, 0)
T1 = datetime(2026, 9, 18, 9, 30, 1, 800000)


def _record(report, **kwargs):
    fields = dict(
        event_key=f"{DAY}:auction:09:30:00",
        session=DAY,
        phase="auction",
        started_at=T0,
        finished_at=T1,
        cash=50_000.0,
        market_value=0.0,
        position_count=0,
        orders=0,
        rejects=0,
    )
    fields.update(kwargs)
    return build_record(report, **fields)


def _funnel(**kwargs):
    report = RunReport()
    report.report(**kwargs)
    return report


def test_report_merges_fields_and_accumulates_reasons():
    report = RunReport()
    report.report(candidates=37, reasons={"高开超过阈值": 12})
    report.report(passed=2, reasons={"高开超过阈值": 3, "竞价量不足": 5})
    assert report.candidates == 37 and report.passed == 2
    assert report.reasons == {"高开超过阈值": 15, "竞价量不足": 5}


def test_summary_shows_the_funnel_and_the_orders():
    report = RunReport()
    report.report(candidates=37, routed=5, passed=2)
    record = _record(report, orders=2)
    assert record.summary == "候选 37 只 → 入档 5 只 → 闸门通过 2 只 → 下单 2 笔"
    assert record.routed == 5
    assert record.duration_ms == 1800
    assert record.total_value == 50_000.0


def test_summary_says_why_nothing_was_bought():
    passed_none = _record(_funnel(candidates=37, passed=0))
    assert passed_none.summary == "候选 37 只 → 闸门通过 0 只，未下单"
    # 入档为 0：没买是价格带/竞价量拦的，不是闸门
    none_routed = RunReport()
    none_routed.report(candidates=34, routed=0, passed=0)
    assert _record(none_routed).summary == "候选 34 只 → 入档 0 只 → 闸门通过 0 只，未下单"
    empty_pool = _record(_funnel(candidates=0, passed=0))
    assert empty_pool.summary == "候选池为空，未下单"


def test_strategy_without_a_funnel_is_not_reported_as_zero():
    record = _record(
        RunReport(), phase="checkpoint", position_count=2, market_value=19_880.0
    )
    assert record.candidates is None and record.passed is None
    assert record.summary == "持仓 2 只，无下单"
    assert record.total_value == 69_880.0


def test_note_replaces_the_funnel_when_the_strategy_returned_early():
    record = _record(_funnel(note="没有上一交易日，无从选股"))
    assert record.summary == "没有上一交易日，无从选股"
    assert record.note == "没有上一交易日，无从选股"


def test_error_wins_over_everything_else():
    record = _record(
        _funnel(candidates=37, passed=2),
        orders=2,
        error="券商账户快照已过期: 19950.0s",
    )
    assert record.summary == "事件失败：券商账户快照已过期: 19950.0s"


def test_phase_labels_cover_every_phase():
    assert PHASE_LABELS == {
        "session_start": "日初结算",
        "auction": "竞价选股",
        "checkpoint": "检查点",
        "bar": "收盘撮合",
        "session_end": "收盘对账",
    }
