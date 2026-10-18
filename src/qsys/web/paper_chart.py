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

    `shadow` 仅有每日总资产，没有完整资金变动流水，无法区分盈亏与入出金。
    保留参数兼容调用方，但收益率返回 None，不能凭资产跳变推断资金流。
    """
    return [{
        "session": row["session"],
        "strategy": _ratio(row["total_value"], initial),
        "benchmark": _ratio(row.get("benchmark_close"), initial),
        "shadow": None,
    } for row in daily]


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
