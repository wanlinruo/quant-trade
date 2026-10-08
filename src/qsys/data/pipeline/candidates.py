"""候选股-日选择器：决定要下载哪些股在哪些日的分钟线。

这是唯一决定分钟线数据量的地方。实测（894 个交易日，2023-01 ~ 2026-09）：
  window=1   41,592 组 ≈ 1000 万根
  window=3  122,936 组 ≈ 2950 万根
  window=6  232,162 组 ≈ 5570 万根
对照：全市场全历史约 10.2 亿根。

**交易日推进，不是日历日推进**：首板在周五，候选日是下周一。
交易日序列直接取自 daily_flags 里实际出现过的日期——那本就是从真实日线
派生的，比另配一份日历更不容易走样。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

import polars as pl
import structlog

from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.data.suspensions import full_day_suspensions

log = structlog.get_logger(__name__)

MANIFEST_COLUMNS = ["symbol", "dt"]

_SCHEMA = {"symbol": pl.Utf8, "dt": pl.Date}


def candidate_stock_days(
    warehouse: Warehouse,
    start: date,
    end: date,
    *,
    window: int = 1,
    sessions: Sequence[date] | None = None,
) -> pl.DataFrame:
    """首板之后 `window` 个交易日的（标的, 日期）组合。

    `sessions` 默认取自 `daily_flags` 里实际出现过的日期——那本就是从真实
    日线派生的，比另配一份日历更不容易走样。

    但**竞价 tick 要在当日日线入库之前就采**（tick 只有 20 个交易日的窗口，
    而全市场日线增量要跑 5 个多小时，压在后面等于拿不可恢复的数据赌可恢复
    的数据）。那时 T 日还不在 `daily_flags` 里，T-1 的首板就找不到下一个
    交易日。此时显式传入交易日历即可。
    """
    if window < 1:
        raise ValueError(f"window must be >= 1: {window}")

    flags = WarehouseQuery(warehouse).daily_flags(start, end)
    empty = pl.DataFrame(schema=_SCHEMA)
    if flags.height == 0:
        return empty

    if sessions is None:
        sessions = sorted(flags["dt"].unique().to_list())
    else:
        sessions = sorted(sessions)
    position = {d: i for i, d in enumerate(sessions)}

    boards = flags.filter(pl.col("is_first_board")).select(["symbol", "dt"])
    if boards.height == 0:
        return empty

    pairs: set[tuple[str, date]] = set()
    for symbol, day in zip(boards["symbol"].to_list(), boards["dt"].to_list()):
        origin = position.get(day)
        if origin is None:  # 外部日历里没有这天，跳过而不是崩
            continue
        for step in range(1, window + 1):
            following = origin + step
            if following < len(sessions):
                pairs.add((symbol, sessions[following]))

    if not pairs:
        return empty

    ordered = sorted(pairs, key=lambda p: (p[1], p[0]))
    return pl.DataFrame(
        {"symbol": [p[0] for p in ordered], "dt": [p[1] for p in ordered]},
        schema=_SCHEMA,
    )


def write_manifest(frame: pl.DataFrame, path: Path) -> int:
    """写出 Windows 下载脚本的输入清单。返回行数。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.select(MANIFEST_COLUMNS).write_csv(path)
    log.info("manifest.written", path=str(path), rows=frame.height)
    return frame.height


