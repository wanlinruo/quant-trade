"""每个定时任务的质检：它该写的表当天到齐没有、数量对不对。

由 `scripts/service_checks.py`（`qsys-service-checks.timer`，交易日 20:00）执行，
结果作为服务 `service-checks` 的运行记录落盘，服务管理页把每条检查挂回所属任务。

期望值只取直取源或运行记录，不做推断：
- 日线标的集合来自 Tushare daily；涨跌停价、ST、估值、flags 都应覆盖它；
- 停牌表当天可以真的为空，所以只认 Tushare 同步运行记录里「停牌」这一步成功；
- 分钟线与竞价的应到集合与每日归档的下载清单同源（首板次日候选），扣掉直取停牌。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time

import polars as pl

from qsys.data.pipeline.auction import missing_auction_days
from qsys.data.pipeline.candidates import candidate_stock_days
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.markets.ashare import board_of
from qsys.ops.services import FAILED, SKIPPED, SUCCEEDED, WARNING

OK, BAD, WARN, SKIP = SUCCEEDED, FAILED, WARNING, SKIPPED

# 日线行数相对上一交易日跌幅超过 2% 视为没到齐（实测 9 月每日波动在 ±5 只以内）
DAILY_MIN_RATIO = 0.98
MINUTE_BARS_PER_DAY = 241
VOLUME_TOLERANCE_LOTS = 1.0
PRODUCTION_BOARDS = {"main", "gem", "star"}
SAMPLE_LIMIT = 20


@dataclass(frozen=True, slots=True)
class CheckContext:
    warehouse: Warehouse
    query: WarehouseQuery
    day: date
    previous: date | None
    sessions: list[date]
    runs: list[dict]  # 当天所有服务的运行记录

    def daily(self, day: date | None = None) -> pl.DataFrame:
        target = day or self.day
        return self.query.tushare_daily(None, target, target, columns=["symbol", "dt", "volume"])

    def last_run(self, service: str) -> dict | None:
        mine = [r for r in self.runs
                if r.get("service") == service and r.get("session") == self.day.isoformat()]
        return mine[-1] if mine else None


@dataclass(frozen=True, slots=True)
class CheckResult:
    status: str
    expected: str
    actual: str
    detail: str = ""
    rows: int | None = None


@dataclass(frozen=True, slots=True)
class ServiceCheck:
    service: str
    name: str
    table: str
    fn: Callable[[CheckContext], CheckResult]


def _samples(items) -> str:
    items = sorted(items)
    head = "、".join(str(i) for i in items[:SAMPLE_LIMIT])
    return head + (f" 等 {len(items)} 个" if len(items) > SAMPLE_LIMIT else "")


def _covers(ctx: CheckContext, frame: pl.DataFrame, what: str, *,
            boards: set[str] | None = None) -> CheckResult:
    daily = set(ctx.daily()["symbol"].to_list())
    if boards is not None:
        daily = {s for s in daily if board_of(s.split(".")[0]) in boards}
    if not daily:
        return CheckResult(BAD, "当日有日线", "日线为空，无法核对", rows=0)
    have = set(frame.filter(pl.col("dt") == ctx.day)["symbol"].to_list())
    missing = daily - have
    if missing:
        return CheckResult(BAD, f"覆盖日线 {len(daily)} 只", f"缺 {len(missing)} 只",
                           f"缺{what}：{_samples(missing)}", rows=len(have))
    return CheckResult(OK, f"覆盖日线 {len(daily)} 只", f"{len(have)} 只，全覆盖", rows=len(have))


# ---- Tushare 收盘同步 ----

def _daily_arrived(ctx: CheckContext) -> CheckResult:
    today = ctx.daily().height
    if today == 0:
        return CheckResult(BAD, "当日有日线", "0 行", rows=0)
    if ctx.previous is None:
        return CheckResult(OK, "当日有日线", f"{today} 行", rows=today)
    before = ctx.daily(ctx.previous).height
    floor = int(before * DAILY_MIN_RATIO)
    status = OK if today >= floor else BAD
    return CheckResult(status, f"≥ 上一交易日 {before} 行的 {DAILY_MIN_RATIO:.0%}（{floor}）",
                       f"{today} 行", rows=today)


def _limits_cover_daily(ctx: CheckContext) -> CheckResult:
    return _covers(ctx, ctx.query.price_limits(None, ctx.day, ctx.day), "涨跌停价")


def _st_covers_daily(ctx: CheckContext) -> CheckResult:
    return _covers(ctx, ctx.query.stock_st(None, ctx.day, ctx.day), "ST 结论")


def _suspend_receipt(ctx: CheckContext) -> CheckResult:
    run = ctx.last_run("tushare-sync")
    if run is None:
        return CheckResult(BAD, "当日同步记录里「停牌」成功", "当日没有 Tushare 同步运行记录")
    step = next((s for s in run.get("steps", []) if s.get("table") == "suspend_daily"), None)
    if step is None:
        return CheckResult(BAD, "当日同步记录里「停牌」成功", "同步未执行到停牌这一步",
                           run.get("error", ""))
    if step.get("status") != SUCCEEDED:
        return CheckResult(BAD, "当日同步记录里「停牌」成功", f"停牌步骤 {step.get('status')}",
                           step.get("error") or step.get("detail", ""))
    rows = ctx.query.suspensions(None, ctx.day, ctx.day).height
    return CheckResult(OK, "当日同步记录里「停牌」成功", f"成功，当日停牌 {rows} 只",
                       "停牌可以真的为 0 只，所以以采集成功为准", rows=rows)


def _limit_list_arrived(ctx: CheckContext) -> CheckResult:
    rows = ctx.query.limit_list(None, ctx.day, ctx.day).height
    return CheckResult(OK if rows else BAD, "当日涨停/炸板列表非空", f"{rows} 行",
                       "" if rows else "Tushare limit_list_d 发布较晚（D-12），19:00 补跑后仍为空",
                       rows=rows)


def _index_arrived(ctx: CheckContext) -> CheckResult:
    rows = ctx.query.index_bars(None, ctx.day, ctx.day).filter(pl.col("symbol") == "000300.SH").height
    return CheckResult(OK if rows else BAD, "沪深300 当日 1 行", f"{rows} 行", rows=rows)


def _instruments_fresh(ctx: CheckContext) -> CheckResult:
    frame = ctx.query.instruments()
    as_of = frame["as_of"].max() if frame.height else None
    ok = as_of is not None and as_of >= ctx.day
    return CheckResult(OK if ok else BAD, f"主表更新日 ≥ {ctx.day}", str(as_of or "空表"),
                       rows=frame.height)


def _flags_cover_daily(ctx: CheckContext) -> CheckResult:
    return _covers(ctx, ctx.query.daily_flags(ctx.day, ctx.day), "首板等标记",
                   boards=PRODUCTION_BOARDS)


def _regime_arrived(ctx: CheckContext) -> CheckResult:
    rows = ctx.query.market_regime(ctx.day, ctx.day).height
    return CheckResult(OK if rows == 1 else BAD, "当日 1 行", f"{rows} 行", rows=rows)


def _valuation_covers_daily(ctx: CheckContext) -> CheckResult:
    return _covers(ctx, ctx.query.valuation(ctx.day, ctx.day), "估值")


def _metric_checks(ctx: CheckContext) -> CheckResult:
    frame = ctx.query.metric_checks()
    if frame.height == 0:
        return CheckResult(BAD, "当日跑过增量质检", "没有质检结果")
    today = frame.filter(pl.col("scope_end") == ctx.day)
    if today.height == 0:
        return CheckResult(BAD, "当日跑过增量质检", "当日没有质检结果")
    latest = today.filter(pl.col("run_id") == today["run_id"].max())
    blocking = latest.filter(~pl.col("passed") & (pl.col("severity") == "blocking"))
    if blocking.height:
        return CheckResult(WARN, "阻断级规则全部通过", f"{blocking.height} 项未通过",
                           _samples(set(blocking["check_id"].to_list())), rows=latest.height)
    return CheckResult(OK, "阻断级规则全部通过", f"{latest.height} 项全部通过", rows=latest.height)


# ---- 盘前涨跌停价 ----

def _premarket_rows(ctx: CheckContext) -> CheckResult:
    run = ctx.last_run("premarket-limits")
    if run is None:
        return CheckResult(BAD, "开盘前取到当日涨跌停价", "当日没有盘前运行记录")
    step = next((s for s in run.get("steps", []) if s.get("table") == "price_limits_daily"), None)
    if step is None or step.get("status") != SUCCEEDED:
        return CheckResult(BAD, "开盘前取到当日涨跌停价",
                           "未执行" if step is None else f"步骤 {step.get('status')}",
                           (step or {}).get("detail", "") or run.get("error", ""))
    rows = step.get("rows") or 0
    return CheckResult(OK, "开盘前取到当日涨跌停价",
                       f"{step.get('finished_at', '')[11:]} 完成，{rows} 只", rows=rows)


# ---- 每日归档（QMT） ----

def _archive_wanted(ctx: CheckContext) -> pl.DataFrame:
    """与每日归档的下载清单同源：首板次日候选，扣掉当日直取停牌。"""
    lookback = ctx.sessions[max(0, ctx.sessions.index(ctx.day) - 10)]
    wanted = candidate_stock_days(ctx.warehouse, lookback, ctx.day, sessions=ctx.sessions)
    wanted = wanted.filter(pl.col("dt") == ctx.day)
    suspended = (ctx.query.suspensions(None, ctx.day, ctx.day)
                 .filter(pl.col("suspend_type") == "S").select(["symbol", "dt"]))
    return wanted.join(suspended, on=["symbol", "dt"], how="anti")


def _auction_arrived(ctx: CheckContext) -> CheckResult:
    wanted = _archive_wanted(ctx)
    if wanted.height == 0:
        return CheckResult(OK, "首板次日候选都有竞价", "当日没有候选", rows=0)
    missing = missing_auction_days(ctx.warehouse, wanted)
    status = BAD if missing.height else OK
    return CheckResult(status, f"候选 {wanted.height} 只", f"缺 {missing.height} 只",
                       _samples(set(missing["symbol"].to_list())) if missing.height else "",
                       rows=wanted.height - missing.height)


def _minute_bars(ctx: CheckContext) -> tuple[pl.DataFrame, pl.DataFrame]:
    wanted = _archive_wanted(ctx)
    if wanted.height == 0:
        return wanted, pl.DataFrame(schema={"symbol": pl.Utf8, "bars": pl.UInt32,
                                            "volume": pl.Int64})
    bars = ctx.query.minute_bars(
        wanted["symbol"].to_list(),
        datetime.combine(ctx.day, time(0, 0)), datetime.combine(ctx.day, time(23, 59, 59)),
    )
    per = bars.group_by("symbol").agg(pl.len().alias("bars"), pl.col("volume").sum())
    return wanted, per


def _minutes_arrived(ctx: CheckContext) -> CheckResult:
    wanted, per = _minute_bars(ctx)
    if wanted.height == 0:
        return CheckResult(OK, "首板次日候选都有分钟线", "当日没有候选", rows=0)
    missing = set(wanted["symbol"].to_list()) - set(per["symbol"].to_list())
    return CheckResult(BAD if missing else OK, f"候选 {wanted.height} 只", f"缺 {len(missing)} 只",
                       _samples(missing) if missing else "", rows=per.height)


def _minutes_complete(ctx: CheckContext) -> CheckResult:
    wanted, per = _minute_bars(ctx)
    if per.height == 0:
        return CheckResult(SKIP, f"每只 {MINUTE_BARS_PER_DAY} 根", "没有分钟线可核对")
    bad = per.filter(pl.col("bars") != MINUTE_BARS_PER_DAY)
    return CheckResult(BAD if bad.height else OK, f"每只 {MINUTE_BARS_PER_DAY} 根",
                       f"{bad.height} 只不是 {MINUTE_BARS_PER_DAY} 根",
                       _samples({f"{s}({n})" for s, n in bad.select("symbol", "bars").rows()}),
                       rows=per.height)


def _minutes_volume(ctx: CheckContext) -> CheckResult:
    wanted, per = _minute_bars(ctx)
    if per.height == 0:
        return CheckResult(SKIP, "全天成交量与日线一致", "没有分钟线可核对")
    daily = ctx.daily().select("symbol", pl.col("volume").alias("daily_volume"))
    joined = per.join(daily, on="symbol", how="left")
    no_daily = joined.filter(pl.col("daily_volume").is_null())
    bad = joined.filter((pl.col("volume") - pl.col("daily_volume")).abs() > VOLUME_TOLERANCE_LOTS)
    if no_daily.height:
        return CheckResult(BAD, "全天成交量与日线一致", f"{no_daily.height} 只没有日线可对",
                           _samples(set(no_daily["symbol"].to_list())), rows=per.height)
    return CheckResult(BAD if bad.height else OK, f"差额 ≤ {VOLUME_TOLERANCE_LOTS:g} 手",
                       f"{bad.height} 只不一致",
                       _samples({f"{s}(分钟 {v} / 日线 {d:g})"
                                 for s, v, d in bad.select("symbol", "volume", "daily_volume").rows()}),
                       rows=per.height)


CHECKS: tuple[ServiceCheck, ...] = (
    ServiceCheck("premarket-limits", "开盘前取到当日涨跌停价", "price_limits_daily", _premarket_rows),
    ServiceCheck("daily-archive", "竞价逐笔到货", "auction_ticks", _auction_arrived),
    ServiceCheck("daily-archive", "分钟线到货", "minute_bars", _minutes_arrived),
    ServiceCheck("daily-archive", "分钟线每只 241 根", "minute_bars", _minutes_complete),
    ServiceCheck("daily-archive", "分钟线成交量对日线", "minute_bars", _minutes_volume),
    ServiceCheck("tushare-sync", "日线到货", "tushare_daily", _daily_arrived),
    ServiceCheck("tushare-sync", "涨跌停价覆盖日线", "price_limits_daily", _limits_cover_daily),
    ServiceCheck("tushare-sync", "ST 结论覆盖日线", "stock_st_daily", _st_covers_daily),
    ServiceCheck("tushare-sync", "停牌采集成功", "suspend_daily", _suspend_receipt),
    ServiceCheck("tushare-sync", "涨停列表到货", "limit_list_daily", _limit_list_arrived),
    ServiceCheck("tushare-sync", "沪深300 到货", "index_bars", _index_arrived),
    ServiceCheck("tushare-sync", "标的主表已更新", "instruments", _instruments_fresh),
    ServiceCheck("tushare-sync", "派生标记覆盖日线", "daily_flags", _flags_cover_daily),
    ServiceCheck("tushare-sync", "市场广度到货", "market_regime", _regime_arrived),
    ServiceCheck("tushare-sync", "估值覆盖日线", "valuation_snapshot", _valuation_covers_daily),
    ServiceCheck("tushare-sync", "增量质检阻断项", "metric_checks", _metric_checks),
)


def step_name(check: ServiceCheck) -> str:
    """运行记录里的步骤名：`服务id｜检查名`，页面据此把结果挂回所属任务。"""
    return f"{check.service}｜{check.name}"


def parse_step_name(name: str) -> tuple[str, str] | None:
    service, sep, check = name.partition("｜")
    return (service, check) if sep else None
