"""聚宽交叉验证对照工具。

三层控制变量（spec §8.2）：
  L1 数据比对        —— 数据未对齐时，任何净值差异都无法归因，必须先做完
  L2 固定数据比引擎  —— 用聚宽数据喂自建引擎，差异纯粹来自撮合/费用/成交时点
  L3 各用各的数据    —— 差异应约等于 L1 + L2

残差必须收敛到可解释范围，否则引擎不通过验收。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import polars as pl

# 引擎验收阈值：L2 的最终净值差异超过此值即判定未通过
L2_FINAL_DIFF_THRESHOLD = 0.005


@dataclass(frozen=True, slots=True)
class PriceDiff:
    n_rows: int
    n_mismatched: int
    max_abs_diff: float
    mismatch_rate: float
    samples: pl.DataFrame


@dataclass(frozen=True, slots=True)
class EquityDiff:
    final_value_ours: float
    final_value_theirs: float
    final_diff_pct: float
    max_diff_pct: float
    correlation: float


def compare_prices(
    ours: pl.DataFrame, theirs: pl.DataFrame, *, tolerance: float = 0.005
) -> PriceDiff:
    """L1：逐字段比对收盘价。只比对两侧都有的行。"""
    joined = ours.join(theirs, on=["symbol", "dt"], how="inner", suffix="_theirs")
    if joined.height == 0:
        return PriceDiff(0, 0, 0.0, 0.0, joined)

    joined = joined.rename({"close": "close_ours"}).with_columns(
        (pl.col("close_ours") - pl.col("close_theirs")).abs().alias("abs_diff")
    )
    bad = joined.filter(pl.col("abs_diff") > tolerance)
    return PriceDiff(
        n_rows=joined.height,
        n_mismatched=bad.height,
        max_abs_diff=float(joined["abs_diff"].max() or 0.0),
        mismatch_rate=bad.height / joined.height,
        samples=bad.head(20),
    )


def compare_equity(ours: pl.DataFrame, theirs: pl.DataFrame) -> EquityDiff:
    """L2 / L3：比对净值曲线。"""
    joined = ours.join(theirs, on="dt", how="inner", suffix="_theirs").rename(
        {"total_value": "value_ours"}
    )
    if joined.height == 0:
        return EquityDiff(0.0, 0.0, 0.0, 0.0, 0.0)

    a = joined["value_ours"].to_list()
    b = joined["total_value_theirs"].to_list()

    diffs = [abs(x - y) / y for x, y in zip(a, b, strict=True) if y != 0]
    final_diff = abs(a[-1] - b[-1]) / b[-1] if b[-1] != 0 else 0.0

    mean_a, mean_b = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    denom = math.sqrt(var_a * var_b)
    correlation = cov / denom if denom > 0 else 0.0

    return EquityDiff(
        final_value_ours=a[-1],
        final_value_theirs=b[-1],
        final_diff_pct=final_diff,
        max_diff_pct=max(diffs) if diffs else 0.0,
        correlation=correlation,
    )


def format_report(price_diff: PriceDiff, equity_diff: EquityDiff) -> str:
    verdict = (
        "通过" if equity_diff.final_diff_pct <= L2_FINAL_DIFF_THRESHOLD else "未通过"
    )
    lines = [
        "=" * 64,
        "聚宽交叉验证报告",
        "=" * 64,
        "",
        "L1 数据比对（数据未对齐时净值差异无法归因，必须先看这里）",
        f"  比对行数      : {price_diff.n_rows}",
        f"  不一致行数    : {price_diff.n_mismatched}",
        f"  不一致比例    : {price_diff.mismatch_rate:.4%}",
        f"  最大绝对差    : {price_diff.max_abs_diff:.4f}",
        "",
        "L2 固定数据比引擎（差异纯粹来自撮合 / 费用 / 成交时点）",
        f"  自建最终净值  : {equity_diff.final_value_ours:,.2f}",
        f"  聚宽最终净值  : {equity_diff.final_value_theirs:,.2f}",
        f"  最终净值差异  : {equity_diff.final_diff_pct:.4%}",
        f"  单日最大差异  : {equity_diff.max_diff_pct:.4%}",
        f"  净值曲线相关性: {equity_diff.correlation:.6f}",
        "",
        f"引擎验收（阈值 {L2_FINAL_DIFF_THRESHOLD:.2%}）: {verdict}",
        "",
        "残差排查清单：",
        "  成交时点   —— 聚宽默认在哪根 bar 成交？是否对齐 T 信号 / T+1 开盘",
        "  费用口径   —— 佣金最低 5 元、印花税、过户费是否逐项对齐",
        "  涨跌停判定 —— 聚宽用何价判涨停？是否允许涨停板买入",
        "  成交量约束 —— 聚宽默认有无成交量占比限制",
        "  复权口径   —— 前复权基准日不同会导致净值曲线整体缩放",
        "=" * 64,
    ]
    return "\n".join(lines)
