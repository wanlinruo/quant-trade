"""三个退出决策，逐条对照原文 L615-L768。"""

import pytest

from qsys.strategy.structural.exits import (
    ExitDecision,
    dynamic_ma5,
    legacy_exit,
    low_1125,
    low_1425,
)


# ---------------- dynamic_ma5（原文 L615）----------------


def test_ma5_blends_four_closes_with_the_live_price():
    """(最近 4 日收盘均值 x 4 + 现价) / 5。"""
    assert dynamic_ma5([10.0, 10.0, 10.0, 10.0], 15.0) == pytest.approx(11.0)


def test_ma5_uses_only_the_last_four_closes():
    assert dynamic_ma5([99.0, 10.0, 10.0, 10.0, 10.0], 15.0) == pytest.approx(11.0)


def test_ma5_without_closes_is_none():
    assert dynamic_ma5([], 15.0) is None


# ---------------- legacy_exit：HIGH 用（原文 L624）----------------


def test_legacy_takes_profit_when_not_limit_up_and_in_profit():
    d = legacy_exit(last_price=11.0, limit_up=12.0, entry_price=10.0, ma5=10.5)
    assert d.should_exit is True
    assert d.reason == "TAKE_PROFIT"


def test_legacy_holds_at_limit_up_even_when_profitable():
    """涨停就不卖——排队也卖不掉，且明天可能继续涨。"""
    d = legacy_exit(last_price=12.0, limit_up=12.0, entry_price=10.0, ma5=10.5)
    assert d.should_exit is False


def test_legacy_stops_out_below_ma5():
    d = legacy_exit(last_price=9.0, limit_up=12.0, entry_price=10.0, ma5=9.5)
    assert d.should_exit is True
    assert d.reason == "MA5_STOP"


def test_take_profit_wins_over_ma5_stop():
    """现价既高于入场价又低于 MA5 时，原版走 TAKE_PROFIT。

    这个顺序看着像 bug，但原策略是 FROZEN 版本——照抄，不要"优化"。
    """
    d = legacy_exit(last_price=10.5, limit_up=12.0, entry_price=10.0, ma5=11.0)
    assert d.reason == "TAKE_PROFIT"


def test_legacy_holds_when_neither_condition_fires():
    d = legacy_exit(last_price=9.8, limit_up=12.0, entry_price=10.0, ma5=9.0)
    assert d.should_exit is False


def test_legacy_needs_an_entry_price():
    assert legacy_exit(11.0, 12.0, 0.0, 10.5).should_exit is False
    assert legacy_exit(11.0, 12.0, None, 10.5).should_exit is False


def test_legacy_without_ma5_only_checks_take_profit():
    """MA5 算不出来时不该把止损判成触发。"""
    assert legacy_exit(9.0, 12.0, 10.0, None).should_exit is False


def test_legacy_reports_the_return():
    d = legacy_exit(last_price=11.0, limit_up=12.0, entry_price=10.0, ma5=10.5)
    assert d.ret == pytest.approx(10.0)


# ---------------- low_1125（原文 L707）----------------


def test_low_1125_holds_at_limit_up():
    assert low_1125(last_price=11.0, limit_up=11.0, entry_price=10.0).should_exit is False


def test_low_1125_sells_when_profitable_and_not_limit_up():
    d = low_1125(last_price=10.5, limit_up=11.0, entry_price=10.0)
    assert d.should_exit is True
    assert d.reason == "LW_1125_PROFIT_NON_LIMIT"


def test_low_1125_waits_when_not_profitable():
    """不盈利就等到 14:25，不是当场割。"""
    d = low_1125(last_price=9.5, limit_up=11.0, entry_price=10.0)
    assert d.should_exit is False


def test_low_1125_flat_is_not_profit():
    """严格大于才算盈利。"""
    assert low_1125(10.0, 11.0, 10.0).should_exit is False


# ---------------- low_1425（原文 L742）----------------


def test_low_1425_holds_overnight_at_limit_up():
    assert low_1425(last_price=11.0, limit_up=11.0, entry_price=10.0).should_exit is False


def test_low_1425_sells_unconditionally_when_not_limit_up():
    """盈亏都卖——这是 LOW 分支的硬退出。"""
    for last in (9.0, 10.0, 10.9):
        d = low_1425(last_price=last, limit_up=11.0, entry_price=10.0)
        assert d.should_exit is True, last
        assert d.reason == "LW_1425_NON_LIMIT_EXIT"


def test_low_decisions_need_an_entry_price():
    assert low_1125(10.5, 11.0, None).should_exit is False
    assert low_1425(9.0, 11.0, 0.0).should_exit is False


def test_a_missing_limit_up_price_is_treated_as_not_limit_up():
    """拿不到涨停价时按「未涨停」处理——LOW 的 14:25 是无条件卖，
    误判成涨停会让仓位莫名过夜。"""
    assert low_1425(9.0, None, 10.0).should_exit is True


def test_decisions_are_immutable():
    with pytest.raises(Exception):
        ExitDecision(True, "X", 1.0).reason = "Y"
