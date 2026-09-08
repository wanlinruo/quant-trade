"""每日运行报告。

一条消息回答四个问题：钱变了多少、成交了什么、有什么被拒了、
对账过没过。异常时 headline 必须一眼可读——你可能是在手机上看它。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import polars as pl

from qsys.ops.journal import SessionJournal


@dataclass(frozen=True, slots=True)
class EquityLine:
    dt: date
    cash: float = 0.0
    market_value: float = 0.0
    total_value: float = 0.0
    change_pct: float = 0.0


@dataclass(frozen=True, slots=True)
class DailyReport:
    dt: date
    equity: EquityLine
    fills: tuple[dict, ...]
    rejects: tuple[dict, ...]
    reconciliations: tuple[dict, ...]
    quality_summary: str = ""
    dead_sources: tuple[str, ...] = ()

    @property
    def failed_reconciliations(self) -> tuple[dict, ...]:
        return tuple(r for r in self.reconciliations if not r["is_consistent"])

    @property
    def is_healthy(self) -> bool:
        return not self.failed_reconciliations and not self.dead_sources

    @property
    def headline(self) -> str:
        if self.is_healthy:
            return (
                f"正常 · {self.dt} · 净值 {self.equity.total_value:,.2f} "
                f"({self.equity.change_pct:+.2%}) · 成交 {len(self.fills)} 笔"
            )
        problems: list[str] = []
        if self.failed_reconciliations:
            problems.append(f"对账不一致 {len(self.failed_reconciliations)} 次")
        if self.dead_sources:
            problems.append(f"失联: {', '.join(self.dead_sources)}")
        return f"异常 · {self.dt} · " + " · ".join(problems)

    def as_text(self) -> str:
        lines = [
            "=" * 60,
            f"每日运行报告 {self.dt}",
            "=" * 60,
            self.headline,
            "",
            "净值",
            f"  现金      : {self.equity.cash:,.2f}",
            f"  持仓市值  : {self.equity.market_value:,.2f}",
            f"  总值      : {self.equity.total_value:,.2f} ({self.equity.change_pct:+.2%})",
            "",
            f"成交 ({len(self.fills)} 笔)",
        ]
        for f in self.fills[:20]:
            lines.append(
                f"  {f['symbol']} {f['side']} {f['qty']} @ {f['price']:.2f} "
                f"费用 {f['fee']:.2f}"
            )
        if len(self.fills) > 20:
            lines.append(f"  ... 另有 {len(self.fills) - 20} 笔")

        lines += ["", f"拒单 ({len(self.rejects)} 笔)"]
        for r in self.rejects[:20]:
            lines.append(f"  {r['symbol']} {r['qty']} —— {r['reason']}")
        if len(self.rejects) > 20:
            lines.append(f"  ... 另有 {len(self.rejects) - 20} 笔")

        lines += ["", f"对账 ({len(self.reconciliations)} 次)"]
        for r in self.reconciliations:
            status = "一致" if r["is_consistent"] else "不一致"
            lines.append(f"  [{r['layer']}] {status}")
            if not r["is_consistent"]:
                lines.append(
                    f"      现金差 {r['cash_diff']:,.2f} · 持仓不符 {r['n_mismatches']} 项"
                )
                if r["detail"]:
                    lines.append(f"      {r['detail']}")

        lines += ["", "数据质量", f"  {self.quality_summary or '（未运行）'}"]
        if self.dead_sources:
            lines += ["", "失联", f"  {', '.join(self.dead_sources)}"]
        lines.append("=" * 60)
        return "\n".join(lines)


def _rows(df: pl.DataFrame) -> tuple[dict, ...]:
    return tuple(df.iter_rows(named=True))


def build_daily_report(
    journal: SessionJournal,
    dt: date,
    *,
    quality_summary: str = "",
    dead_sources: Sequence[str] = (),
) -> DailyReport:
    curve = journal.equity(end=dt)
    equity = EquityLine(dt=dt)
    if curve.height:
        last = curve.row(curve.height - 1, named=True)
        change = 0.0
        if curve.height > 1:
            prev = curve.row(curve.height - 2, named=True)["total_value"]
            if prev:
                change = last["total_value"] / prev - 1.0
        equity = EquityLine(
            dt=dt, cash=last["cash"], market_value=last["market_value"],
            total_value=last["total_value"], change_pct=change,
        )

    return DailyReport(
        dt=dt,
        equity=equity,
        fills=_rows(journal.fills(dt)),
        rejects=_rows(journal.rejects(dt)),
        reconciliations=_rows(journal.reconciliations(dt)),
        quality_summary=quality_summary,
        dead_sources=tuple(dead_sources),
    )
