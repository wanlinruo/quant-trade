"""通知：把已经产生的运行记录与告警送到人眼前。

和 ``qsys.ops.alerts`` 的分工：alerts 管「怎么送」（sink、失败隔离），
这里管「送不送、送什么」。

**只读不写**：所有判断都基于已经落盘的运行记录，不参与也不改变交易流程。
发送失败由 AlertBus 吞掉——通知挂了绝不能连累交易。

三个引擎每天各 7 个事件，全推是骚扰。只在四种情形推（2026-09-29 与维护者定）：

- 有实际买卖：下单、成交或拒单
- 竞价决策结果：每天一条，即使不交易也推，好确认策略在跑
- 策略出错
- 收盘汇总
"""

from __future__ import annotations

from qsys.ops.alerts import AlertBus
from qsys.ops.strategy_log import RunRecord

# 这两个阶段每天固定推一条；其余阶段只有出事或有买卖才推
_ALWAYS_PHASES = frozenset({"auction", "session_end"})


def should_notify(record: RunRecord) -> bool:
    if record.error:
        return True
    if record.orders or record.rejects:
        return True
    return record.phase in _ALWAYS_PHASES


def _money(value: float) -> str:
    return f"{value:,.2f}"


def compose(name: str, record: RunRecord) -> tuple[str, str]:
    """(标题, 正文)。标题要能在通知列表里一眼看出发生了什么。"""
    if record.error:
        title = f"{name}｜{record.phase} 出错"
        return title, f"{record.event_key}\n{record.error}"

    if record.orders or record.rejects:
        title = f"{name}｜下单 {record.orders} 笔"
        if record.rejects:
            title += f"，拒单 {record.rejects} 笔"
    elif record.phase == "auction":
        title = f"{name}｜竞价决策"
    else:
        title = f"{name}｜收盘"

    lines = [record.summary or "（无摘要）"]
    if record.reasons:
        lines.append("　" + "　".join(
            f"{reason} {count}" for reason, count in record.reasons.items()
        ))
    lines.append(
        f"总资产 {_money(record.total_value)}（现金 {_money(record.cash)}，"
        f"持仓 {record.position_count} 只）"
    )
    lines.append(f"{record.session} {record.phase}")
    return title, "\n".join(lines)


class StrategyNotifier:
    """把运行记录按上面的规则推出去。alerts 为 None 时整体静默。"""

    def __init__(self, alerts: AlertBus | None, name: str) -> None:
        self._alerts = alerts
        self._name = name

    def on_run(self, record: RunRecord) -> bool:
        """返回是否真的发了，便于调用方留痕与测试。"""
        if self._alerts is None or not should_notify(record):
            return False
        title, detail = compose(self._name, record)
        if record.error:
            self._alerts.critical(title, detail)
        elif record.rejects:
            self._alerts.warning(title, detail)
        else:
            self._alerts.info(title, detail)
        return True

