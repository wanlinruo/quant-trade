"""付费分钟线的年度验收与日期覆盖规则。

年度分片的规则只读发布时生成的质量摘要，不在每日验证里重扫 16 亿行。摘要由
``pipeline.paid_minute.validate_year`` 从事实分片计算，事实、隔离证据和摘要
三者均保留。结构问题阻断；尚未归因的跨源数值差异挂 D-20、只报警告。

``year=YYYY/date=YYYYMMDD`` 日期覆盖是从付费暂存区挑出、替换 QMT 零价格记录的
标的日，不在年度摘要里；它们体量很小，每日直接复验。
"""

from __future__ import annotations

import json
from datetime import date, datetime
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
from qsys.data.pipeline.minute_repairs import compare_with_daily, valid_paid_overrides


def _summaries(ctx: CheckContext) -> list[tuple[Path, dict]]:
    paths = sorted(
        (ctx.warehouse.root / "paid_minute_quality").glob(
            "year=*/quality-summary.json"
        )
    )
    return [(path, json.loads(path.read_text())) for path in paths]


def _structure(ctx: CheckContext) -> CheckResult:
    summaries = _summaries(ctx)
    problems: list[dict[str, object]] = []
    summarized = {int(report["year"]) for _path, report in summaries}
    for year_dir in sorted((ctx.warehouse.root / "paid_minute_bars").glob("year=*")):
        year = year_dir.name.split("=", 1)[1]
        if any(year_dir.glob("shard=*/part.parquet")) and \
                not (year.isdigit() and int(year) in summarized):
            problems.append({"year": year, "problem": "年度分片没有年度质量摘要"})
    if not summaries and not problems:
        return CheckResult(
            0, 0, "尚无付费分钟线年度质量摘要", applicable=False
        )
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
        len(problems), max(checked, len(summaries), 1),
        f"{len(summaries)} 个年度摘要，结构/到货异常 {len(problems)} 项",
        pl.DataFrame(problems) if problems else None,
    )


def _override_structure(ctx: CheckContext) -> CheckResult:
    """日期覆盖逐标的日复验：241 根标准网格、价格和成交额有效、日期与分区一致。"""
    problems: list[dict[str, object]] = []
    checked = 0
    for path in sorted((ctx.warehouse.root / "paid_minute_bars").glob(
            "year=*/date=*/part.parquet")):
        day = datetime.strptime(path.parent.name.split("=", 1)[1], "%Y%m%d").date()
        frame = pl.read_parquet(path)
        symbols = sorted(frame["symbol"].unique().to_list())
        checked += len(symbols)
        name = str(path.relative_to(ctx.warehouse.root))
        stray = frame.filter(pl.col("dt").dt.date() != day).height
        if stray:
            problems.append({"path": name, "symbol": "*",
                             "problem": f"混入其他交易日 {stray} 行"})
        _accepted, rejected = valid_paid_overrides(frame, symbols, day)
        problems.extend({"path": name, "symbol": symbol, "problem": reason}
                        for symbol, reason in sorted(rejected.items()))
    if not checked:
        return CheckResult(0, 0, "没有付费分钟日期覆盖", applicable=False)
    return CheckResult(
        len(problems), checked,
        f"日期覆盖 {checked} 个标的日，结构异常 {len(problems)} 项",
        pl.DataFrame(problems) if problems else None,
    )


def _override_daily_comparison(ctx: CheckContext) -> pl.DataFrame | None:
    files = sorted((ctx.warehouse.root / "paid_minute_bars").glob("year=*/date=*/part.parquet"))
    if not files:
        return None
    minutes = pl.concat([pl.read_parquet(path) for path in files], how="vertical_relaxed")
    days = minutes["dt"].dt.date()
    daily = ctx.query.tushare_daily(
        sorted(minutes["symbol"].unique().to_list()), days.min(), days.max(),
        columns=("symbol", "dt", "open", "high", "low", "close", "volume", "amount"),
    )
    return compare_with_daily(minutes, daily)


