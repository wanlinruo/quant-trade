"""模拟交易收益曲线的数据：日线历史与某日分时。

两层口径：
- 历史：每个交易日收盘后的**累计收益**（相对初始资金）；
- 分时：选中那天每分钟的**当日收益**（相对前一交易日收盘，首日相对初始资金），
  同时带累计收益供悬停查看。

基准（沪深300）的值按初始资金缩放存储；缺观测时保持 None，不推算。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date


def _ratio(value: float | None, base: float | None) -> float | None:
    if value is None or not base:
        return None
    return value / base - 1


def history_series(
    daily: Sequence[dict], initial: float,
    shadow: Mapping[str, float] | None = None,
) -> list[dict]:
    """日级历史曲线。

    `shadow` 是承袭本账户的影子盘每日总资产（按交易日）。两边本金不同，故一律比
    **累计收益率**：影子盘以本账户首个交易日当天（或之前最后一天）的总资产为基准。
    没有影子盘数据的交易日给 None，不插值。
    """
    base = _shadow_base(daily, shadow)
    return [{
        "session": row["session"],
        "strategy": _ratio(row["total_value"], initial),
        "benchmark": _ratio(row.get("benchmark_close"), initial),
        "shadow": (
            _ratio((shadow or {}).get(row["session"]), base) if base else None
        ),
    } for row in daily]


def _shadow_base(daily: Sequence[dict], shadow: Mapping[str, float] | None) -> float | None:
    """影子盘的基准点：本账户第一个交易日当天或之前最近一天的总资产。"""
    if not shadow or not daily:
        return None
    first = daily[0]["session"]
    earlier = [s for s in sorted(shadow) if s <= first]
    value = shadow.get(first) or (shadow[earlier[-1]] if earlier else None)
    return value if value else None


def intraday_series(
    points: Sequence[dict], daily: Sequence[dict], day: date, initial: float,
) -> list[dict]:
    earlier = [row for row in daily if row["session"] < day.isoformat()]
    if earlier:
        base, benchmark_base = earlier[-1]["total_value"], earlier[-1].get("benchmark_close")
    else:
        base = benchmark_base = initial
    return [{
        "at": row["at"],
        "time": row["at"][11:16],
        "strategy_day": _ratio(row["total_value"], base),
        "strategy_cum": _ratio(row["total_value"], initial),
        "benchmark_day": _ratio(row["benchmark_value"], benchmark_base),
        "benchmark_cum": _ratio(row["benchmark_value"], initial),
    } for row in points]
