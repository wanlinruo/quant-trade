"""Research-only adapter for the Issue #230 09:30 opening-minute pilot."""

from datetime import time

from qsys.research.first_board_intraday import (
    FirstMinuteResearchStrategy,
    FrozenSelectionBook,
)


def create(deps):
    params = deps.params or {}
    book = FrozenSelectionBook(
        str(params["selection_path"]), policy=str(params["selection_policy"]),
        split="PILOT_EVAL",
    )
    return FirstMinuteResearchStrategy(
        book,
        position_weight=float(params.get("position_weight", 0.10)),
        entry_price_buffer=float(params.get("entry_price_buffer", 0.03)),
        exit_policy=str(params.get("exit_policy", "next_open")),
        decision_at=time(9, 30), strategy_tag="AI_FIRST_BOARD_0930_PILOT",
    )
