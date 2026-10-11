"""Research-only loader for Issue #230's frozen 09:31 selections.

This file intentionally lives outside ``strategies/``: it is an offline
historical adjudication adapter, not an importable shadow/trading strategy.
"""

from qsys.research.first_board_intraday import (
    FirstMinuteResearchStrategy,
    FrozenSelectionBook,
)


def create(deps):
    params = deps.params or {}
    book = FrozenSelectionBook(
        str(params["selection_path"]), policy=str(params["selection_policy"]),
        split=str(params["signal_split"]),
    )
    return FirstMinuteResearchStrategy(
        book,
        position_weight=float(params.get("position_weight", 0.10)),
        entry_price_buffer=float(params.get("entry_price_buffer", 0.03)),
        exit_policy=str(params.get("exit_policy", "next_open")),
    )
