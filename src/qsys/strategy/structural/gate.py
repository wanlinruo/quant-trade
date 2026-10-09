"""S6 分类与 S7 Gate，对照原文 L1876-L2005。

**缺数据一律放行**，不是拒绝。原策略写得很明白：
「preserve baseline rather than reject an unclassifiable trade」——
分类不出来时保持基线行为，而不是把这单毙掉。

三值逻辑是这里最容易写错的地方：
  OR  —— True 支配；两个 False 才 False；否则未知
  AND —— False 支配；两个 True 才 True；否则未知
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from qsys.strategy.structural.params import (
    S6_HC_ADVANCE_NOT_LOW,
    S6_HC_TOUCHED_NOT_LOW,
    S6_HIGH_MAIN_BREADTH_HIGH,
    S6_LOW_DISTANCE60_HIGH,
    S6_LOW_HEAT_LOW_THRESHOLD,
    S6_LOW_STOCK_RET10_HIGH,
    S6_LOW_TREND_WEAK_THRESHOLD,
)


@dataclass(frozen=True, slots=True)
class Classification:
    state: str
    known: bool
    reason: str


def three_or(a: bool | None, b: bool | None) -> bool | None:
    """原文 L1891：True 支配；两个 False 才 False；否则未知。"""
    if a is True or b is True:
        return True
    if a is False and b is False:
        return False
    return None


def three_and(a: bool | None, b: bool | None) -> bool | None:
    """原文 L1900：False 支配；两个 True 才 True；否则未知。"""
    if a is False or b is False:
        return False
    if a is True and b is True:
        return True
    return None


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number  # NaN


def classify(
    tag: str,
    env: Mapping[str, Any],
    stock_factors: Mapping[str, Any],
    *,
    thresholds: Mapping[str, float] | None = None,
) -> Classification:
    """冻结的结构化分类。绝不改变候选列表本身，只给 Gate 用。"""
    if tag == "LOW_MAIN":
        return _classify_low_main(env, stock_factors, thresholds or {})
    if tag == "HIGH_MAIN":
        return _classify_high_main(env, thresholds or {})
    if tag == "HIGH_CHINEXT":
        return _classify_high_chinext(env, thresholds or {})
    # LOW_CHINEXT 等样本不足的分支保持基线
    return Classification("UNCHANGED", True, "SAMPLE_INSUFFICIENT_KEEP_BASELINE")


def _classify_low_main(
    env: Mapping[str, Any], stock_factors: Mapping[str, Any],
    thresholds: Mapping[str, float],
) -> Classification:
    slope = _num(env.get("index_ma20_slope_5d"))
    heat = _num(env.get("prior_limitup_nextday_return_mean"))
    ret10 = _num(stock_factors.get("stock_return_10d"))
    dist60 = _num(stock_factors.get("distance_to_60d_high"))

    market_bad = (
        None
        if slope is None or heat is None
        else bool(
            slope > thresholds.get("low_trend_weak", S6_LOW_TREND_WEAK_THRESHOLD)
            and heat > thresholds.get("low_heat_low", S6_LOW_HEAT_LOW_THRESHOLD)
        )
    )
    stock_bad = (
        None
        if ret10 is None or dist60 is None
        else bool(ret10 >= S6_LOW_STOCK_RET10_HIGH and dist60 >= S6_LOW_DISTANCE60_HIGH)
    )
    risk = three_or(market_bad, stock_bad)

    if risk is True:
        reasons = []
        if market_bad is True:
            reasons.append("MARKET_BAD_NOT_WEAK_HIGH_HEAT")
        if stock_bad is True:
            reasons.append("STOCK_OVERHEAT_RET10_HIGH_NEAR_60D_HIGH")
        return Classification("RISK", True, "|".join(reasons))
    if risk is False:
        return Classification("CLEAN", True, "LOW_MAIN_CLEAN")
    return Classification("UNKNOWN_ALLOW", False, "LOW_MAIN_DATA_UNKNOWN_ALLOW")


def _classify_high_main(
    env: Mapping[str, Any], thresholds: Mapping[str, float]
) -> Classification:
    breadth = _num(env.get("market_median_return_1d"))
    if breadth is None:
        return Classification("UNKNOWN_ALLOW", False, "BREADTH_UNKNOWN_ALLOW")
    if breadth > thresholds.get(
        "high_main_breadth_high", S6_HIGH_MAIN_BREADTH_HIGH
    ):
        return Classification("FAVORABLE", True, "BREADTH_STRONG")
    return Classification("OTHER", True, "BREADTH_OTHER")


def _classify_high_chinext(
    env: Mapping[str, Any], thresholds: Mapping[str, float]
) -> Classification:
    touched = _num(env.get("touched_limit_up_count"))
    advance = _num(env.get("market_advance_ratio"))
    a = None if touched is None else bool(touched > S6_HC_TOUCHED_NOT_LOW)
    b = None if advance is None else bool(
        advance > thresholds.get(
            "high_chinext_advance_not_low", S6_HC_ADVANCE_NOT_LOW
        )
    )
    favorable = three_and(a, b)

    if favorable is True:
        return Classification("FAVORABLE", True, "ACTIVITY_AND_BREADTH_OK")
    if favorable is False:
        return Classification("OTHER", True, "ACTIVITY_OR_BREADTH_LOW")
    return Classification("UNKNOWN_ALLOW", False, "HC_ENV_UNKNOWN_ALLOW")


def gate_allow(tag: str, classification: Classification) -> bool:
    """S7 冻结 Gate（原文 L1997-L2005）。

    UNKNOWN_ALLOW 一律放行——保持基线优先于拒掉一笔无法分类的交易。
    """
    if classification.state == "UNKNOWN_ALLOW":
        return True
    if tag == "LOW_MAIN":
        return classification.state == "CLEAN"
    if tag in ("HIGH_MAIN", "HIGH_CHINEXT"):
        return classification.state == "FAVORABLE"
    # LOW_CHINEXT 等无 Gate
    return True
