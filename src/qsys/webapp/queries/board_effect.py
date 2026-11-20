"""打板看板“昨日涨停今日表现”的纯派生（#662，设计文档 §8.9；R2 起输出契约模型）。

输入只用读取器已给出的 CompareGroup（昨日涨停个股、今日去向与今日涨跌幅），
前端不再各自重算（§9.5）。今日涨跌幅为 null 的票不进入平均，单独计数，不当 0。
口径（08b 设计者决定，可改）：
- 断板：去向为 rush / missed（今日未封、未炸），按今日涨跌幅 > 0 记红、≤ 0 记绿；
- 大面：今日涨跌幅 ≤ -5%（含跌停）。
"""

from __future__ import annotations

from collections.abc import Iterable

from qsys.webapp.contracts.board import EffectItem, LayerRow, MoneyEffect
from qsys.webapp.queries.board_reader import CompareGroup, CompareItem

BIG_LOSS = -0.05
_BREAK = {"rush", "missed"}


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _priced(items: Iterable[CompareItem]) -> list[float]:
    return [i.pct for i in items if i.pct is not None]


def money_effect(groups: list[CompareGroup]) -> MoneyEffect | None:
    if not groups:
        return None
    items = [i for g in groups for i in g.items]
    pcts = _priced(items)
    top = max(groups, key=lambda g: g.level)
    return MoneyEffect(
        total=len(items), priced=len(pcts), no_quote=len(items) - len(pcts),
        avg_pct=_mean(pcts), red_rate=sum(p > 0 for p in pcts) / len(pcts) if pcts else None,
        multi_avg_pct=_mean(_priced(i for g in groups if g.level >= 2 for i in g.items)),
        big_loss=sum(p <= BIG_LOSS for p in pcts), top_level=top.level,
        top_items=[EffectItem(symbol=i.symbol, name=i.name, pct=i.pct, status=i.status)
                   for i in top.items])


def layer_rows(groups: list[CompareGroup], prev_rates: dict[int, float | None]) -> list[LayerRow]:
    rows = []
    for g in sorted(groups, key=lambda g: g.level, reverse=True):
        n = {"promoted": 0, "broken": 0, "break_up": 0, "break_down": 0, "limit_down": 0,
             "unknown": 0, "no_quote": 0}
        for i in g.items:
            if i.status in ("promoted", "broken", "limit_down", "unknown"):
                n[i.status] += 1
            elif i.status in _BREAK:
                key = "no_quote" if i.pct is None else ("break_up" if i.pct > 0 else "break_down")
                n[key] += 1
        total = len(g.items)
        rows.append(LayerRow(level=g.level, total=total, **n,
                             rate=n["promoted"] / total if total else None,
                             prev_rate=prev_rates.get(g.level), avg_pct=_mean(_priced(g.items))))
    return rows


def layer_rates(rows: list[LayerRow]) -> dict[int, float | None]:
    return {r.level: r.rate for r in rows}
