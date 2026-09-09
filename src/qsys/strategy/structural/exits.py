"""退出决策，逐条对照原文 L615-L768。

**本策略是隔夜策略**。原文 `get_sellable_lots`（L606）写明：

    \"\"\"A股T+1：只允许卖出入场日早于当前交易日的lot。\"\"\"
    if lot.get('entry_date') is not None and lot['entry_date'] < today

所以三个检查点卖的是**前一日买入、持有过夜的仓位**，不是当日买的。
引擎既有的 `unfreeze()` 与 `SettlementConstraint` 已经正确实现了这条规则。

三个检查点的分工（原文 L188-L204 的 run_daily）：

| 时刻 | 作用于 | 规则 |
|---|---|---|
| 11:25 | HIGH | `legacy_exit`（止盈 / MA5 止损） |
| 11:25 | LOW | `low_1125`（涨停持有；盈利卖；不盈利等 14:25） |
| 14:25 | LOW | `low_1425`（涨停持有过夜；否则无条件卖） |
| 14:50 | HIGH | `legacy_exit` |

这些函数都是纯函数：给价格给判定，不碰账户、不访问数据。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExitDecision:
    should_exit: bool
    reason: str | None = None
    ret: float | None = None


_HOLD = ExitDecision(False)


def dynamic_ma5(recent_closes: Sequence[float], last_price: float) -> float | None:
    """动态 MA5（原文 L615）：最近 4 日收盘均值 × 4，加上现价，再除 5。

    「动态」是指第 5 个点用的是当前价而不是今日收盘——盘中就能算。
    """
    closes = list(recent_closes)[-4:]
    if not closes:
        return None
    return (sum(closes) / len(closes) * 4 + last_price) / 5


def _return_pct(last_price: float, entry_price: float) -> float:
    return 100.0 * (last_price / entry_price - 1.0)


def _is_limit_up(last_price: float, limit_up: float | None) -> bool:
    """拿不到涨停价时按「未涨停」处理。

    LOW 的 14:25 是无条件卖，误判成涨停会让仓位莫名过夜。
    """
    return limit_up is not None and last_price >= limit_up


def legacy_exit(
    last_price: float,
    limit_up: float | None,
    entry_price: float | None,
    ma5: float | None,
) -> ExitDecision:
    """HIGH 分支的退出（原文 L624）。

    **判定顺序照抄**：先止盈再 MA5。现价既高于入场价又低于 MA5 时，
    原版走 TAKE_PROFIT 而非 MA5_STOP——看着像 bug，但原策略是 FROZEN
    版本，改了就没法对账。
    """
    if not entry_price or entry_price <= 0:
        return _HOLD

    if not _is_limit_up(last_price, limit_up) and last_price > entry_price:
        return ExitDecision(True, "TAKE_PROFIT", _return_pct(last_price, entry_price))

    if ma5 is not None and last_price < ma5:
        return ExitDecision(True, "MA5_STOP", _return_pct(last_price, entry_price))

    return _HOLD


def low_1125(
    last_price: float, limit_up: float | None, entry_price: float | None
) -> ExitDecision:
    """LOW 分支 11:25（原文 L707）：涨停持有；盈利卖；不盈利等 14:25。"""
    if not entry_price or entry_price <= 0:
        return _HOLD

    ret = _return_pct(last_price, entry_price)
    if _is_limit_up(last_price, limit_up):
        return ExitDecision(False, None, ret)
    if ret > 0:
        return ExitDecision(True, "LW_1125_PROFIT_NON_LIMIT", ret)
    return ExitDecision(False, None, ret)


def low_1425(
    last_price: float, limit_up: float | None, entry_price: float | None
) -> ExitDecision:
    """LOW 分支 14:25（原文 L742）：涨停持有过夜；否则**无条件卖**。"""
    if not entry_price or entry_price <= 0:
        return _HOLD

    ret = _return_pct(last_price, entry_price)
    if _is_limit_up(last_price, limit_up):
        return ExitDecision(False, None, ret)
    return ExitDecision(True, "LW_1425_NON_LIMIT_EXIT", ret)
