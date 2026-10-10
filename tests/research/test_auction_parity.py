from datetime import date, datetime, timedelta

import polars as pl

from qsys.research.auction_parity import evaluate_parity


def test_parity_infers_share_to_lot_units_and_passes_complete_overlap():
    history, ticks = [], []
    start = date(2026, 8, 1)
    for index in range(1_000):
        day = start + timedelta(days=index % 25)
        symbol = f"{600000 + index:06d}.SH"
        history.append({
            "symbol": symbol, "dt": day, "open": 10.0,
            "volume_raw": 100_000.0, "amount_raw": 1_000_000.0,
        })
        ticks.append({
            "symbol": symbol, "dt": day,
            "ts": datetime.combine(day, datetime.min.time()).replace(
                hour=9, minute=25, second=4
            ),
            "last_price": 10.0, "volume": 1_000, "amount": 1_000_000.0,
        })
    verdict = evaluate_parity(pl.DataFrame(history), pl.DataFrame(ticks))
    assert verdict["auction_parity_pass"] is True
    assert verdict["volume_scale_to_qmt_lots"] == 0.01
    assert verdict["amount_scale_to_qmt_cny"] == 1.0


def test_parity_rejects_price_disagreement():
    history = pl.DataFrame({
        "symbol": ["600001.SH"], "dt": [date(2026, 9, 1)], "open": [10.0],
        "volume_raw": [100_000.0], "amount_raw": [1_000_000.0],
    })
    ticks = pl.DataFrame({
        "symbol": ["600001.SH"], "dt": [date(2026, 9, 1)],
        "ts": [datetime(2026, 9, 1, 9, 25, 4)], "last_price": [9.0],
        "volume": [1_000], "amount": [1_000_000.0],
    })
    verdict = evaluate_parity(history, ticks)
    assert verdict["auction_parity_pass"] is False
    assert verdict["checks"]["price_match_rate_at_least_99_5pct"] is False
