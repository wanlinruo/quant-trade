from scripts.service_checks import _exit_code


def test_strategy_failure_does_not_fail_data_quality_unit():
    assert _exit_code({"data": 0, "strategy": 2}) == 0


def test_data_failure_fails_data_quality_unit():
    assert _exit_code({"data": 1, "strategy": 0}) == 1


# ---- 质检的是今天（2026-10-01 国庆第一天实际发生）----

from pathlib import Path  # noqa: E402


def test_the_checks_run_for_today_not_the_last_closed_session():
    """定时器按 Mon..Fri 触发、不认节假日。取「最近已收盘交易日」会在假期里
    天天重查节前那一天：运维群连收五条一模一样的结论，日报还会把它当成当天的
    质检结果——于是「休市日不安排验证」和一份验证结论出现在同一封信里。
    """
    source = Path("scripts/service_checks.py").read_text(encoding="utf-8")

    assert "last_closed_session" not in source
    assert "datetime.now().date()" in source


def test_a_non_trading_day_is_skipped_rather_than_rechecked():
    source = Path("scripts/service_checks.py").read_text(encoding="utf-8")
    body = source[source.index("确定交易日"):source.index("sessions = calendar")]

    assert "不是交易日" in body and "run.skip" in body
