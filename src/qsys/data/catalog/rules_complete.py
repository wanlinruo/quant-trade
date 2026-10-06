"""B 类完整性规则：日历、内部空洞、双口径空值、到货与跨度。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import polars as pl

from qsys.data.catalog.checks import Category, Check, CheckContext, CheckResult, Scope, register
from qsys.data.pipeline.auction import missing_auction_days
from qsys.data.pipeline.candidates import candidate_stock_days
from qsys.data.quality.daily_rules import find_holes

_ROW_NULL_MAX = .02
_SYMBOL_NULL_MAX = .005
_LIMIT_LIST_SLA = time(19, 0)
# qsys-daily-archive 工作日 15:40 采当日竞价 tick，正常 1 分钟内完成
_AUCTION_SLA = time(16, 0)
# 候选由前一交易日首板推出；回看要跨过春节这类长假才能找到前一交易日
_CANDIDATE_LOOKBACK = timedelta(days=30)


def _now() -> datetime:
    """单独封装挂钟，保证到货 SLA 边界可测试。"""
    return datetime.now()


def _calendar(ctx: CheckContext) -> CheckResult:
    daily = ctx.query.tushare_daily(None, ctx.start, ctx.end, columns=["symbol", "dt"])
    flags = ctx.query.daily_flags(ctx.start, ctx.end)
    regime = ctx.query.market_regime(ctx.start, ctx.end)
    valuation = ctx.query.valuation(ctx.start, ctx.end)
    price_limits = ctx.query.price_limits(None, ctx.start, ctx.end)
    reference = set(daily["dt"].unique().to_list()) if daily.height else set()
    if not reference:
        # 冷启动夹具或日线未到时，至少检查已有派生表自身的日期集合。
        reference = set(flags["dt"].unique().to_list()) | set(regime["dt"].unique().to_list())
    if not reference:
        return CheckResult(0, 0, "区间内没有可建立日历基准的数据")
    bad: list[dict[str, object]] = []
    for name, frame, require_each in (
        ("daily_flags", flags, True), ("market_regime", regime, True),
        ("valuation_snapshot", valuation, False),
        ("price_limits_daily", price_limits, True),
    ):
        dates = set(frame["dt"].unique().to_list()) if frame.height else set()
        for day in sorted(dates - reference):
            bad.append({"dataset": name, "dt": day, "kind": "非交易日"})
        if require_each and daily.height:
            for day in sorted(reference - dates):
                bad.append({"dataset": name, "dt": day, "kind": "缺交易日"})
    sample = pl.DataFrame(bad) if bad else None
    return CheckResult(len(bad), len(reference), f"日历基准 {len(reference)} 个交易日，异常 {len(bad)}", sample)


register(Check("B1.calendar", Category.COMPLETENESS, Scope.INCREMENTAL,
               ("tushare_daily.dt", "daily_flags.dt", "market_regime.dt",
                "valuation_snapshot.dt", "price_limits_daily.dt"), (),
               "日频表日期与仓库交易日基准对齐", _calendar))


def _holes(ctx: CheckContext) -> CheckResult:
    daily = ctx.query.tushare_daily(None, ctx.start, ctx.end, columns=["symbol", "dt"])
    sessions = sorted(daily["dt"].unique().to_list()) if daily.height else []
    report = find_holes(ctx.warehouse, ctx.start, ctx.end, sessions=sessions or None)
    # 停牌日本来就没有日线：suspend_d 直取记录为 S（停牌）的股-日不算空洞。
    # 复牌 R 不能解释缺 bar；suspend_daily 从 2022-08-04 起才有，更早的空洞照实报。
    suspended = ctx.query.suspensions(None, ctx.start, ctx.end).filter(
        pl.col("suspend_type") == "S"
    ).select("symbol", "dt").unique()
    holes = report.stock_days.join(suspended, on=["symbol", "dt"], how="anti")
    explained = report.total - holes.height
    return CheckResult(
        holes.height, daily.height,
        f"发现 {holes.height} 个无法解释的内部股-日空洞（另有 {explained} 个为直取停牌日，已扣除）",
        holes.head(5000) if holes.height else None,
    )


register(Check("B2.holes", Category.COMPLETENESS, Scope.FULL,
               ("tushare_daily.dt", "tushare_daily.symbol"), (),
               "每只标的首末记录之间不应有无法解释的交易日空洞（直取停牌日除外）", _holes))


def _null_rate(ctx: CheckContext) -> CheckResult:
    frame = ctx.query.daily_flags(ctx.start, ctx.end)
    if not frame.height:
        return CheckResult(0, 0, "区间内无 daily_flags")
    nulls = frame.filter(pl.col("is_st").is_null())
    row_rate = nulls.height / frame.height
    total_symbols = frame["symbol"].n_unique()
    null_symbols = nulls["symbol"].n_unique()
    symbol_rate = null_symbols / total_symbols if total_symbols else 0
    violations = max(nulls.height if row_rate > _ROW_NULL_MAX else 0,
                     null_symbols if symbol_rate > _SYMBOL_NULL_MAX else 0)
    detail = (f"is_st 空值：按行 {row_rate:.2%}（阈值 {_ROW_NULL_MAX:.0%}）；"
              f"按标的 {null_symbols}/{total_symbols}={symbol_rate:.2%}"
              f"（阈值 {_SYMBOL_NULL_MAX:.1%}）")
    return CheckResult(violations, frame.height, detail,
                       nulls.head(5000) if nulls.height else None)


register(Check("B3.null_rate", Category.COMPLETENESS, Scope.INCREMENTAL,
               ("daily_flags.is_st", "daily_flags.symbol", "tushare_daily.close",
                "tushare_daily.open", "tushare_daily.high", "tushare_daily.low",
                "tushare_daily.volume", "tushare_daily.amount", "tushare_daily.pre_close",
                "valuation_snapshot.close", "valuation_snapshot.symbol",
                "market_regime.valid_stock_count"), ("D-10",),
               "空值率同时按行和受影响标的统计", _null_rate))


def _auction_misses(ctx: CheckContext) -> tuple[list[dict[str, object]], int]:
    """首板次日应有竞价 tick（D-7）。QMT 只留约 20 个交易日，缺了就永久缺。

    期望集合与 daily_archive 的下载清单同源（``candidate_stock_days``），
    只扣掉当日直取停牌的标的——停牌没有竞价，不是缺货。
    """
    wanted = candidate_stock_days(ctx.warehouse, ctx.start - _CANDIDATE_LOOKBACK, ctx.end)
    wanted = wanted.filter(pl.col("dt").is_between(ctx.start, ctx.end))
    now = _now()
    if now.time() < _AUCTION_SLA:
        wanted = wanted.filter(pl.col("dt") != now.date())
    if not wanted.height:
        return [], 0
    suspended = (ctx.query.suspensions(None, ctx.start, ctx.end)
                 .filter(pl.col("suspend_type") == "S").select(["symbol", "dt"]).unique())
    trading = wanted.join(suspended, on=["symbol", "dt"], how="anti")
    missed = missing_auction_days(ctx.warehouse, trading)
    # 停牌候选也逐个核对过（结论是豁免），计入分母
    return ([{"dataset": "auction_ticks", "symbol": row["symbol"], "dt": row["dt"],
              "reason": "首板次日未到货"} for row in missed.iter_rows(named=True)],
            wanted.height)


def _arrival(ctx: CheckContext) -> CheckResult:
    daily = ctx.query.tushare_daily(None, ctx.start, ctx.end, columns=["symbol", "dt"])
    sessions = set(daily["dt"].unique().to_list()) if daily.height else set()
    missing: list[dict[str, object]] = []
    if sessions:
        for name, frame in (
            ("daily_flags", ctx.query.daily_flags(ctx.start, ctx.end)),
            ("market_regime", ctx.query.market_regime(ctx.start, ctx.end)),
        ):
            have = set(frame["dt"].unique().to_list()) if frame.height else set()
            missing.extend({"dataset": name, "dt": day, "reason": "未到货"}
                           for day in sorted(sessions - have))
        if ctx.query.instruments().height == 0:
            missing.append({"dataset": "instruments", "dt": ctx.end, "reason": "空表"})
    recon_parts = sorted((ctx.warehouse.root / "live_recon").glob("year=*/part.parquet"))
    recon = pl.concat([pl.read_parquet(path) for path in recon_parts]) if recon_parts else pl.DataFrame()
    notes: list[str] = []
    if recon.height:
        if "mode" not in recon.columns:          # 2026-10-06 之前落盘的行没有这一列
            recon = recon.with_columns(pl.lit(None, dtype=pl.Utf8).alias("mode"))
        failed = recon.filter(pl.col("dt").is_between(ctx.start, ctx.end) & ~pl.col("is_consistent"))
        # 影子盘与账户对不上是正当的：维护者自己要转账、买国债逆回购，而影子核心
        # 一张单都发不出去，所以 `runtime._assert_reconciled` 在影子模式下只告警。
        # 这里必须跟着分级，否则一条正当的漂移会每天伪装成阻断违规，把真正的
        # 持仓不一致盖掉（2026-09-28 起生产实际发生，#134）。
        # 未知模式一律按实盘处理——保守，而不是替它假设成影子。
        shadow = failed.filter(pl.col("mode") == "shadow").height
        undetermined = failed.filter(pl.col("mode").is_null()).height
        blocking = failed.filter(pl.col("mode").is_not_null() & (pl.col("mode") != "shadow"))
        missing.extend({"dataset": "live_recon", "dt": row["dt"], "reason": row["layer"]}
                       for row in blocking.iter_rows(named=True))
        if shadow:
            notes.append(f"影子盘漂移 {shadow} 条不计入违规")
        if undetermined:
            notes.append(f"模式未记录 {undetermined} 条，判不了")
    auction_missed, auction_checked = _auction_misses(ctx)
    missing.extend(auction_missed)
    checked = (len(sessions) * 2 + (1 if sessions else 0) + auction_checked
               + (recon.height if recon.height else 0))
    detail = f"到货或采集失败 {len(missing)} 项"
    if notes:
        detail = f"{detail}（{'；'.join(notes)}）"
    return CheckResult(len(missing), checked, detail,
                       pl.DataFrame(missing) if missing else None,
                       failed_metrics=_blame(missing))


# 违规里的 dataset -> 该由哪个指标担责。没列出的（daily_flags、market_regime）
# 不对应本规则声明的任何指标，归到"说不清"，那时三个指标一起判失败才是诚实的。
_BLAME = {
    "live_recon": "live_recon.is_consistent",
    "instruments": "instruments.symbol",
    "auction_ticks": "auction_ticks.dt",
}


def _blame(missing: list[dict[str, object]]) -> tuple[str, ...] | None:
    """点名真正出问题的指标。

    一条规则管好几张表时，不点名就会连坐：2026-09-30 维护者截图显示
    auction_ticks.dt 与 instruments.symbol 都标着「失败」，而 10,382 条违规
    其实全部来自 live_recon——页面上看就像三个数据源同时出事。
    """
    if not missing:
        return None
    datasets = {str(item.get("dataset", "")) for item in missing}
    if not datasets <= set(_BLAME):
        return None          # 出现没法归因的数据集，保守地全判
    return tuple(sorted({_BLAME[name] for name in datasets}))


# D-18：原先还登记了 ingest_log / name_snapshots / suspend_daily / minute_bars，
# 实现却从未检查（ingest_log 自换源后已不再写入）。这些到货检查改由服务管理的
# 每日服务质检承担（scripts/service_checks.py），这里只保留真正检查的项。
register(Check("B4.arrival", Category.COMPLETENESS, Scope.INCREMENTAL,
               ("live_recon.is_consistent", "instruments.symbol", "auction_ticks.dt"), (),
               "事实表到货后派生消费者也必须到货，实盘对账不得不一致", _arrival))


def _zero_limit(ctx: CheckContext) -> CheckResult:
    flags = ctx.query.daily_flags(ctx.start, ctx.end)
    listed = ctx.query.limit_list(None, ctx.start, ctx.end)
    if not flags.height:
        return CheckResult(0, 0, "区间内无交易日数据")
    sessions = set(flags["dt"].unique().to_list())
    now = _now()
    # limit_list_d 实测到 19:00 才发布。当日 SLA 前缺席不是异常；若本次
    # 区间只有今天，明确写 N/A，不能提前制造 D-12 告警。
    if now.time() < _LIMIT_LIST_SLA:
        sessions.discard(now.date())
        if not sessions:
            return CheckResult(
                0, 0,
                f"当日 limit_list_d 尚未到 {_LIMIT_LIST_SLA:%H:%M} 发布 SLA",
                applicable=False,
            )
    have = set(listed.filter(pl.col("limit_type") == "U")["dt"].unique().to_list())
    empty = sorted(sessions - have)
    return CheckResult(len(empty), len(sessions), f"{len(empty)} 个交易日零涨停：{empty[:10]}",
                       pl.DataFrame({"dt": empty}) if empty else None)


register(Check("B4.zero_limit_up", Category.COMPLETENESS, Scope.INCREMENTAL,
               ("limit_list_daily.dt", "limit_list_daily.limit_type"), ("D-12",),
               "全市场零涨停通常意味着接口尚未发布", _zero_limit))


def _span(ctx: CheckContext) -> CheckResult:
    daily = ctx.query.tushare_daily(None, ctx.start, ctx.end, columns=["symbol", "dt"])
    if not daily.height:
        return CheckResult(0, 0, "区间内无日线，无法验证跨度")
    dates = sorted(daily["dt"].unique().to_list())
    # 请求边界可能落在周末或长假；超过十个自然日才算真正没覆盖到边界。
    violations = int((dates[0] - ctx.start).days > 10) + int((ctx.end - dates[-1]).days > 10)
    return CheckResult(violations, len(dates), f"实际跨度 {dates[0]} 至 {dates[-1]}；请求 {ctx.start} 至 {ctx.end}")


register(Check("B5.span", Category.COMPLETENESS, Scope.FULL,
               ("tushare_daily.dt", "bars_daily.dt", "paid_daily.dt",
                "index_bars.dt", "corporate_actions.ex_date", "share_capital.dt"), (),
               "历史数据首末日期覆盖声明区间", _span))
