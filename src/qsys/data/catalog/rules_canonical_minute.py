"""规范一分钟线的补数纳入规则。

``bar_minute_1m`` 按 paid > Tushare 覆盖 > 聚宽覆盖 > QMT 逐标的日选源。覆盖数据集
（``tushare_minute_bars``、``joinquant_minute_bars``）是经过验证才发布的补数，
每个覆盖标的日都必须出现在规范表里，且来源只能是覆盖本身或优先级更高的来源，
不能缺失，也不能退回 QMT。

2026-09-22 发现 2025 年规范表漏掉了聚宽补的 1,787 个标的日（D-21 的 13 只沪市
证券），此前没有任何规则覆盖这一点。覆盖集体量很小，这里逐日只读规范分区的
symbol/source 两列，不重扫分钟事实，也不受当年范围限制。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import polars as pl

from qsys.data import schema
from qsys.data.catalog.checks import (
    Category,
    Check,
    CheckContext,
    CheckResult,
    Scope,
    register,
)

# 覆盖数据集 -> 规范表 source 取值中允许出现的来源（覆盖本身及更高优先级）
OVERRIDES = {
    "tushare_minute_bars": ("paid", "tushare"),
    "joinquant_minute_bars": ("paid", "tushare", "joinquant"),
}
_SOURCE = {"tushare_minute_bars": "tushare", "joinquant_minute_bars": "joinquant"}


def _partitions(root: Path) -> list[tuple[str, Path]]:
    return sorted(
        (path.parent.name.split("=", 1)[1], path)
        for path in root.glob("year=*/date=*/part.parquet")
    )


def override_inclusion(ctx: CheckContext) -> CheckResult:
    base = ctx.warehouse.root
    problems: list[dict[str, object]] = []
    checked = 0
    for dataset, allowed in OVERRIDES.items():
        for stamp, path in _partitions(base / dataset):
            wanted = pl.read_parquet(path, columns=["symbol"])["symbol"].unique().to_list()
            if not wanted:
                continue
            checked += len(wanted)
            day = datetime.strptime(stamp, "%Y%m%d").date()
            canonical = base / "bar_minute_1m" / f"year={day.year}" / f"date={stamp}" \
                / "part.parquet"
            if canonical.exists():
                present = (
                    pl.scan_parquet(canonical)
                    .select("symbol", "source")
                    .filter(pl.col("symbol").is_in(wanted))
                    .unique()
                    .collect()
                )
            else:
                present = pl.DataFrame(schema={"symbol": pl.Utf8, "source": pl.Utf8})
            sources = dict(zip(present["symbol"].to_list(), present["source"].to_list()))
            for symbol in sorted(wanted):
                source = sources.get(symbol)
                if source in allowed:
                    continue
                problems.append({
                    "dataset": dataset, "dt": day, "symbol": symbol,
                    "canonical_source": source,
                    "problem": "规范表缺少该覆盖标的日" if source is None
                    else f"规范表选了 {source}，应为 {_SOURCE[dataset]} 或更高优先级",
                })
    if not checked:
        return CheckResult(0, 0, "没有已发布的分钟覆盖数据", applicable=False)
    sample = pl.DataFrame(problems).head(50) if problems else None
    detail = f"{checked:,} 个覆盖标的日，{len(problems):,} 个未按优先级进入规范表"
    if problems:
        missing = pl.DataFrame(problems)
        detail += "（" + "；".join(
            f"{row['dataset']} {row['n']:,} 个，{row['d0']}～{row['d1']}"
            for row in missing.group_by("dataset").agg(
                pl.len().alias("n"), pl.col("dt").min().alias("d0"),
                pl.col("dt").max().alias("d1"),
            ).sort("dataset").iter_rows(named=True)
        ) + "）"
    return CheckResult(len(problems), checked, detail, sample)


register(Check(
    "B2.canonical_minute_override_inclusion", Category.COMPLETENESS, Scope.FULL,
    tuple(f"bar_minute_1m.{name}" for name in schema.BAR_MINUTE_1M.names), (),
    "Tushare/聚宽分钟覆盖的每个标的日都按优先级进入规范一分钟线",
    override_inclusion,
))
