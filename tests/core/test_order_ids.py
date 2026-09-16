from datetime import date

from qsys.core.order_ids import live_order_id
from qsys.agent.xtquant_adapter import _ours


def test_live_order_id_is_short_deterministic_and_unique():
    first = live_order_id("structural", date(2026, 9, 16), "auction", 1)
    assert first == live_order_id("structural", date(2026, 9, 16), "auction", 1)
    assert len(first.encode()) <= 24
    assert first != live_order_id("structural", date(2026, 9, 16), "auction", 2)
    assert _ours(first)
    assert not _ours("manual-order")
