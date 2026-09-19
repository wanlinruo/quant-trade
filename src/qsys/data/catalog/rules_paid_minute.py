"""冻结付费分钟线的年度验收规则。

规则只读发布时生成的质量摘要，不在每日验证里重扫 16 亿行。摘要由
``pipeline.paid_minute.validate_year`` 从事实分片计算，事实、隔离证据和摘要
三者均保留。结构问题阻断；尚未归因的跨源数值差异挂 D-20、只报警告。
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from qsys.data.catalog.checks import (
    Category,
    Check,
    CheckContext,
    CheckResult,
    Scope,
    register,
)


def _summaries(ctx: CheckContext) -> list[tuple[Path, dict]]:
    paths = sorted(
        (ctx.warehouse.root / "paid_minute_quality").glob(
            "year=*/quality-summary.json"
        )
    )
    return [(path, json.loads(path.read_text())) for path in paths]


def _structure(ctx: CheckContext) -> CheckResult:
    summaries = _summaries(ctx)
    if not summaries:
        return CheckResult(
            0, 0, "尚无付费分钟线年度质量摘要", applicable=False
        )
    problems: list[dict[str, object]] = []
    checked = 0
    for _path, report in summaries:
        year = report["year"]
        structural = report["structural"]
        daily = report["daily_comparison"]
        checked += int(daily["stock_days"])
        expectations = {
            "status": report["status"] == "PUBLISHED",
            "invalid_session_times": structural["invalid_session_times"] == 0,
            "missing_paid_days": daily["missing_paid_days"] == 0,
            "non_241_regular_days": daily["non_241_regular_days"] == 0,
            "factor_not_unique_days": daily["factor_not_unique_days"] == 0,
            "factor_mismatch_days": daily["factor_mismatch_days"] == 0,
        }
        problems.extend(
            {"year": year, "problem": name}
            for name, passed in expectations.items()
            if not passed
        )
    return CheckResult(
        len(problems), max(checked, len(summaries)),
        f"{len(summaries)} 个年度摘要，结构/到货异常 {len(problems)} 项",
        pl.DataFrame(problems) if problems else None,
    )


def _difference(ctx: CheckContext, key: str, label: str) -> CheckResult:
    summaries = _summaries(ctx)
    if not summaries:
        return CheckResult(0, 0, "尚无付费分钟线年度质量摘要", applicable=False)
    rows = []
    checked = 0
    violations = 0
    for _path, report in summaries:
        daily = report["daily_comparison"]
        count = int(daily[key])
        checked += int(daily["matched_paid_days"])
        violations += count
        if count:
            rows.append({"year": report["year"], "violations": count})
    return CheckResult(
        violations, max(checked, len(summaries)),
        f"付费分钟聚合与同源日线的{label}差异 {violations} 个股日",
        pl.DataFrame(rows) if rows else None,
    )


_ALL = tuple(f"paid_minute_bars.{name}" for name in (
    "symbol", "dt", "open", "high", "low", "close", "volume", "amount", "factor"
))

register(Check(
    "B2.paid_minute_structure", Category.COMPLETENESS, Scope.FULL,
    _ALL, (), "年度已发布、交易时段完整、日级键不缺失且因子日内唯一", _structure,
))
register(Check(
    "A3.paid_minute_ohlc_vs_daily", Category.TRUTHFULNESS, Scope.FULL,
    tuple(f"paid_minute_bars.{name}" for name in ("open", "high", "low", "close")),
    ("D-20",), "分钟聚合 OHLC 与付费日线一致",
    lambda ctx: _difference(ctx, "ohlc_mismatch_days", "OHLC"),
))
register(Check(
    "A3.paid_minute_volume_vs_daily", Category.TRUTHFULNESS, Scope.FULL,
    ("paid_minute_bars.volume",), ("D-20",), "分钟成交量汇总与付费日线一致",
    lambda ctx: _difference(ctx, "volume_mismatch_days", "成交量"),
))
register(Check(
    "A3.paid_minute_amount_vs_daily", Category.TRUTHFULNESS, Scope.FULL,
    ("paid_minute_bars.amount",), ("D-20",), "分钟成交额汇总与付费日线一致",
    lambda ctx: _difference(ctx, "amount_mismatch_gt_100_days", "成交额"),
))
