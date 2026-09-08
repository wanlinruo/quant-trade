from datetime import date, datetime

import pytest

from qsys.core.events import Reject
from qsys.core.trading import Fill, Side
from qsys.core.types import Market, Symbol
from qsys.ops.daily_report import build_daily_report
from qsys.ops.journal import SessionJournal

CMB = Symbol("600036", Market.SH)
D0 = date(2024, 1, 2)
D = date(2024, 1, 3)
T = datetime(2024, 1, 3, 9, 30)


@pytest.fixture
def journal(warehouse) -> SessionJournal:
    return SessionJournal(warehouse)


def seed(journal: SessionJournal, *, consistent: bool = True) -> None:
    journal.record_equity(D0, cash=100_000.0, market_value=0.0)
    journal.record_equity(D, cash=88_000.0, market_value=13_000.0)
    journal.record_fills(D, [Fill("a", CMB, Side.BUY, 1000, 12.0, 5.1, T)])
    journal.record_reconciliation(
        D, "intraday", is_consistent=consistent,
        cash_diff=0.0 if consistent else 1234.5,
        n_mismatches=0 if consistent else 1,
        detail="" if consistent else "600036.SH: local=500 remote=400", at=T,
    )


def test_report_carries_the_session_date(journal):
    seed(journal)
    assert build_daily_report(journal, D).dt == D


def test_equity_change_is_computed_against_the_previous_session(journal):
    seed(journal)
    report = build_daily_report(journal, D)
    assert report.equity.total_value == 101_000.0
    assert report.equity.change_pct == pytest.approx(0.01)


def test_change_is_zero_on_the_first_session(journal):
    journal.record_equity(D, cash=100_000.0, market_value=0.0)
    assert build_daily_report(journal, D).equity.change_pct == 0.0


def test_report_counts_fills_and_rejects(journal):
    seed(journal)
    journal.record_rejects(D, [Reject("b", CMB, Side.BUY, 100, "limit up", T)])
    report = build_daily_report(journal, D)
    assert len(report.fills) == 1
    assert len(report.rejects) == 1


def test_healthy_when_reconciliation_passes(journal):
    seed(journal, consistent=True)
    assert build_daily_report(journal, D).is_healthy is True


def test_unhealthy_when_reconciliation_fails(journal):
    seed(journal, consistent=False)
    report = build_daily_report(journal, D)
    assert report.is_healthy is False
    assert "对账" in report.headline


def test_unhealthy_when_a_source_is_silent(journal):
    seed(journal)
    report = build_daily_report(journal, D, dead_sources=("agent",))
    assert report.is_healthy is False
    assert "agent" in report.headline


def test_text_report_contains_every_section(journal):
    seed(journal)
    text = build_daily_report(journal, D, quality_summary="quality: clean").as_text()
    for marker in ("净值", "成交", "拒单", "对账", "数据质量"):
        assert marker in text


def test_text_report_shows_reject_reasons(journal):
    seed(journal)
    journal.record_rejects(D, [Reject("b", CMB, Side.BUY, 100, "涨停买不到", T)])
    assert "涨停买不到" in build_daily_report(journal, D).as_text()


def test_text_report_shows_mismatch_detail(journal):
    seed(journal, consistent=False)
    assert "600036.SH" in build_daily_report(journal, D).as_text()


def test_report_on_a_session_with_no_data(journal):
    report = build_daily_report(journal, D)
    assert report.equity.total_value == 0.0
    assert report.fills == ()
    assert report.is_healthy is True


def test_headline_is_short_and_actionable(journal):
    seed(journal, consistent=False)
    headline = build_daily_report(journal, D).headline
    assert len(headline) < 120
    assert headline.startswith("异常") or headline.startswith("正常")
