"""每日增量同步：一天的全部 Tushare 数据集。

**日线为空必须显式失败。** 2026-09-10/11 的归档在 7200 秒超时被杀，根因是
mootdx 上游静默返回空表而系统当成正常，连采了两小时空数据都没报错。
停牌、涨跌停列表为空则是常态，不视为失败——这两类的判据在各自的
ingest_* 函数里，不在这里。

`_REFERENCE_STEPS` 当前为空：涨跌停价、停牌、涨跌停列表、指数日线四张表
由计划 C 的 Task 4-8 建，各自把自己的 ingest_* 追加进来。
tests/data/pipeline/test_tushare_sync.py 有一条守卫测试盯着这件事——
新增采集函数却忘了接线，是「采集不到却不报错」的经典形态。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import structlog

from qsys.data.pipeline.tushare_daily import compose_daily, write_year
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

# (数据集名, 采集函数)。函数签名统一为 fn(provider, warehouse, [day]) -> int。
# Task 4: ingest_price_limits / Task 5: ingest_suspensions
# Task 6 落地指数日线时从 qsys.data.pipeline.index_bars import CSI300，
# 不在这里重复定义常量。       / Task 8: ingest_limit_list
_REFERENCE_STEPS: list[tuple[str, Callable[..., int]]] = []


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


def sync(provider, warehouse: Warehouse, day: date) -> dict[str, int]:
    """同步一个交易日。返回 {数据集: 写入行数}。

    日线为空直接抛错，**且在抛错之前不写任何表**——半张表比没有表更难查。
    """
    daily = provider.daily(day)
    if daily.height == 0:
        # 非交易日由调用方用 trade_cal 提前排除；走到这里就是该有数据却没有。
        raise RuntimeError(
            f"Tushare daily 在 {day:%Y%m%d} 返回 0 行——该交易日应有数据。"
            f"不写入任何表，退出。"
        )

    frame = compose_daily(daily, provider.daily_basic(day), provider.adj_factor(day))
    counts = {"tushare_daily": write_year(warehouse, frame, day.year)}
    for name, ingest in _REFERENCE_STEPS:
        counts[name] = ingest(provider, warehouse, [day])

    log.info("tushare_sync.done", day=str(day), **counts)
    return counts
