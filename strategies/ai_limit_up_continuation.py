"""AI 连板强势延续策略（Issue #229）。

本文件可在「策略研究」页面从仓库导入。默认只用于历史回测，
不会自动进入模拟或实盘。回测应保持页面默认的「09:31 连续竞价判定」。
"""

from pathlib import Path

from qsys.strategy.ai_limit_up_continuation import (
    AiLimitUpContinuationStrategy,
    ContinuationSelection,
    ContinuationSignalBook,
)


PARAMETERS = {
    "ranking_path": {
        "type": "str",
        "default": "/data/qsys/research/issue-210/run-20261010/predictions.parquet",
        "label": "AI 排名文件",
        "description": "#210 冻结 predictions.parquet；仅读取信号列",
    },
    "signal_split": {
        "type": "str", "default": "ALL", "choices": ["ALL", "TEST", "OBS"],
        "label": "信号分段", "description": "研究运行可限定 TEST/OBS",
    },
    "top_k": {
        "type": "int", "default": 3, "min": 1, "max": 20,
        "label": "AI Top-K", "description": "只读取每日排名最高的 K 只",
    },
    "gap_min": {
        "type": "float", "default": 0.0, "min": -0.2, "max": 0.2,
        "label": "竞价涨幅下限", "description": "包含下限",
    },
    "gap_max": {
        "type": "float", "default": 0.02, "min": -0.2, "max": 0.2,
        "label": "竞价涨幅上限", "description": "不包含上限",
    },
    "position_weight": {
        "type": "float", "default": 0.10, "min": 0.01, "max": 0.33,
        "label": "单槽仓位", "description": "默认每只占总资产 10%",
    },
    "max_positions": {
        "type": "int", "default": 3, "min": 1, "max": 3,
        "label": "最大持仓数", "description": "冻结研究上限为 3",
    },
    "entry_price_buffer": {
        "type": "float", "default": 0.005, "min": 0.0, "max": 0.05,
        "label": "入场保护价上浮", "description": "默认 0.5%，且不超过涨停价",
    },
    "exit_policy": {
        "type": "str", "default": "next_open",
        "choices": ["next_open", "11:25", "14:25"],
        "label": "退出时点", "description": "入场后下一交易日首次卖出尝试",
    },
}


def create(deps):
    params = deps.params or {}
    book = ContinuationSignalBook(
        Path(str(params.get("ranking_path", PARAMETERS["ranking_path"]["default"]))),
        model="lightgbm", top_k=int(params.get("top_k", 3)),
        split=str(params.get("signal_split", "ALL")),
    )
    selection = ContinuationSelection(
        book, gap_min=float(params.get("gap_min", 0.0)),
        gap_max=float(params.get("gap_max", 0.02)),
    )
    return AiLimitUpContinuationStrategy(
        selection, calendar=deps.calendar,
        position_weight=float(params.get("position_weight", 0.10)),
        max_positions=int(params.get("max_positions", 3)),
        entry_price_buffer=float(params.get("entry_price_buffer", 0.005)),
        exit_policy=str(params.get("exit_policy", "next_open")),
    )
