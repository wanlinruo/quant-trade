"""Wheel-bound Structural Strategy V1.2 entrypoint for isolated backtests."""

from qsys.strategy.structural.selection import StructuralSelection
from qsys.strategy.structural.strategy import StructuralStrategy


PARAMETERS = {
    "require_auction_volume": {
        "type": "bool", "default": True,
    },
    "gate_enabled": {
        "type": "bool", "default": True,
    },
    "auction_at": {
        "type": "str", "default": "09:30", "choices": ["09:30", "09:25:10"],
    },
    "entry_price_buffer": {
        "type": "float", "default": 0.002, "min": 0.0, "max": 0.2,
    },
    "sizing_price_buffer": {
        "type": "float", "default": 0.002, "min": 0.0, "max": 0.2,
    },
}


def create(deps):
    params = deps.params or {}
    return StructuralStrategy(
        StructuralSelection(
            deps.feed, deps.market, deps.query,
            require_auction_volume=bool(params.get("require_auction_volume", True)),
            gate_enabled=bool(params.get("gate_enabled", True)),
        ),
        calendar=deps.calendar,
    )
