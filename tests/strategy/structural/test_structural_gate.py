"""S6 分类与 S7 Gate。"""

import pytest

from qsys.strategy.structural.gate import (
    Classification,
    classify,
    gate_allow,
    three_and,
    three_or,
)
from qsys.strategy.structural.params import (
    S6_HC_ADVANCE_NOT_LOW,
    S6_HC_TOUCHED_NOT_LOW,
    S6_HIGH_MAIN_BREADTH_HIGH,
    S6_LOW_DISTANCE60_HIGH,
    S6_LOW_HEAT_LOW_THRESHOLD,
    S6_LOW_STOCK_RET10_HIGH,
    S6_LOW_TREND_WEAK_THRESHOLD,
)


# ---------------- 三值逻辑（原文 L1891-L1906）----------------


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (True, True, True),
        (True, False, True),
        (False, True, True),
        (True, None, True),
        (None, True, True),
        (False, False, False),
        (False, None, None),
        (None, False, None),
        (None, None, None),
    ],
)
def test_three_or(a, b, expected):
    """True 支配；两个 False 才 False；否则未知。"""
    assert three_or(a, b) is expected


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (False, False, False),
        (False, True, False),
        (True, False, False),
        (False, None, False),
        (None, False, False),
        (True, True, True),
        (True, None, None),
        (None, True, None),
        (None, None, None),
    ],
)
def test_three_and(a, b, expected):
    """False 支配；两个 True 才 True；否则未知。"""
    assert three_and(a, b) is expected


# ---------------- LOW_MAIN ----------------


def low_env(slope, heat):
    return {"index_ma20_slope_5d": slope, "prior_limitup_nextday_return_mean": heat}


def low_factors(ret10, dist60):
    return {"stock_return_10d": ret10, "distance_to_60d_high": dist60}


def test_low_main_clean_when_neither_market_nor_stock_is_bad():
    cls = classify(
        "LOW_MAIN",
        low_env(S6_LOW_TREND_WEAK_THRESHOLD - 0.01, S6_LOW_HEAT_LOW_THRESHOLD - 0.01),
        low_factors(0.0, -0.5),
    )
    assert cls.state == "CLEAN"
    assert gate_allow("LOW_MAIN", cls) is True


def test_low_main_risk_when_the_market_is_bad():
    """趋势不弱且情绪高 —— 首板溢价被透支。"""
    cls = classify(
        "LOW_MAIN",
        low_env(S6_LOW_TREND_WEAK_THRESHOLD + 0.01, S6_LOW_HEAT_LOW_THRESHOLD + 0.01),
        low_factors(0.0, -0.5),
    )
    assert cls.state == "RISK"
    assert gate_allow("LOW_MAIN", cls) is False


def test_low_main_risk_when_the_stock_is_overheated():
    cls = classify(
        "LOW_MAIN",
        low_env(S6_LOW_TREND_WEAK_THRESHOLD - 0.01, S6_LOW_HEAT_LOW_THRESHOLD - 0.01),
        low_factors(S6_LOW_STOCK_RET10_HIGH + 0.01, S6_LOW_DISTANCE60_HIGH + 0.01),
    )
    assert cls.state == "RISK"


def test_low_main_unknown_allows_when_the_index_factor_is_missing():
    """缺数据放行，不是拒绝——原策略明确不因无法分类而拒单。"""
    cls = classify("LOW_MAIN", low_env(None, 0.01), low_factors(0.0, -0.5))
    assert cls.state == "UNKNOWN_ALLOW"
    assert cls.known is False
    assert gate_allow("LOW_MAIN", cls) is True


def test_low_main_risk_wins_over_missing_market_data():
    """个股已过热时，市场因子缺失不影响 RISK 判定（三值 OR 里 True 支配）。"""
    cls = classify(
        "LOW_MAIN",
        low_env(None, None),
        low_factors(S6_LOW_STOCK_RET10_HIGH + 0.01, S6_LOW_DISTANCE60_HIGH + 0.01),
    )
    assert cls.state == "RISK"


# ---------------- HIGH_MAIN ----------------


def test_high_main_favorable_on_strong_breadth():
    cls = classify(
        "HIGH_MAIN", {"market_median_return_1d": S6_HIGH_MAIN_BREADTH_HIGH + 0.001}, {}
    )
    assert cls.state == "FAVORABLE"
    assert gate_allow("HIGH_MAIN", cls) is True


def test_high_main_other_on_weak_breadth():
    cls = classify(
        "HIGH_MAIN", {"market_median_return_1d": S6_HIGH_MAIN_BREADTH_HIGH - 0.001}, {}
    )
    assert cls.state == "OTHER"
    assert gate_allow("HIGH_MAIN", cls) is False


def test_research_threshold_overrides_do_not_change_defaults():
    env = {"market_median_return_1d": S6_HIGH_MAIN_BREADTH_HIGH + 0.001}
    assert classify("HIGH_MAIN", env, {}).state == "FAVORABLE"
    overridden = classify(
        "HIGH_MAIN", env, {},
        thresholds={"high_main_breadth_high": S6_HIGH_MAIN_BREADTH_HIGH + 0.002},
    )
    assert overridden.state == "OTHER"


def test_high_main_unknown_allows():
    cls = classify("HIGH_MAIN", {"market_median_return_1d": None}, {})
    assert cls.state == "UNKNOWN_ALLOW"
    assert gate_allow("HIGH_MAIN", cls) is True


# ---------------- HIGH_CHINEXT ----------------


def hc_env(touched, advance):
    return {"touched_limit_up_count": touched, "market_advance_ratio": advance}


def test_high_chinext_needs_both_activity_and_breadth():
    """AND 语义：两个都要达标。"""
    cls = classify(
        "HIGH_CHINEXT",
        hc_env(S6_HC_TOUCHED_NOT_LOW + 1, S6_HC_ADVANCE_NOT_LOW + 0.01),
        {},
    )
    assert cls.state == "FAVORABLE"
    assert gate_allow("HIGH_CHINEXT", cls) is True


def test_high_chinext_rejects_when_only_one_holds():
    cls = classify(
        "HIGH_CHINEXT",
        hc_env(S6_HC_TOUCHED_NOT_LOW + 1, S6_HC_ADVANCE_NOT_LOW - 0.01),
        {},
    )
    assert cls.state == "OTHER"
    assert gate_allow("HIGH_CHINEXT", cls) is False


def test_high_chinext_false_dominates_missing_data():
    """三值 AND 里 False 支配：一个明确不达标就是 OTHER，不管另一个缺不缺。"""
    cls = classify(
        "HIGH_CHINEXT", hc_env(None, S6_HC_ADVANCE_NOT_LOW - 0.01), {}
    )
    assert cls.state == "OTHER"


def test_high_chinext_unknown_allows_when_both_missing():
    cls = classify("HIGH_CHINEXT", hc_env(None, None), {})
    assert cls.state == "UNKNOWN_ALLOW"
    assert gate_allow("HIGH_CHINEXT", cls) is True


# ---------------- LOW_CHINEXT：无 Gate ----------------


def test_low_chinext_has_no_gate():
    """样本不足，原策略保持基线，一律放行。"""
    cls = classify("LOW_CHINEXT", {}, {})
    assert cls.state == "UNCHANGED"
    assert gate_allow("LOW_CHINEXT", cls) is True


def test_an_unknown_tag_is_allowed():
    assert gate_allow("SOMETHING_ELSE", Classification("UNCHANGED", True, "")) is True