def holding_stock_days(
    fills: pl.DataFrame,
    sessions: Sequence[date],
    *,
    window: int = 30,
) -> pl.DataFrame:
    """每笔买入之后 `window` 个交易日的（标的, 日期）组合。

    候选清单（`candidate_stock_days`）只覆盖**买入决策**要的那几天。退出
    要的是**持仓期间**每一天的日内报价——两者不是一回事。

    实测教训：分钟线缺哪天，`on_checkpoint` 就在哪天拿不到报价而静默跳过，
    持仓于是**永久卡死**。回测里 603809.SH 从 2025-12-26 一直挂到区间末尾，
    300821.SZ 挂了 5 个月。满仓建仓的策略被一只卡死的持仓抽干现金，
    后面 16 个该买的买不进去——包括聚宽当天买了 6 只的 2026-07-31。

    T+1：买入当日不可卖，故从 D+1 开始。
    """
    if window < 1:
        raise ValueError(f"window must be >= 1: {window}")

    empty = pl.DataFrame(schema=_SCHEMA)
    buys = fills.filter(pl.col("side") == "buy")
    if buys.height == 0:
        return empty

    ordered_sessions = sorted(sessions)
    position = {d: i for i, d in enumerate(ordered_sessions)}

    pairs: set[tuple[str, date]] = set()
    for symbol, day in zip(buys["symbol"].to_list(), buys["dt"].to_list()):
        origin = position.get(day)
        if origin is None:
            continue
        for step in range(1, window + 1):
            following = origin + step
            if following < len(ordered_sessions):
                pairs.add((symbol, ordered_sessions[following]))

    if not pairs:
        return empty

    ordered = sorted(pairs, key=lambda p: (p[1], p[0]))
    return pl.DataFrame(
        {"symbol": [p[0] for p in ordered], "dt": [p[1] for p in ordered]},
        schema=_SCHEMA,
    )


def held_stock_days(
    fills: pl.DataFrame,
    start: date,
    end: date,
    sessions: Sequence[date],
) -> pl.DataFrame:
    """模拟盘真实持有过的（标的, 日期）组合，限定在 [start, end] 内。

    `fills` 需含 account_id / symbol / side / qty / filled_at。按账户逐笔累计
    持仓：从买入当日到清仓当日（两头都算，买入日要看竞价、清仓日要看卖出时点），
    至今未清仓的算到 `end`。

    候选清单只覆盖买入决策；持仓期间的检查点价格同样要归档——2026-09-22 查
    模拟盘为何没卖时，两只持仓当天的分钟线不在仓库里，只能去 QMT 现取。
    """
    empty = pl.DataFrame(schema=_SCHEMA)
    if fills.height == 0:
        return empty
    ordered = sorted(d for d in sessions if start <= d <= end)
    if not ordered:
        return empty
    frame = fills.with_columns(
        pl.col("filled_at").cast(pl.Utf8).str.slice(0, 10).str.to_date().alias("day")
    ).sort("filled_at")
    pairs: set[tuple[str, date]] = set()
    for (account, symbol), rows in frame.group_by(["account_id", "symbol"],
                                                  maintain_order=True):
        qty = 0
        opened: date | None = None
        for side, amount, day in rows.select("side", "qty", "day").iter_rows():
            if side == "buy":
                if qty <= 0:
                    opened = day
                qty += int(amount)
            else:
                qty -= int(amount)
                if qty <= 0 and opened is not None:
                    pairs.update((symbol, d) for d in ordered if opened <= d <= day)
                    opened = None
        if qty > 0 and opened is not None:
            pairs.update((symbol, d) for d in ordered if d >= opened)
    if not pairs:
        return empty
    return pl.DataFrame(sorted(pairs), schema=_SCHEMA, orient="row")


def drop_suspended(query: WarehouseQuery, days: pl.DataFrame) -> pl.DataFrame:
    """扣掉当日全天停牌的（标的, 日期）。

    停牌真值只认 `suspend_daily`（Tushare ``suspend_d``），`suspend_type='S'` 为停牌、
    `'R'` 为复牌。

    **竞价下载清单与竞价质检必须都走这一个函数。** 分钟下载也在这里先剔除已知停牌，
    但分钟到货质检刻意不复用清单，而以 Tushare 当日实际有成交股票为独立期望。

    2025 年下载没扣停牌，QMT 返回 241 根前收盘价、成交量全 0，42 个整日虚构的标的日
    因此进了仓库；质检却已扣掉它们，所以从不报警。见
    docs/reference/qmt-range-query-padding.md。
    """
    if days.height == 0:
        return days
    receipts = query.suspensions(None, days["dt"].min(), days["dt"].max())
    # suspend_timing=null 的 S 才是全天停牌；有具体时段的是盘中临停。
    # 同日 R 表示已经复牌，不能再把整日删除。
    suspended = full_day_suspensions(receipts)
    return days.join(suspended, on=["symbol", "dt"], how="anti")
