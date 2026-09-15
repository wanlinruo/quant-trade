"""Structural Strategy V1.2 —— 聚宽原版的 qsys 移植，单文件入口。

规则本身在 `qsys.strategy.structural` 里分了 8 个模块，每个对应原文的一段，
便于逐条对账到行号。这个文件是**装载入口**：说明它要什么依赖、跑什么调度。

跑：
  uv run python scripts/run_backtest.py \
      --strategy strategies/structural_v1_2.py \
      --start 2025-09-10 --end 2026-09-08 --cash 100000 --jq-compat
"""

from qsys.strategy.structural.selection import StructuralSelection
from qsys.strategy.structural.strategy import StructuralStrategy


PARAMETERS = {
    "require_auction_volume": {
        "type": "bool", "default": True, "label": "要求竞价成交量",
        "description": "缺少竞价量时是否拒绝候选标的",
    }
}


def create(deps):
    """`--strategy` 装载时调用。调度由策略自己在 on_init 里声明。"""
    require_auction_volume = (deps.params or {}).get("require_auction_volume", True)
    return StructuralStrategy(
        StructuralSelection(
            deps.feed, deps.market, deps.query,
            require_auction_volume=bool(require_auction_volume),
        )
    )