def _override_vs_daily(ctx: CheckContext, *, gross: bool) -> CheckResult:
    """日期覆盖逐标的日与 Tushare 日线对账。

    粗大错误（缺日线、成交量/成交额/价格相对偏差超过 5%）阻断；取整与首末分钟
    边界这类小差异与年度付费分钟同口径，挂 D-20 报警告。
    """
    result = _override_daily_comparison(ctx)
    if result is None:
        return CheckResult(0, 0, "没有付费分钟日期覆盖", applicable=False)
    flagged = result.filter(pl.col("gross").is_not_null()) if gross \
        else result.filter(pl.col("minor"))
    label = "粗大偏差" if gross else "超出年度验收容差"
    return CheckResult(
        flagged.height, result.height,
        f"日期覆盖 {result.height} 个标的日与 Tushare 日线对账，{label} {flagged.height} 个",
        flagged if flagged.height else None,
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


# D-21：卖家 2020—2025 每个年度包都没有这 13 只沪市证券的成员文件，无法从源包补回；
# 2025-06-12 起已由聚宽覆盖。单独一条规则挂已知缺陷报警告，名单外的缺失仍阻断。
D21_MISSING_SYMBOLS = frozenset({
    "603211.SH", "603213.SH", "603214.SH", "603215.SH", "603216.SH", "603217.SH",
    "603219.SH", "603220.SH", "603221.SH", "603222.SH", "603223.SH", "603226.SH",
    "603227.SH",
})


def _symbol_coverage(ctx: CheckContext, *, known: bool) -> CheckResult:
    """用独立 Tushare 日线发现源包整只沪深证券缺失，不重扫分钟事实。

    ``known=False`` 只报 D-21 名单之外的缺失（阻断），``known=True`` 只报名单内的
    缺失（挂 D-21 警告）；两条规则分母相同。

    北交所历史代码会被 Tushare 映射成当前 920 号段，付费源则保留当时的
    43/83/87 代码；``302132.SZ`` 也存在同类换码。两者不能直接按 symbol
    做反连接，因此这里只比较 2020—2025 期间身份稳定的沪深股票号段。
    """
    summaries = _summaries(ctx)
    if not summaries:
        return CheckResult(0, 0, "尚无付费分钟线年度质量摘要", applicable=False)
    problems: list[dict[str, object]] = []
    checked = 0
    compared_years = 0
    for _path, report in summaries:
        year = int(report["year"])
        aggregate = ctx.warehouse.root / "paid_minute_quality" / f"year={year}" \
            / "daily_aggregate.parquet"
        if not aggregate.exists():
            problems.append({"year": year, "symbol": "*", "traded_days": None,
                             "problem": "缺少分钟日聚合质量产物"})
            continue
        daily = ctx.query.tushare_daily(
            None, date(year, 1, 1), date(year, 12, 31),
            columns=("symbol", "dt", "volume"),
        )
        symbol = pl.col("symbol")
        prefix = symbol.str.slice(0, 3)
        comparable = (
            symbol.str.ends_with(".SH")
            & prefix.is_in(["600", "601", "603", "605", "688", "689"])
        ) | (
            symbol.str.ends_with(".SZ")
            & prefix.is_in(["000", "001", "002", "003", "300", "301"])
        )
        daily = daily.filter((pl.col("volume") > 0) & comparable)
        if daily.is_empty():
            continue
        compared_years += 1
        traded = daily.group_by("symbol").agg(pl.len().alias("traded_days"))
        minute_symbols = pl.read_parquet(aggregate, columns=["symbol"]).select(
            "symbol"
        ).unique()
        missing = traded.join(minute_symbols, on="symbol", how="anti").sort("symbol")
        listed = pl.col("symbol").is_in(list(D21_MISSING_SYMBOLS))
        missing = missing.filter(listed if known else ~listed)
        checked += traded.height
        problems.extend(
            {"year": year, "symbol": row["symbol"],
             "traded_days": row["traded_days"], "problem": "全年分钟证券缺失"}
            for row in missing.iter_rows(named=True)
        )
    if not compared_years and not problems:
        return CheckResult(
            0, 0, "没有与付费分钟年度重叠的 Tushare 日线", applicable=False
        )
    return CheckResult(
        len(problems), max(checked, len(summaries)),
        f"{compared_years} 个年度与 Tushare 成交证券对账，全年分钟证券缺失 "
        f"{len(problems)} 只",
        pl.DataFrame(problems) if problems else None,
    )


_ALL = tuple(f"paid_minute_bars.{name}" for name in (
    "symbol", "dt", "open", "high", "low", "close", "volume", "amount", "factor"
))

register(Check(
    "B2.paid_minute_structure", Category.COMPLETENESS, Scope.FULL,
    _ALL, (), "年度分片均有摘要且已发布、交易时段完整、日级键不缺失、因子日内唯一",
    _structure,
))
register(Check(
    "B2.paid_minute_override_structure", Category.COMPLETENESS, Scope.FULL,
    _ALL, (), "日期覆盖逐标的日满足 241 根标准网格与数值有效", _override_structure,
))
register(Check(
    "B2.paid_minute_symbol_coverage", Category.COMPLETENESS, Scope.FULL,
    _ALL, (), "Tushare 有成交的证券不能在付费分钟年度中整只缺失（D-21 名单除外）",
    lambda ctx: _symbol_coverage(ctx, known=False),
))
register(Check(
    "B2.paid_minute_known_symbol_gap", Category.COMPLETENESS, Scope.FULL,
    _ALL, ("D-21",), "D-21：卖家源包缺失的 13 只沪市证券仍然缺失",
    lambda ctx: _symbol_coverage(ctx, known=True),
))
_VALUES = tuple(f"paid_minute_bars.{name}" for name in (
    "open", "high", "low", "close", "volume", "amount"
))

register(Check(
    "A3.paid_minute_override_gross_vs_daily", Category.TRUTHFULNESS, Scope.FULL,
    _VALUES, (), "日期覆盖与日线无粗大偏差（缺日线或量/额/价相对偏差超过 5%）",
    lambda ctx: _override_vs_daily(ctx, gross=True),
))
register(Check(
    "A3.paid_minute_override_vs_daily", Category.TRUTHFULNESS, Scope.FULL,
    _VALUES, ("D-20",), "日期覆盖汇总与日线在年度验收容差内一致",
    lambda ctx: _override_vs_daily(ctx, gross=False),
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
