"""分钟线质量校验。

分钟线来自 QMT、日线来自 mootdx，**两个独立数据源**。它们必须对得上：
  · 分钟成交量之和 ≈ 日线成交量
  · 分钟最高价之最大 == 日线最高价
  · 首根分钟线的 open == 日线 open（竞价成交价即开盘价，2-1a 已实测）

对不上说明至少一个源错了，或时间戳约定弄反了。这是本项目能拿到的最强的
数据交叉验证——两个独立源同时错得一模一样的概率很低。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

import polars as pl
import structlog

from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

# 成交量允许 1% 偏差：两源对停牌集合竞价、盘后固定价的归属口径可能不同
_VOLUME_TOLERANCE = 0.01
_PRICE_TOLERANCE = 0.005  # 半分

_MORNING = (time(9, 31), time(11, 30))
_AFTERNOON = (time(13, 1), time(15, 0))


@dataclass(frozen=True, slots=True)
class MinuteIssue:
    symbol: str
    dt: date
    rule: str
    detail: str


def _in_session(moment: time) -> bool:
    return (_MORNING[0] <= moment <= _MORNING[1]) or (
        _AFTERNOON[0] <= moment <= _AFTERNOON[1]
    )


def check_minute_bars(warehouse: Warehouse, day: date) -> list[MinuteIssue]:
    """校验某个交易日的分钟线。返回问题列表，空表示干净。"""
    query = WarehouseQuery(warehouse)
    minutes = query.minute_bars(
        None,
        datetime.combine(day, time(0, 0)),
        datetime.combine(day, time(23, 59, 59)),
    )
    if minutes.height == 0:
        return []

    daily = query.daily_bars(None, day, day)
    reference_of = (
        {row["symbol"]: row for row in daily.iter_rows(named=True)}
        if daily.height
        else {}
    )

    issues: list[MinuteIssue] = []

    for key, group in minutes.sort(["symbol", "dt"]).group_by(
        "symbol", maintain_order=True
    ):
        symbol = key[0] if isinstance(key, tuple) else key

        if group["dt"].n_unique() != group.height:
            issues.append(MinuteIssue(symbol, day, "duplicate_dt", "同一时刻多根 bar"))

        bad_ohlc = group.filter(
            (pl.col("high") < pl.col("low"))
            | (pl.col("high") < pl.col("open"))
            | (pl.col("high") < pl.col("close"))
            | (pl.col("low") > pl.col("open"))
            | (pl.col("low") > pl.col("close"))
        )
        if bad_ohlc.height:
            issues.append(
                MinuteIssue(symbol, day, "ohlc", f"{bad_ohlc.height} 根 OHLC 不自洽")
            )

        outside = [t for t in group["dt"].to_list() if not _in_session(t.time())]
        if outside:
            issues.append(
                MinuteIssue(
                    symbol, day, "outside_session", f"{len(outside)} 根在交易时段外"
                )
            )

        reference = reference_of.get(symbol)
        if reference is None:
            continue  # 没有日线可比不是分钟线的错

        minute_volume = int(group["volume"].sum())
        if reference["volume"] > 0:
            drift = abs(minute_volume - reference["volume"]) / reference["volume"]
            if drift > _VOLUME_TOLERANCE:
                issues.append(
                    MinuteIssue(
                        symbol,
                        day,
                        "volume_vs_daily",
                        f"分钟合计 {minute_volume} vs 日线 {reference['volume']}",
                    )
                )

        minute_high = float(group["high"].max())
        if abs(minute_high - reference["high"]) > _PRICE_TOLERANCE:
            issues.append(
                MinuteIssue(
                    symbol,
                    day,
                    "high_vs_daily",
                    f"分钟最高 {minute_high} vs 日线 {reference['high']}",
                )
            )

        first_open = float(group["open"].to_list()[0])
        if abs(first_open - reference["open"]) > _PRICE_TOLERANCE:
            issues.append(
                MinuteIssue(
                    symbol,
                    day,
                    "first_bar_open",
                    f"首根 open {first_open} vs 日线 open {reference['open']}",
                )
            )

    if issues:
        log.warning("minutes.quality_issues", day=str(day), count=len(issues))
    return issues
