"""分钟线质量校验。

分钟线来自 QMT、日线来自 Tushare，**两个独立数据源**。它们必须对得上：
  · 分钟成交量之和 ≈ 日线成交量
  · 有成交分钟的最高价之最大 == 日线最高价
  · 首根有成交分钟线的 open == 日线 open（竞价成交价即开盘价，2-1a 已实测）

价格只看有成交的 bar：集合竞价无成交时，QMT 仍给出成交量为 0、四价等于昨收的
09:30 占位 bar，真实开盘价是连续竞价第一笔（2026-09-16..18 生产实测 297 例全部
如此，改看有成交 bar 后差异为 0）。

北交所盘后固定价格交易被 QMT 并入 15:00 那根 bar，Tushare 日线不计入，且无法
从同一根 bar 里拆出。北交所成交量因此做区间检查：日线成交量须落在
[分钟合计 − 15:00 bar 成交量, 分钟合计] 之间（各留 1% 余量）。

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

# 09:30 是开盘集合竞价那一根，属于正常数据，不能判成时段外。
# 13:00 则确实不存在（12:59–13:00 无交易），故下午自 13:01 起。
_MORNING = (time(9, 30), time(11, 30))
_AFTERNOON = (time(13, 1), time(15, 0))


@dataclass(frozen=True, slots=True)
class MinuteIssue:
    symbol: str
    dt: date
    rule: str
    detail: str
    known_issue: str | None = None


# 精确到证券、交易日和规则。这里不是容差白名单：任何其它证券、日期或规则的
# 差异仍会阻断。D-24 的证据与处置见 deferred-corrections.md。
_KNOWN_SOURCE_DIFFERENCES = {
    ("920055.BJ", date(2026, 9, 21), "high_vs_daily"): "D-24",
}


def _bj_after_hours_explains(
    symbol: str, group: pl.DataFrame, minute_volume: int, daily_volume: float
) -> bool:
    """北交所：日线成交量是否落在「扣除/含 15:00 bar」两个分钟合计之间。"""
    if not symbol.endswith(".BJ"):
        return False
    closing = group.filter(pl.col("dt").dt.time() == _AFTERNOON[1])["volume"].sum()
    lower = (minute_volume - closing) * (1 - _VOLUME_TOLERANCE)
    upper = minute_volume * (1 + _VOLUME_TOLERANCE)
    return lower <= daily_volume <= upper


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
            if drift > _VOLUME_TOLERANCE and not _bj_after_hours_explains(
                symbol, group, minute_volume, reference["volume"]
            ):
                issues.append(
                    MinuteIssue(
                        symbol,
                        day,
                        "volume_vs_daily",
                        f"分钟合计 {minute_volume} vs 日线 {reference['volume']}",
                    )
                )

        traded = group.filter(pl.col("volume") > 0)
        if traded.height == 0:
            continue  # 全天无成交 bar：没有可比的成交价
        minute_high = float(traded["high"].max())
        if abs(minute_high - reference["high"]) > _PRICE_TOLERANCE:
            rule = "high_vs_daily"
            issues.append(
                MinuteIssue(
                    symbol,
                    day,
                    rule,
                    f"分钟最高 {minute_high} vs 日线 {reference['high']}",
                    _KNOWN_SOURCE_DIFFERENCES.get((symbol, day, rule)),
                )
            )

        first_open = float(traded["open"].to_list()[0])
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
