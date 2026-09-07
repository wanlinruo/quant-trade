"""三层对账 —— 整个可靠性设计的压舱石。

  盘中  持仓快照 vs 本地推演   不一致立即停止下单
  盘后  本地订单簿 vs 实际成交 逐笔比对
  回测  SimBroker vs 实盘成交  差异须能被滑点模型解释

第三层是这套架构独有的能力：它让回测随实盘运行越来越准，
而不是永远停在假设上。积累的差异数据是五期滑点校准的输入。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import structlog

from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill
from qsys.live.messages import AccountSnapshot

log = structlog.get_logger(__name__)

_PRICE_TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class PositionMismatch:
    symbol: str
    local_qty: int
    remote_qty: int


@dataclass(frozen=True, slots=True)
class PositionReconResult:
    is_consistent: bool
    cash_diff: float
    mismatches: tuple[PositionMismatch, ...]

    def summary(self) -> str:
        if self.is_consistent:
            return "reconcile: consistent"
        lines = [f"reconcile: INCONSISTENT (cash diff {self.cash_diff:,.2f})"]
        for m in self.mismatches:
            lines.append(f"  {m.symbol}: local={m.local_qty} remote={m.remote_qty}")
        return "\n".join(lines)


def reconcile_positions(
    local: Portfolio, remote: AccountSnapshot, *, cash_tolerance: float = 1.0
) -> PositionReconResult:
    local_qty = {str(s): p.total_qty for s, p in local.positions.items()}
    remote_qty = {s: p.total_qty for s, p in remote.positions.items()}

    mismatches = [
        PositionMismatch(symbol, local_qty.get(symbol, 0), remote_qty.get(symbol, 0))
        for symbol in sorted(set(local_qty) | set(remote_qty))
        if local_qty.get(symbol, 0) != remote_qty.get(symbol, 0)
    ]
    cash_diff = abs(local.cash - remote.cash)
    consistent = not mismatches and cash_diff <= cash_tolerance

    if not consistent:
        log.error("reconcile.inconsistent", cash_diff=cash_diff, mismatches=len(mismatches))

    return PositionReconResult(consistent, cash_diff, tuple(mismatches))


@dataclass(frozen=True, slots=True)
class FillMismatch:
    client_order_id: str
    kind: str  # missing_remote / missing_local / qty_differs / price_differs
    detail: str


@dataclass(frozen=True, slots=True)
class FillReconResult:
    is_consistent: bool
    mismatches: tuple[FillMismatch, ...]


def reconcile_fills(
    local_fills: Sequence[Fill], remote_fills: Sequence[Fill]
) -> FillReconResult:
    local_by_id = {f.order_id: f for f in local_fills}
    remote_by_id = {f.order_id: f for f in remote_fills}
    mismatches: list[FillMismatch] = []

    for order_id in sorted(set(local_by_id) | set(remote_by_id)):
        ours = local_by_id.get(order_id)
        theirs = remote_by_id.get(order_id)
        if theirs is None:
            mismatches.append(FillMismatch(order_id, "missing_remote", "本地有成交，远端无"))
        elif ours is None:
            mismatches.append(FillMismatch(order_id, "missing_local", "远端有成交，本地无"))
        elif ours.qty != theirs.qty:
            mismatches.append(
                FillMismatch(order_id, "qty_differs", f"local={ours.qty} remote={theirs.qty}")
            )
        elif abs(ours.price - theirs.price) > _PRICE_TOLERANCE:
            mismatches.append(
                FillMismatch(order_id, "price_differs",
                             f"local={ours.price} remote={theirs.price}")
            )

    return FillReconResult(not mismatches, tuple(mismatches))


@dataclass(frozen=True, slots=True)
class SlippageSample:
    symbol: str
    sim_price: float
    live_price: float
    deviation: float


@dataclass(frozen=True, slots=True)
class SlippageReport:
    samples: tuple[SlippageSample, ...]
    mean_deviation: float
    max_deviation: float

    def explained_by(self, model_rate: float) -> bool:
        """差异是否落在滑点模型的解释范围内。"""
        return self.max_deviation <= model_rate


def compare_sim_vs_live(
    sim_fills: Sequence[Fill], live_fills: Sequence[Fill]
) -> SlippageReport:
    live_by_id = {f.order_id: f for f in live_fills}
    samples: list[SlippageSample] = []

    for sim in sim_fills:
        live = live_by_id.get(sim.order_id)
        if live is None or sim.price <= 0:
            continue
        samples.append(
            SlippageSample(
                symbol=str(sim.symbol),
                sim_price=sim.price,
                live_price=live.price,
                deviation=abs(live.price / sim.price - 1.0),
            )
        )

    if not samples:
        return SlippageReport((), 0.0, 0.0)

    deviations = [s.deviation for s in samples]
    return SlippageReport(tuple(samples), sum(deviations) / len(deviations), max(deviations))
