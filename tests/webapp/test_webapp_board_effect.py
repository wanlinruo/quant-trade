"""打板看板赚钱效应与逐层晋级表（#662，设计文档 §8.9；R2 起返回契约模型）。"""

from __future__ import annotations

import pytest

from qsys.webapp.queries.board_effect import layer_rates, layer_rows, money_effect
from qsys.webapp.queries.board_reader import CompareGroup, CompareItem


def g(level, *items):
    return CompareGroup(
        level=level,
        items=[CompareItem(symbol=s, name=s, pct=p, column=None, status=st, first_seal=None,
                           open_times=None, prediction=None) for s, st, p in items],
        promoted=sum(st == "promoted" for _, st, _ in items))


def test_effect_basic():
    e = money_effect([g(2, ("A", "promoted", 0.10), ("B", "limit_down", -0.10)),
                      g(1, ("C", "missed", 0.02), ("D", "missed", -0.06))])
    assert e.total == 4 and e.priced == 4 and e.no_quote == 0
    assert e.avg_pct == pytest.approx(-0.01) and e.red_rate == 0.5
    assert e.multi_avg_pct == pytest.approx(0.0) and e.big_loss == 2
    assert e.top_level == 2 and [i.symbol for i in e.top_items] == ["A", "B"]
    assert e.top_items[1].model_dump() == {"symbol": "B", "name": "B", "pct": -0.10, "status": "limit_down"}


def test_effect_ignores_null_pct_and_counts_it():
    e = money_effect([g(1, ("A", "missed", None), ("B", "missed", 0.04))])
    assert e.priced == 1 and e.no_quote == 1
    assert e.avg_pct == pytest.approx(0.04) and e.red_rate == 1.0
    assert e.multi_avg_pct is None


def test_effect_all_unpriced_has_no_ratios():
    e = money_effect([g(1, ("A", "missed", None))])
    assert e.avg_pct is None and e.red_rate is None and e.big_loss == 0


def test_effect_none_when_heights_unavailable():
    assert money_effect([]) is None


def test_layer_rows_split_breaks_by_sign_and_keep_prev_rate():
    rows = layer_rows([g(1, ("A", "promoted", 0.1), ("B", "rush", 0.08), ("C", "missed", 0.0),
                         ("D", "broken", 0.03), ("E", "unknown", 0.1), ("F", "missed", None))],
                      {1: 0.25})
    r = rows[0]
    assert (r.promoted, r.break_up, r.break_down, r.broken, r.unknown,
            r.no_quote, r.limit_down) == (1, 1, 1, 1, 1, 1, 0)
    assert r.total == 6 and r.level == 1
    assert r.rate == pytest.approx(1 / 6) and r.prev_rate == 0.25
    assert r.avg_pct == pytest.approx((0.1 + 0.08 + 0.0 + 0.03 + 0.1) / 5)


def test_layer_rows_sorted_desc_and_prev_rate_missing_is_none():
    rows = layer_rows([g(1, ("A", "missed", 0.1)), g(3, ("B", "promoted", 0.1))], {})
    assert [r.level for r in rows] == [3, 1]
    assert rows[0].prev_rate is None


def test_layer_rates_maps_level_to_rate():
    rows = layer_rows([g(2, ("A", "promoted", 0.1), ("B", "missed", -0.02))], {})
    assert layer_rates(rows) == {2: 0.5}
