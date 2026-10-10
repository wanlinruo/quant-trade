"""每日增量同步：一天的全部 Tushare 数据集。

**日线为空必须显式失败。** 2026-09-10/11 的归档在 7200 秒超时被杀，根因是
mootdx 上游静默返回空表而系统当成正常，连采了两小时空数据都没报错。
停牌、涨跌停列表为空则是常态，不视为失败——这两类的判据在各自的
ingest_* 函数里，不在这里。

`_REFERENCE_STEPS` 已接入涨跌停价、停牌、指数、标的、名称区间和涨停列表。
tests/data/pipeline/test_tushare_sync.py 有守卫测试盯着这件事——新增采集函数却
忘了接线，是「采集不到却不报错」的经典形态。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta

import structlog

from qsys.data.pipeline.index_bars import CSI300
from qsys.data.pipeline.tushare_daily import compose_daily, write_year
from qsys.data.pipeline import tushare_reference as reference
from qsys.data.pipeline.adjust import build_adj_factors
from qsys.data.pipeline.delisting import build_delisting_pool
from qsys.data.pipeline.flags import build_daily_flags
from qsys.data.pipeline.regime import build_market_regime
from qsys.data.pipeline.valuation import build_valuation
from qsys.data.pipeline.first_board_inputs import ingest_moneyflow
from qsys.data.storage import Warehouse
from qsys.ops.services import ServiceRun, record_step

log = structlog.get_logger(__name__)

def _sync_index(provider, warehouse: Warehouse, days: list[date]) -> int:
    return reference.ingest_index_daily(provider, warehouse, [CSI300], days[0], days[-1])


def _sync_instruments(provider, warehouse: Warehouse, _days: list[date]) -> int:
    """as_of 取**墙上时钟的今天**，不是正在同步的交易日。

    `stock_basic` 返回的是当前名称，只对取数当天成立。补跑旧交易日时
    as_of 仍是今天，快照与那个交易日对不上、自然不生效——这正是想要的，
    把它错记成目标交易日就等于拿今天的名字填过去。
    """
    return reference.ingest_instruments(provider, warehouse, date.today())


def _sync_name_history(provider, warehouse: Warehouse, _days: list[date]) -> int:
    return reference.ingest_name_history(provider, warehouse)


def _sync_limit_list(provider, warehouse: Warehouse, days: list[date]) -> int:
    """当日名单 + 最近几个交易日里仍为空的名单（D-12）。

    `limit_list_d` 实测晚于 17:00 发布，19:00 那次若也没取到，下一次同步
    只看新交易日，前一天就永远空着。这里重新直取，不做任何推断；仍取不到
    就继续空着，由 B4.zero_limit_up 报告。
    """
    from qsys.data.query import WarehouseQuery

    query = WarehouseQuery(warehouse)
    end = max(days)
    sessions = query.tushare_daily(
        None, end - _LIMIT_LIST_CALENDAR_SPAN, end, columns=["dt"]
    )["dt"].unique().sort().to_list()
    recent = [day for day in sessions if day < min(days)][-_LIMIT_LIST_LOOKBACK:]
    listed = (set(query.limit_list(None, recent[0], recent[-1])["dt"].to_list())
              if recent else set())
    retry = [day for day in recent if day not in listed]
    if retry:
        log.warning("limit_list.refetch_empty_sessions",
                    days=[str(day) for day in retry])
    return reference.ingest_limit_list(provider, warehouse, sorted({*retry, *days}))


# 每日任务的统一签名是 fn(provider, warehouse, [day])。指数与无分区主表的
# 原始 ingest 签名不同，故用三个显式适配器；不要用 partial 隐藏参数语义。
_REFERENCE_STEPS: list[tuple[str, Callable[..., int]]] = [
    ("price_limits_daily", reference.ingest_price_limits),
    ("suspend_daily", reference.ingest_suspensions),
    ("stock_st_daily", reference.ingest_stock_st),
    ("index_bars", _sync_index),
    ("instruments", _sync_instruments),
    ("name_history", _sync_name_history),
    ("limit_list_daily", _sync_limit_list),
    ("moneyflow_daily", ingest_moneyflow),
]

# 服务管理页显示的「接口 → 表」。键与 _REFERENCE_STEPS 的名字一一对应（守卫测试）。
STEP_SOURCES: dict[str, tuple[str, str, str]] = {  # 名字 → (步骤, 接口, 写入表)
    "tushare_daily": ("日线（含股本估值、复权因子）", "Tushare daily / daily_basic / adj_factor",
                      "tushare_daily"),
    "price_limits_daily": ("涨跌停价", "Tushare stk_limit", "price_limits_daily"),
    "suspend_daily": ("停牌", "Tushare suspend_d", "suspend_daily"),
    "stock_st_daily": ("ST 名单", "Tushare stock_st", "stock_st_daily"),
    "index_bars": ("沪深300 指数", "Tushare index_daily", "index_bars"),
    "instruments": ("标的主表", "Tushare stock_basic", "instruments"),
    "name_history": ("改名与退市整理期记录", "Tushare namechange", "name_history"),
    "limit_list_daily": ("涨停列表", "Tushare limit_list_d", "limit_list_daily"),
    "moneyflow_daily": ("个股资金流", "Tushare moneyflow", "moneyflow_daily"),
}

# 接线守卫检查的是实际公开 ingest，而不是适配器函数名。
_REFERENCE_INGESTS = {
    reference.ingest_price_limits,
    reference.ingest_suspensions,
    reference.ingest_stock_st,
    reference.ingest_index_daily,
    reference.ingest_instruments,
    reference.ingest_name_history,
    reference.ingest_limit_list,
    ingest_moneyflow,
}

# D-12 回补：只回看最近 4 个交易日（加上当日共 5 天）。更早的缺口交给告警
_LIMIT_LIST_LOOKBACK = 4
_LIMIT_LIST_CALENDAR_SPAN = timedelta(days=30)

_DERIVED_LOOKBACK_DAYS = 150


def last_open_day(client, today: date) -> date | None:
    """最近一个已收盘的交易日。交易日历取自 trade_cal，不自行推算。

    是「同步哪一天」的判断逻辑，不是脚本的参数解析，所以放在这个模块里
    （而不是 scripts/tushare_daily_sync.py）——scripts/ 不是包，测试没法
    `from scripts.x import`，这条规矩本仓库已经踩过一次坑（见 brief）。
    """
    rows = client.call("trade_cal", {
        "exchange": "SSE",
        "start_date": (today - timedelta(days=30)).strftime("%Y%m%d"),
        "end_date": today.strftime("%Y%m%d"), "is_open": "1",
    })
    days = sorted(r["cal_date"] for r in rows)
    if not days:
        return None
    stamp = days[-1]
    return date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))


def next_open_day(client, day: date) -> date | None:
    """`day` 之后的第一个交易日（trade_cal）。退市整理期池要提前建好 T+1 的那天。"""
    rows = client.call("trade_cal", {
        "exchange": "SSE",
        "start_date": (day + timedelta(days=1)).strftime("%Y%m%d"),
        "end_date": (day + timedelta(days=30)).strftime("%Y%m%d"), "is_open": "1",
    })
    days = sorted(r["cal_date"] for r in rows)
    if not days:
        return None
    stamp = days[0]
    return date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))


def completed_calendar_end(now: datetime) -> date:
    """自动同步允许查询到的最后自然日；16:00 前不选择当日。"""
    return now.date() if now.time() >= time(16, 0) else now.date() - timedelta(days=1)


def sync(provider, warehouse: Warehouse, day: date, *,
         run: ServiceRun | None = None) -> dict[str, int]:
    """同步一个交易日。返回 {数据集: 写入行数}。

    日线为空直接抛错，**且在抛错之前不写任何表**——半张表比没有表更难查。
    """
    label, interface, table = STEP_SOURCES["tushare_daily"]
    with record_step(run, label, interface=interface, table=table) as step:
        daily = provider.daily(day)
        if daily.height == 0:
            # 非交易日由调用方用 trade_cal 提前排除；走到这里就是该有数据却没有。
            raise RuntimeError(
                f"Tushare daily 在 {day:%Y%m%d} 返回 0 行——该交易日应有数据。"
                f"不写入任何表，退出。"
            )

        frame = compose_daily(daily, provider.daily_basic(day), provider.adj_factor(day))
        counts = {"tushare_daily": write_year(warehouse, frame, day.year)}
        step.rows = counts["tushare_daily"]
    for name, ingest in _REFERENCE_STEPS:
        label, interface, table = STEP_SOURCES.get(name, (name, None, name))
        with record_step(run, label, interface=interface, table=table) as step:
            counts[name] = step.rows = ingest(provider, warehouse, [day])

    log.info("tushare_sync.done", day=str(day), **counts)
    return counts


def refresh_derived(warehouse: Warehouse, day: date, *,
                    run: ServiceRun | None = None,
                    next_session: date | None = None) -> dict[str, int]:
    """事实表成功后刷新所有生产消费者，避免价格与因子/flags 跨日断层。"""
    start = day - timedelta(days=_DERIVED_LOOKBACK_DAYS)
    builders: list[tuple[str, str, Callable[[], int]]] = [
        ("adj_factors", "复权因子", lambda: build_adj_factors(warehouse)),
        ("daily_flags", "首板与 ST 等标记", lambda: build_daily_flags(
            warehouse, start, day, boards=None)),
        ("market_regime", "市场广度", lambda: build_market_regime(
            warehouse, start, day, boards=None)),
        ("valuation_snapshot", "估值快照", lambda: build_valuation(warehouse, day, day)),
        ("delisting_pool", "退市整理期池", lambda: build_delisting_pool(
            warehouse, start, day, extra_sessions=[next_session] if next_session else [])),
    ]
    counts: dict[str, int] = {}
    for name, label, build in builders:
        with record_step(run, f"派生：{label}", interface="仓库内计算", table=name) as step:
            counts[name] = step.rows = build()
    log.info("tushare_sync.derived_refreshed", day=str(day), **counts)
    return counts
