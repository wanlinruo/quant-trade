"""绩效指标。

periods_per_year 默认 243——A 股近年的年均交易日数。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import polars as pl

from qsys.core.trading import Fill, Side

_TRADING_DAYS = 243

# 年化波动率低于此值即视为零。用 `> 0` 做守卫太弱：恒定收益序列的浮点
# 噪声（~1e-15）会让夏普算出 1e14 这种看似天才实则毫无意义的数字。
# 真实策略的年化波动率最低也在 1e-3 量级，1e-9 远在噪声之上、真值之下。
_MIN_VOLATILITY = 1e-9


@dataclass(frozen=True, slots=True)
class Metrics:
    total_return: float = 0.0
    annualized_return: float = 0.0
    volatility: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_days: int = 0
    n_trades: int = 0
    win_rate: float = 0.0
    turnover: float = 0.0


def _drawdown(values: Sequence[float]) -> tuple[float, int]:
    peak = values[0]
    worst = 0.0
    underwater = 0
    longest = 0
    for value in values:
        if value >= peak:
            peak = value
            underwater = 0
        else:
            underwater += 1
            longest = max(longest, underwater)
            worst = max(worst, 1.0 - value / peak)
    return worst, longest


def _trade_stats(fills: Sequence[Fill]) -> tuple[int, float, float]:
    """按先进先出配对买卖，统计笔数、胜率与换手额。"""
    if not fills:
        return 0, 0.0, 0.0

    lots: dict[object, list[tuple[int, float]]] = {}
    wins = closed = 0
    turnover = 0.0

    for fill in fills:
        turnover += fill.gross
        queue = lots.setdefault(fill.symbol, [])
        if fill.side is Side.BUY:
            queue.append((fill.qty, fill.price))
            continue
        remaining = fill.qty
        while remaining > 0 and queue:
            qty, cost = queue[0]
            matched = min(qty, remaining)
            closed += 1
            if fill.price > cost:
                wins += 1
            remaining -= matched
            if matched == qty:
                queue.pop(0)
            else:
                queue[0] = (qty - matched, cost)

    win_rate = wins / closed if closed else 0.0
    return len(fills), win_rate, turnover


def compute_metrics(
    equity: pl.DataFrame,
    fills: Sequence[Fill] = (),
    *,
    periods_per_year: int = _TRADING_DAYS,
    risk_free_rate: float = 0.0,
) -> Metrics:
    n_trades, win_rate, turnover = _trade_stats(list(fills))

    if equity.height < 2:
        return Metrics(n_trades=n_trades, win_rate=win_rate, turnover=turnover)

    values = equity["total_value"].to_list()
    if values[0] <= 0:
        return Metrics(n_trades=n_trades, win_rate=win_rate, turnover=turnover)

    total_return = values[-1] / values[0] - 1.0
    periods = len(values) - 1
    annualized = (1.0 + total_return) ** (periods_per_year / periods) - 1.0

    returns = [
        values[i] / values[i - 1] - 1.0 for i in range(1, len(values)) if values[i - 1] > 0
    ]
    mean = sum(returns) / len(returns) if returns else 0.0
    variance = (
        sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        if len(returns) > 1
        else 0.0
    )
    volatility = math.sqrt(variance) * math.sqrt(periods_per_year)

    excess = mean * periods_per_year - risk_free_rate
    sharpe = excess / volatility if volatility > _MIN_VOLATILITY else 0.0

    max_dd, dd_days = _drawdown(values)

    return Metrics(
        total_return=total_return,
        annualized_return=annualized,
        volatility=volatility,
        sharpe=sharpe,
        max_drawdown=max_dd,
        max_drawdown_days=dd_days,
        n_trades=n_trades,
        win_rate=win_rate,
        turnover=turnover,
    )
