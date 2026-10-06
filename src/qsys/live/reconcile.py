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


def is_cash_equivalent(symbol: str) -> bool:
    """国债逆回购算现金等价物，不是持仓。

    深市 131xxx、沪市 204xxx。它们是闲置资金生息的常规手段，不由策略买卖，
    却会出现在券商持仓里——当成持仓比对就会永远判不一致
    （2026-09-28 维护者买了 7 天逆回购，对账当场判失败）。
    """
    code = symbol.split(".")[0]
    return code.startswith("131") or code.startswith("204")


#: 账户层市值与逐笔之和的允许偏差（元）。只用来判「读数是否完整」，不参与金额计算。
_MARKET_VALUE_TOLERANCE = 0.01


def unusable_market_values(remote: AccountSnapshot) -> frozenset[str]:
    """哪些持仓的市值不能用来算钱。

    两条判据，都不靠猜：

    1. **缺字段**就是没取到。
    2. **有量却报 0**，默认也算没取到——某些 QMT 版本对国债逆回购就这么报，
       照收等于把那笔钱当凭空消失。**例外**：逐笔市值之和与券商账户层直取的
       `market_value` 吻合时，说明逐笔读数是完整的，其中的 0 是券商自己的账。

    第 2 条的例外来自生产实测（2026-10-06）：`131801.SZ` 报 10 万、`131990.SZ`
    报 0，两者之和正好等于账户层的 10 万，而账户总共只入金 10 万——按面值算
    两笔会是 20 万，所以那个 0 是已结算残留行的真实市值。缺字段时做不了这个
    核对（和算不出来），所以第 1 条优先，零值一并不可信。

    例外还要求账户层市值**为正**：两个 0 相等不是佐证，是没有证据。有量的持仓
    市值为 0 本身就不合常理，只有当券商自己给出一个正的账户市值、并且逐笔恰好
    加到那个数时，这个 0 才有依据。
    """
    zeros = frozenset(
        symbol for symbol, p in remote.positions.items()
        if p.market_value is not None and p.total_qty > 0 and p.market_value == 0
    )
    missing = frozenset(
        symbol for symbol, p in remote.positions.items() if p.market_value is None
    )
    if missing:
        return missing | zeros
    total = sum(p.market_value for p in remote.positions.values())
    corroborated = (remote.market_value > 0
                    and abs(total - remote.market_value) <= _MARKET_VALUE_TOLERANCE)
    return frozenset() if corroborated else zeros


@dataclass(frozen=True, slots=True)
class PositionMismatch:
    symbol: str
    local_qty: int
    remote_qty: int


@dataclass(frozen=True, slots=True)
class PositionReconResult:
    is_consistent: bool
    #: 本地与可比现金的差额；现金等价物缺市值时为 None（判不了，不是 0）
    cash_diff: float | None
    mismatches: tuple[PositionMismatch, ...]
    #: 券商没给市值的现金等价物，它们让现金差无法判定
    unvalued_cash_equivalents: tuple[str, ...] = ()
    #: 现金差是否参与了判定。影子模式下账户不是核心独占的，现金只记不判
    cash_decided: bool = True

    def summary(self) -> str:
        if self.is_consistent:
            # 「一致」不能把判不了咽掉：影子模式下现金差不参与判定，结论就是一致，
            # 可缺市值这件事必须留在台账里，否则生产里唯一在跑的模式会静默。
            if self.unvalued_cash_equivalents:
                return ("reconcile: consistent（现金差判不了："
                        f"{len(self.unvalued_cash_equivalents)} 个现金等价物缺市值 "
                        f"{', '.join(self.unvalued_cash_equivalents)}；"
                        "现金差不参与判定：账户非核心独占）")
            if not self.cash_decided and self.cash_diff:
                return (f"reconcile: consistent（现金差 {self.cash_diff:,.2f} "
                        "不参与判定：账户非核心独占）")
            return "reconcile: consistent"
        if self.cash_diff is None:
            head = ("reconcile: INCONSISTENT (cash diff 判不了："
                    f"{len(self.unvalued_cash_equivalents)} 个现金等价物缺市值 "
                    f"{', '.join(self.unvalued_cash_equivalents)})")
        else:
            head = f"reconcile: INCONSISTENT (cash diff {self.cash_diff:,.2f})"
        lines = [head]
        for m in self.mismatches:
            lines.append(f"  {m.symbol}: local={m.local_qty} remote={m.remote_qty}")
        return "\n".join(lines)


def reconcile_positions(
    local: Portfolio, remote: AccountSnapshot, *, cash_tolerance: float = 1.0,
    cash_decides: bool = True,
) -> PositionReconResult:
    """`cash_decides=False`：现金差照记，但不参与一致性判定。

    影子模式用它。那个账户维护者自己还在用——转账、买国债逆回购、到期回款、
    利息入账都是正当操作，而影子核心一张单都发不出去，拿现金判它只会让红灯
    长亮（2026-09-28 转入 5 万、逆回购到期利息约 29 元就都会超容差）。
    实盘相反：核心是账户唯一写入方，现金差必须参与判定。
    """
    # 两侧必须同一套口径：本地账本也可能带着逆回购（`LiveStore.initialize`
    # 按券商快照建本地持仓），只过滤远端会反过来报「本地有、远端没有」。
    local_qty = {
        str(s): p.total_qty for s, p in local.positions.items()
        if not is_cash_equivalent(str(s))
    }
    remote_qty = {
        s: p.total_qty for s, p in remote.positions.items()
        if not is_cash_equivalent(s)
    }

    mismatches = [
        PositionMismatch(symbol, local_qty.get(symbol, 0), remote_qty.get(symbol, 0))
        for symbol in sorted(set(local_qty) | set(remote_qty))
        if local_qty.get(symbol, 0) != remote_qty.get(symbol, 0)
    ]
    # 现金等价物占用的资金仍是可比现金的一部分：只把它从持仓里剔除、不计回现金，
    # 现金差就恒等于逆回购金额（2026-09-28 起生产每天判不一致的真因）。
    # 市值一律直取券商快照；缺一个就判不了，不按 0 也不按 qty×avg_cost 反推。
    equivalents = {s: p for s, p in remote.positions.items() if is_cash_equivalent(s)}
    unusable = unusable_market_values(remote)
    unvalued = tuple(sorted(s for s in equivalents if s in unusable))
    if unvalued:
        cash_diff = None
        cash_ok = False
    else:
        parked = sum(p.market_value for p in equivalents.values())
        cash_diff = abs(local.cash - (remote.cash + parked))
        cash_ok = cash_diff <= cash_tolerance
    consistent = not mismatches and (cash_ok or not cash_decides)

    if not consistent:
        log.error("reconcile.inconsistent", cash_diff=cash_diff,
                  mismatches=len(mismatches), unvalued_cash_equivalents=len(unvalued))
    elif unvalued:
        # 判为一致但现金差判不了：不报错，但不能不出声。
        log.warning("reconcile.cash_undeterminable", unvalued_cash_equivalents=unvalued)

    return PositionReconResult(consistent, cash_diff, tuple(mismatches), unvalued,
                               cash_decided=cash_decides)


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
