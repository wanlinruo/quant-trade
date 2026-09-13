"""A 股市场预设。

core/ 不认识这些数值——它们只出现在这里。将来接币圈或美股，
新增一个同形状的模块即可，core/ 无需改动。

板块取值由本模块的 `board_of()` 给出，`PRICE_LIMITS` 的键与它一一对应。
"""

from __future__ import annotations

from qsys.core.constraints import (
    CashConstraint,
    ConstraintPipeline,
    LotSizeConstraint,
    NoShortConstraint,
    PriceLimitConstraint,
    SettlementConstraint,
    SuspensionConstraint,
)


def board_of(code: str) -> str:
    """按号段判板块。入参是**不带交易所后缀**的代码。

    与 PRICE_LIMITS 同住一处：板块词表和它的涨跌幅上限必须一起改，
    分开放就会出现「新增板块忘了给上限」而无人报错。
    """
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("300", "301")):
        return "gem"
    if code[:2] in ("43", "83", "87", "92"):
        return "bse"
    if code[:2] in ("90", "20"):
        # B 股：外币计价、交易规则与 A 股不同，单独标注以便股票池排除
        return "bshare"
    return "main"


# 日涨跌幅上限。ST 为 5%，不在此表——ST 是股票的状态而非板块属性，
# 自 2026-09-12 起由 data/pipeline/flags.py 从付费源 name 列直取判定。
# 本表只登记板块档位。
PRICE_LIMITS: dict[str, float] = {
    "main": 0.10,
    "gem": 0.20,
    "star": 0.20,
    "bse": 0.30,
    "bshare": 0.10,
}

LOT_SIZE = 100


def ashare_pipeline(*, fee_buffer: float = 0.001) -> ConstraintPipeline:
    """A 股约束流水线。

    顺序有实质意义：硬拒绝在前（停牌、涨跌停），数量夹取在后，
    **整手必须最后**——否则现金夹取会留下无法成交的零股。
    """
    return ConstraintPipeline(
        [
            SuspensionConstraint(),
            PriceLimitConstraint(PRICE_LIMITS),
            NoShortConstraint(),
            SettlementConstraint(),
            CashConstraint(fee_buffer=fee_buffer),
            LotSizeConstraint(lot=LOT_SIZE),
        ]
    )


def ashare_pre_trade_pipeline(*, fee_buffer: float = 0.001) -> ConstraintPipeline:
    """实盘下单时刻可评估的约束子集。

    排除 price_limit 与 suspension —— 它们依赖当时行情，核心侧下单时未必
    持有实时报价。这两条留给券商，其拒单会经回报流回来。

    保留的四条只依赖账户状态，核心侧必须自己检查：
    依赖券商兜底意味着本地持仓推演与实际不符，而对账的前提正是本地推演可信。
    """
    return ConstraintPipeline(
        [
            NoShortConstraint(),
            SettlementConstraint(),
            CashConstraint(fee_buffer=fee_buffer),
            LotSizeConstraint(lot=LOT_SIZE),
        ]
    )
