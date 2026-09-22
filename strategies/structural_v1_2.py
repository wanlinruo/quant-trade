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


# 入场参数的默认值等于原策略行为（09:30、保护价与定量均为开盘价 +0.2%）。
# 推荐的实盘口径是 09:25:10 / 0.03 / 0.005：09:25 撮合后用最终数据决策、09:30 前
# 挂单，保护价放宽到 +3% 才能全部成交，定量只预留 +0.5%（研究文档第 6 节）。
PARAMETERS = {
    "require_auction_volume": {
        "type": "bool", "default": True, "label": "要求竞价成交量",
        "description": "缺少竞价量时是否拒绝候选标的",
    },
    "auction_at": {
        "type": "str", "default": "09:30", "choices": ["09:30", "09:25:10"],
        "label": "竞价决策时刻",
        "description": "09:30 为原策略；09:25:10 为撮合后决策、09:30 前挂单",
    },
    "entry_price_buffer": {
        "type": "float", "default": 0.002, "min": 0.0, "max": 0.2,
        "label": "保护价上浮",
        "description": "限价 = min(开盘价 ×(1+上浮), 涨停价)；原策略 0.002，推荐 0.03",
    },
    "sizing_price_buffer": {
        "type": "float", "default": 0.002, "min": 0.0, "max": 0.2,
        "label": "定量价上浮",
        "description": "按 开盘价 ×(1+上浮) 计算股数；原策略与保护价相同，推荐 0.005",
    },
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
