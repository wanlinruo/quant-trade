# 一期 1-5：吸收 bridge 的执行段安全机制 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有 `qmt-bridge` 中经真实 QMT 环境验证过的六项执行段安全机制移植进 qsys，并补上 qsys 影子模式不做虚拟撮合的短板。产出：qsys 成为**单一系统**，不再需要与另一个进程对接。

**Architecture:** 移植的是**逻辑**而非进程。`protected_limit_price` 独立成 `live/pricing.py`（它算的是委托限价，不是滑点估计，两者不可混同）；持仓归属是**数量**判定归 `Constraint`，外部委托是**是否**判定归 `RiskRule`；写保护做成显式的 `TradingMode` 三态而非布尔标志。bridge 保持原样继续跑它的 shadow，作为交叉对照。

**Tech Stack:** Python 3.11+ · uv · pytest · structlog

**Spec:** `docs/superpowers/specs/2026-09-06-personal-quant-system-design.md`（补强 §5.3 约束、§6.2 风控闸门与故障矩阵、§8.1 影子模式）

## 移植来源与去向

| `qmt-bridge` 中的机制 | 移植到 qsys | 为什么值得移植 |
|---|---|---|
| `planner.protected_price()` | `live/pricing.py` | 对手价 + 保护带 + tick 对齐 + 行情时效，qsys 只有理想化滑点模型 |
| `plan_intent` 的 `NOT_STRATEGY_POSITION` / `EXTERNAL_POSITION_COLLISION` | `core/constraints.PositionOwnershipConstraint` | 同账户上手动持仓与他策略持仓不可见不可碰 |
| `trader._foreign_orders()` | `live/risk.ForeignOrderRule` | 账户上有非本系统的未结委托时停手，不与人抢 |
| `trader.single_instance()` | `agent/locking.py` | 同账户双进程互斥 |
| `config` 的 mode/orders_enabled 三重校验 + `protected_account_ids` | `live/safety.py` | 写保护做成类型而非布尔 |
| `qmt_gateway.quotes()` | `agent/xtquant_adapter.py` | tick 订阅、涨跌停、tick_size、停牌判定 |

**额外补强（qsys 自身短板）**：`ShadowBroker` 目前只记录意图、永不成交，影子组合永远不动。本计划让它用实时行情做虚拟撮合，与 bridge 的影子账本行为对齐。

## Global Constraints

- Python **3.11+**
- **不改动 `C:\qmt-bridge` 的任何文件**。它继续跑自己的 shadow，作为交叉对照
- `core/` 不得出现具体市场常量；移植进来的 `PositionOwnershipConstraint` 必须市场中立
- **写保护是类型不是布尔**：`TradingMode` 三态，`LIVE` 之外一律不可能下单
- 单元测试**不得导入 xtquant、不得连接 Redis、不得触网**
- 移植的每一项都要有独立测试，且测试要覆盖 bridge 里对应的**错误分支**（`STALE_QUOTE` / `SUSPENDED` / `LIMIT_UP` / `NO_OPPONENT` / `T1_UNAVAILABLE` 等）
- 环境隔离：所有命令前置
  `unset VIRTUAL_ENV; export PATH="$HOME/.local/bin:$(echo "$PATH" | tr ':' '\n' | grep -v 'project-002/.venv' | paste -sd: -)"`

---

## File Structure

```
src/qsys/live/
  pricing.py        Quote / QuoteError / protected_limit_price
  safety.py         TradingMode / SafetyPolicy
  risk.py           ← 追加 ForeignOrderRule
  shadow_broker.py  ← 改造：用实时行情做虚拟撮合
src/qsys/core/
  constraints.py    ← 追加 PositionOwnershipConstraint，ConstraintContext 增加 broker_positions
src/qsys/agent/
  locking.py        single_instance 账户锁
  adapter.py        ← BrokerAdapter 协议增加 query_open_orders / quotes
  xtquant_adapter.py ← 补齐行情订阅与解析
  execution_agent.py ← 集成安全机制
tests/live/test_pricing.py test_safety.py
tests/core/test_position_ownership.py
tests/agent/test_locking.py
tests/test_shadow_dryrun_e2e.py
```

---

## Task 1: 实时盘口定价

`protected_limit_price` 算的是**委托限价**，不是滑点估计。两者放同一个抽象里会概念混淆：滑点模型回答「这单大概成交在哪」，本模块回答「我该挂什么价」。

**Files:**
- Create: `src/qsys/live/pricing.py`
- Test: `tests/live/test_pricing.py`

**Interfaces:**
- Consumes: `Side` / `to_cents`（1-2a）
- Produces: `Quote(ask1, bid1, last, timestamp, tick_size=0.01, suspended=False, limit_up=False, limit_down=False)`；`QuoteError(code, message)`；`protected_limit_price(quote, side, now, *, protection=0.005, max_staleness_seconds=3.0) -> float`

- [ ] **Step 1: 写失败测试**

Create `tests/live/test_pricing.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from qsys.core.trading import Side
from qsys.live.pricing import Quote, QuoteError, protected_limit_price

TZ = timezone(timedelta(hours=8))
NOW = datetime(2024, 1, 3, 10, 0, tzinfo=TZ)


def quote(**kw) -> Quote:
    base = dict(ask1=10.02, bid1=10.00, last=10.01, timestamp=NOW, tick_size=0.01)
    return Quote(**{**base, **kw})


def test_buy_uses_ask_plus_protection():
    # 10.02 * 1.005 = 10.0701 -> 买入向下取整到 tick -> 10.07
    assert protected_limit_price(quote(), Side.BUY, NOW) == 10.07


def test_sell_uses_bid_minus_protection():
    # 10.00 * 0.995 = 9.95 -> 卖出向上取整到 tick -> 9.95
    assert protected_limit_price(quote(), Side.SELL, NOW) == 9.95


def test_buy_rounds_down_to_tick():
    """买入向下取整：宁可挂低一点买不到，不可挂高了多付。"""
    price = protected_limit_price(quote(ask1=10.029), Side.BUY, NOW, protection=0.0)
    assert price == 10.02


def test_sell_rounds_up_to_tick():
    """卖出向上取整：宁可挂高一点卖不掉，不可挂低了少收。"""
    price = protected_limit_price(quote(bid1=10.021), Side.SELL, NOW, protection=0.0)
    assert price == 10.03


def test_custom_tick_size_is_honored():
    price = protected_limit_price(quote(ask1=10.007, tick_size=0.001), Side.BUY, NOW,
                                  protection=0.0)
    assert price == 10.007


def test_protection_is_configurable():
    assert protected_limit_price(quote(ask1=10.00), Side.BUY, NOW,
                                 protection=0.01) == 10.10


def test_stale_quote_is_rejected():
    old = NOW - timedelta(seconds=5)
    with pytest.raises(QuoteError, match="STALE_QUOTE"):
        protected_limit_price(quote(timestamp=old), Side.BUY, NOW)


def test_future_quote_is_rejected():
    """来自未来的行情同样不可信。"""
    future = NOW + timedelta(seconds=10)
    with pytest.raises(QuoteError, match="STALE_QUOTE"):
        protected_limit_price(quote(timestamp=future), Side.BUY, NOW)


def test_staleness_window_is_configurable():
    old = NOW - timedelta(seconds=5)
    assert protected_limit_price(quote(timestamp=old), Side.BUY, NOW,
                                 max_staleness_seconds=10) == 10.07


def test_naive_timestamp_is_rejected():
    """无时区的时间戳不可比较，必须拒绝。"""
    with pytest.raises(QuoteError, match="STALE_QUOTE"):
        protected_limit_price(quote(timestamp=datetime(2024, 1, 3, 10, 0)), Side.BUY, NOW)


def test_suspended_is_rejected():
    with pytest.raises(QuoteError, match="SUSPENDED"):
        protected_limit_price(quote(suspended=True), Side.BUY, NOW)


def test_limit_up_blocks_buy():
    with pytest.raises(QuoteError, match="LIMIT_UP"):
        protected_limit_price(quote(limit_up=True), Side.BUY, NOW)


def test_limit_up_allows_sell():
    assert protected_limit_price(quote(limit_up=True), Side.SELL, NOW) == 9.95


def test_limit_down_blocks_sell():
    with pytest.raises(QuoteError, match="LIMIT_DOWN"):
        protected_limit_price(quote(limit_down=True), Side.SELL, NOW)


def test_limit_down_allows_buy():
    assert protected_limit_price(quote(limit_down=True), Side.BUY, NOW) == 10.07


def test_missing_opponent_price_is_rejected():
    with pytest.raises(QuoteError, match="NO_OPPONENT"):
        protected_limit_price(quote(ask1=0.0), Side.BUY, NOW)


def test_sell_with_no_bid_is_rejected():
    with pytest.raises(QuoteError, match="NO_OPPONENT"):
        protected_limit_price(quote(bid1=0.0), Side.SELL, NOW)


def test_price_never_falls_below_one_tick():
    price = protected_limit_price(quote(bid1=0.01), Side.SELL, NOW, protection=0.9)
    assert price >= 0.01


def test_error_carries_a_machine_readable_code():
    with pytest.raises(QuoteError) as exc:
        protected_limit_price(quote(suspended=True), Side.BUY, NOW)
    assert exc.value.code == "SUSPENDED"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/live/test_pricing.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.live.pricing'`

- [ ] **Step 3: 实现 `src/qsys/live/pricing.py`**

```python
"""实时盘口定价。

算的是**委托限价**，不是滑点估计——两者不可混同：
  滑点模型（backtest/slippage.py）回答「这单大概成交在哪」，用于回测
  本模块回答「我该挂什么价」，用于实盘下单

取对手价（买看 ask1、卖看 bid1）加保护带，再按 tick 对齐：
买入向下取整（宁可买不到，不可多付），卖出向上取整（宁可卖不掉，不可少收）。

移植自 qmt-bridge 的 planner.protected_price()，该逻辑已在真实 QMT 环境验证。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from qsys.core.trading import Side

_MIN_TICK = 0.01
_EPS = 1e-10


class QuoteError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class Quote:
    ask1: float
    bid1: float
    last: float
    timestamp: datetime
    tick_size: float = 0.01
    suspended: bool = False
    limit_up: bool = False
    limit_down: bool = False


def protected_limit_price(
    quote: Quote,
    side: Side,
    now: datetime,
    *,
    protection: float = 0.005,
    max_staleness_seconds: float = 3.0,
) -> float:
    """返回带保护带、按 tick 对齐的委托限价。不可成交时抛 QuoteError。"""
    stamp = quote.timestamp
    if stamp is None or stamp.tzinfo is None:
        raise QuoteError("STALE_QUOTE", "行情时间戳缺失或无时区")
    if abs((now - stamp).total_seconds()) > max_staleness_seconds:
        raise QuoteError("STALE_QUOTE", f"行情陈旧或来自未来: {stamp.isoformat()}")

    if quote.suspended:
        raise QuoteError("SUSPENDED", "停牌")
    if side is Side.BUY and quote.limit_up:
        raise QuoteError("LIMIT_UP", "涨停，买单排不到")
    if side is Side.SELL and quote.limit_down:
        raise QuoteError("LIMIT_DOWN", "跌停，卖单排不掉")

    opponent = quote.ask1 if side is Side.BUY else quote.bid1
    if opponent <= 0:
        raise QuoteError("NO_OPPONENT", "无有效对手价")

    tick = quote.tick_size if quote.tick_size > 0 else _MIN_TICK
    raw = opponent * (1.0 + protection if side is Side.BUY else 1.0 - protection)
    # 买入向下取整、卖出向上取整——两个方向都对自己不利，是保守的
    units = math.floor(raw / tick + _EPS) if side is Side.BUY else math.ceil(raw / tick - _EPS)
    return max(tick, round(units * tick, 4))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/live/test_pricing.py -v`
Expected: PASS —— 18 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/live/pricing.py tests/live/test_pricing.py
git commit -m "feat(live): 实时盘口定价（移植自 qmt-bridge 的 protected_price）"
```

---

## Task 2: 持仓归属约束

同一账户上可能同时有：手工持仓、别的策略的持仓、本系统的持仓。**本系统只能碰自己的。**

- 卖出：不在自己账本里 → 拒绝（`NOT_STRATEGY_POSITION`）
- 买入：券商显示有持仓但自己账本没有 → 拒绝（`EXTERNAL_POSITION_COLLISION`）
- 卖出数量还要受券商实际可卖量约束（`T1_UNAVAILABLE`）

**Files:**
- Modify: `src/qsys/core/constraints.py`
- Test: `tests/core/test_position_ownership.py`

**Interfaces:**
- Consumes: `Constraint` / `Verdict` / `ConstraintContext`（1-2a）
- Produces: `ConstraintContext` 增加可选字段 `broker_positions: Mapping[Symbol, int] | None = None`（券商上报的**总量**）与 `broker_available: Mapping[Symbol, int] | None = None`（券商上报的**可卖量**）；`PositionOwnershipConstraint()`（`name = "position_ownership"`）

- [ ] **Step 1: 写失败测试**

Create `tests/core/test_position_ownership.py`:

```python
from datetime import datetime

from qsys.core.constraints import ConstraintContext, PositionOwnershipConstraint
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, OrderIntent, Side
from qsys.core.types import Market, Symbol

CMB = Symbol("600036", Market.SH)
T = datetime(2024, 1, 3, 10, 0)
RULE = PositionOwnershipConstraint()


def ctx(*, own: int = 0, settled: bool = True, broker: dict | None = None,
        broker_avail: dict | None = None) -> ConstraintContext:
    p = Portfolio(1_000_000.0)
    if own:
        p.apply_fill(Fill("seed", CMB, Side.BUY, own, 10.0, 0.0, T))
        if settled:
            p.unfreeze()
    return ConstraintContext(
        portfolio=p, reference_price=10.0,
        broker_positions=broker, broker_available=broker_avail,
    )


def test_buy_passes_when_broker_shows_nothing():
    assert RULE.apply(OrderIntent(CMB, Side.BUY, 100), ctx()).allowed_qty == 100


def test_buy_passes_when_broker_position_is_ours():
    c = ctx(own=500, broker={CMB: 500})
    assert RULE.apply(OrderIntent(CMB, Side.BUY, 100), c).allowed_qty == 100


def test_buy_is_blocked_by_an_unknown_broker_position():
    """券商有持仓但自己账本没有 —— 可能是手工买的或别的策略的，不碰。"""
    c = ctx(own=0, broker={CMB: 800})
    v = RULE.apply(OrderIntent(CMB, Side.BUY, 100), c)
    assert v.is_rejected
    assert "external position" in v.reason


def test_sell_is_blocked_when_not_in_our_ledger():
    c = ctx(own=0, broker={CMB: 800}, broker_avail={CMB: 800})
    v = RULE.apply(OrderIntent(CMB, Side.SELL, 100), c)
    assert v.is_rejected
    assert "not a strategy position" in v.reason


def test_sell_passes_within_our_ledger():
    c = ctx(own=500, broker={CMB: 500}, broker_avail={CMB: 500})
    assert RULE.apply(OrderIntent(CMB, Side.SELL, 500), c).allowed_qty == 500


def test_sell_is_clamped_by_broker_available():
    """自己账本认为可卖 500，券商只让卖 300 —— 以券商为准。"""
    c = ctx(own=500, broker={CMB: 500}, broker_avail={CMB: 300})
    v = RULE.apply(OrderIntent(CMB, Side.SELL, 500), c)
    assert v.allowed_qty == 300
    assert "broker available" in v.reason


def test_sell_is_rejected_when_broker_has_none_available():
    c = ctx(own=500, broker={CMB: 500}, broker_avail={CMB: 0})
    v = RULE.apply(OrderIntent(CMB, Side.SELL, 100), c)
    assert v.is_rejected
    assert "T1" in v.reason or "available" in v.reason


def test_without_broker_data_the_rule_is_inert():
    """未接入券商数据时不做判定，交由其它约束处理——不能凭空拒单。"""
    c = ctx(own=500)
    assert RULE.apply(OrderIntent(CMB, Side.SELL, 500), c).allowed_qty == 500
    assert RULE.apply(OrderIntent(CMB, Side.BUY, 100), c).allowed_qty == 100


def test_broker_position_of_zero_is_not_a_collision():
    c = ctx(own=0, broker={CMB: 0})
    assert RULE.apply(OrderIntent(CMB, Side.BUY, 100), c).allowed_qty == 100


def test_rule_name():
    assert RULE.name == "position_ownership"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/core/test_position_ownership.py -v`
Expected: FAIL —— `ImportError: cannot import name 'PositionOwnershipConstraint'`

- [ ] **Step 3: 修改 `src/qsys/core/constraints.py`**

`ConstraintContext` 追加两个可选字段：

```python
@dataclass(frozen=True, slots=True)
class ConstraintContext:
    portfolio: Portfolio
    reference_price: float
    prev_close: float | None = None
    board: str = "main"
    is_suspended: bool = False
    # 券商上报的持仓。未接入时为 None，此时 PositionOwnershipConstraint 不做判定。
    broker_positions: Mapping[Symbol, int] | None = None
    broker_available: Mapping[Symbol, int] | None = None
```

顶部 import 补上 `from qsys.core.types import Symbol`，并追加：

```python
class PositionOwnershipConstraint(Constraint):
    """只碰自己账本里的持仓。

    同一账户上可能同时存在手工持仓、其它策略的持仓与本系统的持仓。
    卖出只允许自己账本里的；买入时若券商显示有本账本不认识的持仓则拒绝——
    那可能是手工买的或别的策略的，动它会打乱对方。

    未接入券商数据（broker_positions is None）时本约束不做判定：
    凭空拒单比放行更危险。

    移植自 qmt-bridge 的 EXTERNAL_POSITION_COLLISION / NOT_STRATEGY_POSITION。
    """

    name = "position_ownership"

    def apply(self, intent: OrderIntent, ctx: ConstraintContext) -> Verdict:
        if ctx.broker_positions is None:
            return Verdict(intent.qty)

        broker_qty = ctx.broker_positions.get(intent.symbol, 0)
        own_qty = ctx.portfolio.position(intent.symbol).total_qty

        if intent.side is Side.BUY:
            if broker_qty > 0 and own_qty <= 0:
                return Verdict(
                    0,
                    f"external position of {broker_qty} in {intent.symbol}; "
                    f"not ours to touch",
                )
            return Verdict(intent.qty)

        if own_qty <= 0:
            return Verdict(0, f"{intent.symbol} is not a strategy position")

        if ctx.broker_available is None:
            return Verdict(intent.qty)
        available = ctx.broker_available.get(intent.symbol, 0)
        if available <= 0:
            return Verdict(0, f"broker reports no T1-available shares in {intent.symbol}")
        if intent.qty > available:
            return Verdict(available, f"clamped to broker available {available}")
        return Verdict(intent.qty)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/core/test_position_ownership.py tests/core/ -v`
Expected: PASS —— 10 passed，且既有约束测试无回归

- [ ] **Step 5: 提交**

```bash
git add src/qsys/core/constraints.py tests/core/test_position_ownership.py
git commit -m "feat(core): 持仓归属约束（移植自 qmt-bridge 的持仓保护）"
```

---

## Task 3: 外部委托风控规则

账户上出现**不是本系统下的、还未终结的委托**时，停手。不与人抢。

**Files:**
- Modify: `src/qsys/live/risk.py`
- Test: `tests/live/test_risk.py`（追加）

**Interfaces:**
- Consumes: `RiskRule` / `RiskVerdict`（1-3）
- Produces: `OpenOrder(order_id, order_remark, symbol, status)`；`ForeignOrderRule(open_orders_provider, known_refs_provider, *, terminal_statuses=...)`（`name = "foreign_order"`）

- [ ] **Step 1: 追加失败测试**

Append to `tests/live/test_risk.py`:

```python


# --- ForeignOrderRule（移植自 qmt-bridge 的 _foreign_orders）---

from qsys.live.risk import ForeignOrderRule, OpenOrder


def order(oid="B1", remark="r1", status="submitted"):
    return OpenOrder(order_id=oid, order_remark=remark, symbol="600036.SH", status=status)


def test_no_open_orders_allows():
    rule = ForeignOrderRule(lambda: [], lambda: set())
    assert rule.check(BUY, ctx()).allowed is True


def test_our_own_open_order_allows():
    rule = ForeignOrderRule(lambda: [order()], lambda: {("B1", "r1")})
    assert rule.check(BUY, ctx()).allowed is True


def test_foreign_open_order_blocks():
    """账户上有别人下的未结委托 —— 停手，不与人抢。"""
    rule = ForeignOrderRule(lambda: [order(oid="X9", remark="")], lambda: {("B1", "r1")})
    verdict = rule.check(BUY, ctx())
    assert verdict.allowed is False
    assert "foreign" in verdict.reason


def test_foreign_order_blocks_sells_too():
    rule = ForeignOrderRule(lambda: [order(oid="X9", remark="")], lambda: set())
    assert rule.check(SELL, ctx()).allowed is False


def test_terminal_orders_are_ignored():
    """已成交/已撤/已废的委托不算未结。"""
    rule = ForeignOrderRule(
        lambda: [order(oid="X9", remark="", status="filled"),
                 order(oid="X8", remark="", status="cancelled")],
        lambda: set(),
    )
    assert rule.check(BUY, ctx()).allowed is True


def test_numeric_status_codes_are_recognised():
    """QMT 用数字状态码，54/56 分别是已撤/废单。"""
    rule = ForeignOrderRule(lambda: [order(oid="X9", remark="", status="56")],
                            lambda: set())
    assert rule.check(BUY, ctx()).allowed is True


def test_status_matching_is_case_insensitive():
    rule = ForeignOrderRule(lambda: [order(oid="X9", remark="", status="FILLED")],
                            lambda: set())
    assert rule.check(BUY, ctx()).allowed is True


def test_provider_failure_blocks_rather_than_allows():
    """查不到委托时必须停手——查不到不等于没有。"""

    def broken():
        raise RuntimeError("QMT 查询失败")

    rule = ForeignOrderRule(broken, lambda: set())
    verdict = rule.check(BUY, ctx())
    assert verdict.allowed is False
    assert "QMT 查询失败" in verdict.reason


def test_reason_names_the_offending_order():
    rule = ForeignOrderRule(lambda: [order(oid="X9", remark="")], lambda: set())
    assert "X9" in rule.check(BUY, ctx()).reason


def test_rule_name():
    assert ForeignOrderRule(lambda: [], lambda: set()).name == "foreign_order"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/live/test_risk.py -v`
Expected: FAIL —— `ImportError: cannot import name 'ForeignOrderRule'`

- [ ] **Step 3: 追加实现到 `src/qsys/live/risk.py`**

顶部 import 补上 `from collections.abc import Callable, Iterable`，然后追加：

```python
# QMT 的终态状态码：filled/cancelled/rejected 以及数字码 54（已撤）56（废单）
TERMINAL_ORDER_STATUSES = frozenset({"filled", "cancelled", "rejected", "54", "56"})


@dataclass(frozen=True, slots=True)
class OpenOrder:
    order_id: str
    order_remark: str
    symbol: str
    status: str


class ForeignOrderRule(RiskRule):
    """账户上有非本系统的未结委托时停手。

    同一账户可能被手工操作或别的策略使用。发现不认识的未结委托时，
    继续下单会让双方的持仓推演都失真，而对账的前提正是本地推演可信。

    查询失败时**拒绝**而非放行：查不到不等于没有。

    移植自 qmt-bridge 的 trader._foreign_orders()。
    """

    name = "foreign_order"

    def __init__(
        self,
        open_orders_provider: Callable[[], Iterable[OpenOrder]],
        known_refs_provider: Callable[[], set[tuple[str, str]]],
        *,
        terminal_statuses: frozenset[str] = TERMINAL_ORDER_STATUSES,
    ) -> None:
        self._orders = open_orders_provider
        self._known = known_refs_provider
        self._terminal = terminal_statuses

    def check(self, intent: OrderIntent, ctx: RiskContext, **kwargs) -> RiskVerdict:
        try:
            orders = list(self._orders())
            known = self._known()
        except Exception as exc:  # 查不到不等于没有——停手
            return RiskVerdict(False, f"无法查询账户委托: {exc}")

        for order in orders:
            if str(order.status).lower() in self._terminal:
                continue
            if (str(order.order_id), str(order.order_remark or "")) in known:
                continue
            return RiskVerdict(
                False,
                f"foreign open order {order.order_id} on {order.symbol}; "
                f"refusing to act alongside it",
            )
        return RiskVerdict(True)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/live/test_risk.py -v`
Expected: PASS —— 37 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/live/risk.py tests/live/test_risk.py
git commit -m "feat(live): 外部委托风控规则（移植自 qmt-bridge）"
```

---

## Task 4: 交易模式与写保护

bridge 用 `mode` 字符串 + `orders_enabled` 布尔 + 三处运行时校验实现写保护。qsys 做成**类型**：`TradingMode` 三态，`LIVE` 之外在类型层面就不可能下单。

**Files:**
- Create: `src/qsys/live/safety.py`
- Test: `tests/live/test_safety.py`

**Interfaces:**
- Consumes: 无
- Produces: `TradingMode` (StrEnum: DRY_RUN/SHADOW/LIVE)、`SafetyError`、`SafetyPolicy(mode, *, orders_enabled=False, allowed_account_ids=())` 带 `may_place_orders: bool`、`assert_account(account_id)`、`assert_may_place()`；类方法 `SafetyPolicy.from_mapping(dict) -> SafetyPolicy`

- [ ] **Step 1: 写失败测试**

Create `tests/live/test_safety.py`:

```python
import pytest

from qsys.live.safety import SafetyError, SafetyPolicy, TradingMode


def test_dry_run_cannot_place_orders():
    assert SafetyPolicy(TradingMode.DRY_RUN).may_place_orders is False


def test_shadow_cannot_place_orders():
    assert SafetyPolicy(TradingMode.SHADOW).may_place_orders is False


def test_live_without_the_flag_cannot_place_orders():
    """live 模式本身还不够，必须再显式打开 orders_enabled。"""
    assert SafetyPolicy(TradingMode.LIVE, orders_enabled=False).may_place_orders is False


def test_live_with_the_flag_can_place_orders():
    assert SafetyPolicy(TradingMode.LIVE, orders_enabled=True).may_place_orders is True


def test_orders_enabled_outside_live_is_rejected_at_construction():
    """非 live 却打开 orders_enabled —— 配置写错了，立刻报错而不是等到下单时。"""
    with pytest.raises(SafetyError, match="orders_enabled"):
        SafetyPolicy(TradingMode.SHADOW, orders_enabled=True)


def test_assert_may_place_raises_in_shadow():
    with pytest.raises(SafetyError, match="write-protected"):
        SafetyPolicy(TradingMode.SHADOW).assert_may_place()


def test_assert_may_place_passes_in_armed_live():
    SafetyPolicy(TradingMode.LIVE, orders_enabled=True).assert_may_place()


def test_account_whitelist_accepts_a_listed_account():
    SafetyPolicy(TradingMode.SHADOW, allowed_account_ids=("803010036",)).assert_account("803010036")


def test_account_whitelist_rejects_an_unlisted_account():
    policy = SafetyPolicy(TradingMode.SHADOW, allowed_account_ids=("803010036",))
    with pytest.raises(SafetyError, match="not in the allowed"):
        policy.assert_account("999999999")


def test_empty_whitelist_rejects_everything_in_live():
    """live 下没配白名单是危险的默认值，必须显式列出账户。"""
    policy = SafetyPolicy(TradingMode.LIVE, orders_enabled=True)
    with pytest.raises(SafetyError, match="allowed"):
        policy.assert_account("803010036")


def test_empty_whitelist_is_permissive_outside_live():
    SafetyPolicy(TradingMode.DRY_RUN).assert_account("anything")


def test_account_ids_are_compared_as_strings():
    policy = SafetyPolicy(TradingMode.SHADOW, allowed_account_ids=("803010036",))
    policy.assert_account(803010036)


def test_from_mapping_parses_a_config_dict():
    policy = SafetyPolicy.from_mapping({
        "mode": "shadow", "orders_enabled": False,
        "allowed_account_ids": ["803010036"],
    })
    assert policy.mode is TradingMode.SHADOW
    assert policy.may_place_orders is False


def test_from_mapping_rejects_an_unknown_mode():
    with pytest.raises(SafetyError, match="mode"):
        SafetyPolicy.from_mapping({"mode": "yolo"})


def test_from_mapping_defaults_to_dry_run():
    assert SafetyPolicy.from_mapping({}).mode is TradingMode.DRY_RUN


def test_policy_is_frozen():
    with pytest.raises(Exception):
        SafetyPolicy(TradingMode.SHADOW).mode = TradingMode.LIVE
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/live/test_safety.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.live.safety'`

- [ ] **Step 3: 实现 `src/qsys/live/safety.py`**

```python
"""交易模式与写保护。

bridge 用 mode 字符串 + orders_enabled 布尔 + 三处运行时校验实现写保护。
这里做成**类型**：LIVE 之外，may_place_orders 恒为 False，
配置写错（非 live 却开了 orders_enabled）在构造时就报错，而不是等到下单那一刻。

live 下必须显式列出允许的账户白名单——空白名单在 live 下是危险的默认值。

移植自 qmt-bridge 的 config 三重校验与 protected_account_ids。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum


class SafetyError(RuntimeError):
    pass


class TradingMode(StrEnum):
    DRY_RUN = "dry_run"  # 只校验，不接券商
    SHADOW = "shadow"  # 接券商只读，虚拟撮合
    LIVE = "live"  # 真实下单


@dataclass(frozen=True, slots=True)
class SafetyPolicy:
    mode: TradingMode
    orders_enabled: bool = False
    allowed_account_ids: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.orders_enabled and self.mode is not TradingMode.LIVE:
            raise SafetyError(
                f"orders_enabled 只能在 live 模式下开启，当前 mode={self.mode.value}"
            )

    @property
    def may_place_orders(self) -> bool:
        return self.mode is TradingMode.LIVE and self.orders_enabled

    def assert_may_place(self) -> None:
        if not self.may_place_orders:
            raise SafetyError(
                f"write-protected: mode={self.mode.value} "
                f"orders_enabled={self.orders_enabled}"
            )

    def assert_account(self, account_id: object) -> None:
        if self.mode is not TradingMode.LIVE and not self.allowed_account_ids:
            return
        allowed = {str(a) for a in self.allowed_account_ids}
        if str(account_id) not in allowed:
            raise SafetyError(
                f"account {account_id} is not in the allowed list {sorted(allowed)}"
            )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> SafetyPolicy:
        value = str(raw.get("mode", "dry_run"))
        try:
            mode = TradingMode(value)
        except ValueError as exc:
            raise SafetyError(f"未知 mode: {value!r}") from exc
        accounts: Sequence[object] = raw.get("allowed_account_ids", ()) or ()
        return cls(
            mode=mode,
            orders_enabled=bool(raw.get("orders_enabled", False)),
            allowed_account_ids=tuple(str(a) for a in accounts),
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/live/test_safety.py -v`
Expected: PASS —— 16 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/live/safety.py tests/live/test_safety.py
git commit -m "feat(live): 交易模式与写保护（写保护做成类型而非布尔）"
```

---

## Task 5: 账户级单实例锁

同一账户上跑两个执行进程会双双失真。用文件锁互斥，Windows 走 `msvcrt`，POSIX 走 `fcntl`。

**Files:**
- Create: `src/qsys/agent/locking.py`
- Test: `tests/agent/test_locking.py`

**Interfaces:**
- Consumes: 无
- Produces: `LockHeldError`；`single_instance(path)` 上下文管理器

- [ ] **Step 1: 写失败测试**

Create `tests/agent/test_locking.py`:

```python
import multiprocessing
import time

import pytest

from qsys.agent.locking import LockHeldError, single_instance


def test_lock_can_be_acquired(tmp_path):
    with single_instance(tmp_path / "acct.lock"):
        pass


def test_lock_creates_parent_directories(tmp_path):
    target = tmp_path / "deep" / "nested" / "acct.lock"
    with single_instance(target):
        assert target.parent.exists()


def test_lock_is_released_on_exit(tmp_path):
    path = tmp_path / "acct.lock"
    with single_instance(path):
        pass
    with single_instance(path):
        pass


def test_lock_is_released_on_exception(tmp_path):
    path = tmp_path / "acct.lock"
    with pytest.raises(ValueError):
        with single_instance(path):
            raise ValueError("boom")
    with single_instance(path):
        pass


def _hold(path, ready, done):
    from qsys.agent.locking import single_instance

    with single_instance(path):
        ready.set()
        done.wait(timeout=10)


def test_second_holder_is_rejected(tmp_path):
    """同一账户不允许两个执行进程。"""
    path = str(tmp_path / "acct.lock")
    ready, done = multiprocessing.Event(), multiprocessing.Event()
    proc = multiprocessing.Process(target=_hold, args=(path, ready, done))
    proc.start()
    try:
        assert ready.wait(timeout=10), "持锁进程未就绪"
        with pytest.raises(LockHeldError, match="already held"):
            with single_instance(path):
                pass
    finally:
        done.set()
        proc.join(timeout=10)


def test_lock_is_reusable_after_the_holder_exits(tmp_path):
    path = str(tmp_path / "acct.lock")
    ready, done = multiprocessing.Event(), multiprocessing.Event()
    proc = multiprocessing.Process(target=_hold, args=(path, ready, done))
    proc.start()
    ready.wait(timeout=10)
    done.set()
    proc.join(timeout=10)
    time.sleep(0.2)
    with single_instance(path):
        pass


def test_error_message_names_the_lock_path(tmp_path):
    path = str(tmp_path / "acct.lock")
    ready, done = multiprocessing.Event(), multiprocessing.Event()
    proc = multiprocessing.Process(target=_hold, args=(path, ready, done))
    proc.start()
    try:
        ready.wait(timeout=10)
        with pytest.raises(LockHeldError) as exc:
            with single_instance(path):
                pass
        assert "acct.lock" in str(exc.value)
    finally:
        done.set()
        proc.join(timeout=10)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/agent/test_locking.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.agent.locking'`

- [ ] **Step 3: 实现 `src/qsys/agent/locking.py`**

```python
"""账户级单实例锁。

同一账户上跑两个执行进程会让双方的持仓推演都失真，且会互相把对方的
委托认成外部委托。用文件锁互斥：Windows 走 msvcrt，POSIX 走 fcntl。

移植自 qmt-bridge 的 trader.single_instance()。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class LockHeldError(RuntimeError):
    pass


@contextmanager
def single_instance(path: str | Path) -> Iterator[None]:
    """独占持有账户锁；已被他人持有时抛 LockHeldError。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = open(target, "a+")
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise LockHeldError(f"account lock already held: {target}") from exc

    try:
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/agent/test_locking.py -v`
Expected: PASS —— 7 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/agent/locking.py tests/agent/test_locking.py
git commit -m "feat(agent): 账户级单实例锁（移植自 qmt-bridge）"
```

---

## Task 6: 券商适配器扩展

`BrokerAdapter` 协议增加行情与未结委托查询——这两项是前面几个安全机制的数据来源。

**Files:**
- Modify: `src/qsys/agent/adapter.py`
- Test: `tests/agent/test_adapter.py`

**Interfaces:**
- Consumes: `Quote`（Task 1）、`OpenOrder`（Task 3）
- Produces: `BrokerAdapter` 协议增加 `query_open_orders() -> list[OpenOrder]`、`quotes(symbols) -> dict[str, Quote]`；`FakeAdapter` 相应增加 `open_orders` / `quote_map` 构造参数与 `known_refs`

- [ ] **Step 1: 写失败测试**

Create `tests/agent/test_adapter.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from qsys.agent.adapter import FakeAdapter
from qsys.live.pricing import Quote
from qsys.live.risk import OpenOrder

TZ = timezone(timedelta(hours=8))
NOW = datetime(2024, 1, 3, 10, 0, tzinfo=TZ)


def test_query_open_orders_defaults_to_empty():
    assert FakeAdapter().query_open_orders() == []


def test_query_open_orders_returns_configured_orders():
    orders = [OpenOrder("B1", "r1", "600036.SH", "submitted")]
    assert FakeAdapter(open_orders=orders).query_open_orders() == orders


def test_quotes_defaults_to_empty():
    assert FakeAdapter().quotes(["600036.SH"]) == {}


def test_quotes_returns_configured_quotes():
    q = Quote(ask1=10.02, bid1=10.0, last=10.01, timestamp=NOW)
    got = FakeAdapter(quote_map={"600036.SH": q}).quotes(["600036.SH"])
    assert got["600036.SH"].ask1 == 10.02


def test_quotes_only_returns_requested_symbols():
    q = Quote(ask1=1.0, bid1=1.0, last=1.0, timestamp=NOW)
    adapter = FakeAdapter(quote_map={"600036.SH": q, "000001.SZ": q})
    assert set(adapter.quotes(["600036.SH"])) == {"600036.SH"}


def test_quotes_records_requests():
    adapter = FakeAdapter()
    adapter.quotes(["600036.SH", "000001.SZ"])
    assert adapter.quote_requests == [["600036.SH", "000001.SZ"]]


def test_known_refs_tracks_placed_orders():
    """下过的单要能被 ForeignOrderRule 认出来，否则会自己拦自己。"""
    adapter = FakeAdapter()
    adapter.place_order("600036.SH", "buy", 100, "limit", 10.0)
    assert len(adapter.known_refs()) == 1


def test_known_refs_is_empty_initially():
    assert FakeAdapter().known_refs() == set()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/agent/test_adapter.py -v`
Expected: FAIL —— `TypeError: FakeAdapter.__init__() got an unexpected keyword argument 'open_orders'`

- [ ] **Step 3: 修改 `src/qsys/agent/adapter.py`**

`BrokerAdapter` 协议追加两个方法：

```python
    def query_open_orders(self) -> list[OpenOrder]:
        """返回账户上全部未结委托（含非本系统下的）。"""

    def quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        """返回实时盘口。缺失的标的可以不出现在返回值里。"""
```

顶部 import 补上：

```python
from collections.abc import Sequence

from qsys.live.pricing import Quote
from qsys.live.risk import OpenOrder
```

`FakeAdapter` 构造函数追加 `open_orders` 与 `quote_map`，并新增三个方法：

```python
    def __init__(
        self,
        *,
        cash: float = 100_000.0,
        positions: dict[str, tuple[int, int, float]] | None = None,
        fail_with: str | None = None,
        open_orders: list[OpenOrder] | None = None,
        quote_map: dict[str, Quote] | None = None,
    ) -> None:
        ...  # 既有字段保持不变
        self._open_orders = list(open_orders or [])
        self._quotes = dict(quote_map or {})
        self.quote_requests: list[list[str]] = []
        self._refs: set[tuple[str, str]] = set()

    def query_open_orders(self) -> list[OpenOrder]:
        return list(self._open_orders)

    def quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        self.quote_requests.append(list(symbols))
        return {s: self._quotes[s] for s in symbols if s in self._quotes}

    def known_refs(self) -> set[tuple[str, str]]:
        return set(self._refs)
```

并在 `place_order` 成功分支末尾登记引用：

```python
        broker_order_id = f"B{self._seq}"
        self._refs.add((broker_order_id, ""))
        return broker_order_id
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/agent/ -v`
Expected: PASS —— 8 + 既有 11 项通过

- [ ] **Step 5: 提交**

```bash
git add src/qsys/agent/adapter.py tests/agent/test_adapter.py
git commit -m "feat(agent): 券商适配器增加行情与未结委托查询"
```

---

## Task 7: ShadowBroker 虚拟撮合

**这是 qsys 自身的短板，不是移植。** 现在的 `ShadowBroker` 只记录意图、永不成交，影子组合永远不动——影子跑一周也看不出什么。bridge 的 shadow 会用实时行情算出委托价并更新虚拟账本，qsys 必须对齐。

**Files:**
- Modify: `src/qsys/live/shadow_broker.py`
- Test: `tests/live/test_shadow_broker.py`（追加）

**Interfaces:**
- Consumes: `protected_limit_price` / `Quote` / `QuoteError`（Task 1）、`FeeModel`（1-2b）
- Produces: `ShadowBroker(..., quotes_provider=None, fees=None, protection=0.005)`；提供 quotes_provider 时 `on_market_data()` 触发虚拟撮合并经 `poll_fills()` 返回

- [ ] **Step 1: 追加失败测试**

Append to `tests/live/test_shadow_broker.py`:

```python


# --- 虚拟撮合（Task 7）---

from datetime import timedelta, timezone

from qsys.backtest.fees import AShareFeeModel
from qsys.live.pricing import Quote

TZ = timezone(timedelta(hours=8))
QT = datetime(2024, 1, 3, 10, 0, tzinfo=TZ)


def quote(**kw) -> Quote:
    base = dict(ask1=10.02, bid1=10.00, last=10.01, timestamp=QT, tick_size=0.01)
    return Quote(**{**base, **kw})


def make_matching(tmp_path, quotes=None, now=QT):
    transport = MemoryTransport()
    ks = KillSwitch(transport, local_path=tmp_path / "K")
    gate = default_ashare_gate(ks, CAL)
    portfolio = Portfolio(1_000_000.0)
    broker = ShadowBroker(
        transport, STREAMS, risk_gate=gate,
        risk_context_factory=lambda: RiskContext(
            portfolio=portfolio, now=T, reference_price=10.0, day_start_value=1e6
        ),
        quotes_provider=lambda syms: quotes if quotes is not None else {},
        fees=AShareFeeModel(),
        clock=lambda: now,
    )
    return broker, portfolio


def test_shadow_fills_against_live_quotes(tmp_path):
    """影子模式必须成交到虚拟账本，否则影子组合永远不动。"""
    b, _ = make_matching(tmp_path, quotes={"600036.SH": quote()})
    b.submit(order())
    b.on_market_data(None)
    fills = b.poll_fills()
    assert len(fills) == 1
    assert fills[0].price == 10.07  # ask1 10.02 * 1.005 -> 向下取整到 tick


def test_shadow_fill_charges_fees(tmp_path):
    b, _ = make_matching(tmp_path, quotes={"600036.SH": quote()})
    b.submit(order())
    b.on_market_data(None)
    assert b.poll_fills()[0].fee > 0


def test_shadow_still_writes_nothing_to_the_order_stream(tmp_path):
    """撮合是虚拟的，真钱依然一分不动。"""
    transport = MemoryTransport()
    ks = KillSwitch(transport, local_path=tmp_path / "K")
    portfolio = Portfolio(1_000_000.0)
    b = ShadowBroker(
        transport, STREAMS, risk_gate=default_ashare_gate(ks, CAL),
        risk_context_factory=lambda: RiskContext(
            portfolio=portfolio, now=T, reference_price=10.0, day_start_value=1e6
        ),
        quotes_provider=lambda syms: {"600036.SH": quote()},
        fees=AShareFeeModel(), clock=lambda: QT,
    )
    b.submit(order())
    b.on_market_data(None)
    b.poll_fills()
    assert transport.all_messages(STREAMS.orders) == []


def test_suspended_quote_rejects_instead_of_filling(tmp_path):
    b, _ = make_matching(tmp_path, quotes={"600036.SH": quote(suspended=True)})
    b.submit(order())
    b.on_market_data(None)
    assert b.poll_fills() == ()
    assert "SUSPENDED" in b.poll_rejects()[0].reason


def test_limit_up_rejects_a_buy(tmp_path):
    b, _ = make_matching(tmp_path, quotes={"600036.SH": quote(limit_up=True)})
    b.submit(order())
    b.on_market_data(None)
    assert "LIMIT_UP" in b.poll_rejects()[0].reason


def test_missing_quote_rejects(tmp_path):
    b, _ = make_matching(tmp_path, quotes={})
    b.submit(order())
    b.on_market_data(None)
    assert b.poll_fills() == ()
    assert "no quote" in b.poll_rejects()[0].reason


def test_stale_quote_rejects(tmp_path):
    stale = quote(timestamp=QT - timedelta(seconds=30))
    b, _ = make_matching(tmp_path, quotes={"600036.SH": stale})
    b.submit(order())
    b.on_market_data(None)
    assert "STALE_QUOTE" in b.poll_rejects()[0].reason


def test_pending_is_cleared_after_matching(tmp_path):
    b, _ = make_matching(tmp_path, quotes={"600036.SH": quote()})
    b.submit(order())
    b.on_market_data(None)
    assert b.pending() == ()


def test_without_a_quotes_provider_it_only_records(tmp_path):
    """未接行情时退回到「只记录意图」，保持向后兼容。"""
    b, _ = make(tmp_path)
    b.submit(order())
    b.on_market_data(None)
    assert b.poll_fills() == ()
    assert len(b.intents) == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/live/test_shadow_broker.py -v`
Expected: FAIL —— `TypeError: ShadowBroker.__init__() got an unexpected keyword argument 'quotes_provider'`

- [ ] **Step 3: 修改 `src/qsys/live/shadow_broker.py`**

构造函数追加三个可选参数并保存：

```python
        quotes_provider: Callable[[Sequence[str]], Mapping[str, Quote]] | None = None,
        fees: FeeModel | None = None,
        protection: float = 0.005,
        clock: Callable[[], datetime] | None = None,
```

```python
        self._quotes = quotes_provider
        self._fees = fees or ZeroFeeModel()
        self._protection = protection
        self._clock = clock or (lambda: datetime.now(_TZ))
        self._fills: list[Fill] = []
```

`poll_fills` 改为排空 `self._fills`，并把 `on_market_data` 实现为虚拟撮合：

```python
    def on_market_data(self, snapshot: MarketSnapshot | None = None) -> None:
        """用实时行情做虚拟撮合。

        未提供 quotes_provider 时退回到「只记录意图」，保持向后兼容。
        真钱依然一分不动：撮合只更新虚拟账本，绝不写 orders 流。
        """
        if self._quotes is None or not self._intents:
            return

        now = self._clock()
        queued, self._intents = self._intents, []
        symbols = sorted({str(o.symbol) for o in queued})
        try:
            quotes = dict(self._quotes(symbols))
        except Exception as exc:
            for order in queued:
                self._reject(order, f"quote lookup failed: {exc}", now)
            return

        for order in queued:
            quote = quotes.get(str(order.symbol))
            if quote is None:
                self._reject(order, f"no quote for {order.symbol}", now)
                continue
            try:
                price = protected_limit_price(
                    quote, order.side, now, protection=self._protection
                )
            except QuoteError as exc:
                self._reject(order, str(exc), now)
                continue
            gross = to_cents(price * order.qty)
            self._fills.append(
                Fill(order.order_id, order.symbol, order.side, order.qty, price,
                     self._fees.fee_for(order.side, gross), now)
            )

    def _reject(self, order: Order, reason: str, at: datetime) -> None:
        self._rejects.append(
            Reject(order.order_id, order.symbol, order.side, order.qty, reason, at)
        )
```

需要的 import：`datetime` / `timezone` / `timedelta`、`Mapping` / `Sequence`、`Fill` / `to_cents`、`FeeModel` / `ZeroFeeModel`、`Quote` / `QuoteError` / `protected_limit_price`，并定义 `_TZ = timezone(timedelta(hours=8))`。

- [ ] **Step 4: 给 ShadowBroker 接上约束流水线**

`LiveBroker`（计划 1-4 Task 8）已支持 `constraints` / `constraint_context_factory`。
`ShadowBroker` 必须有**完全一致**的接入方式——否则影子期验证的约束行为
与实盘不同，影子就失去了意义。

构造函数追加同名的两个可选参数并保存，在 `submit()` 的风控通过之后、
记录意图之前施加约束（与 `LiveBroker.submit` 逐行对应）：

```python
        qty = order.qty
        if self._constraints is not None and self._constraint_ctx is not None:
            result = self._constraints.apply(intent, self._constraint_ctx())
            if result.is_rejected:
                self._rejects.append(
                    Reject(order.order_id, order.symbol, order.side, order.qty,
                           result.binding_reason, order.created_at)
                )
                return
            qty = result.allowed_qty
        order = replace(order, qty=qty)
```

需要 `from dataclasses import replace`。

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/live/test_shadow_broker.py -v`
Expected: PASS —— 18 passed

- [ ] **Step 6: 提交**

```bash
git add src/qsys/live/shadow_broker.py tests/live/test_shadow_broker.py
git commit -m "feat(live): 影子模式虚拟撮合与约束接入"
```

---

## Task 8: 真实 xtquant 行情与委托查询

补齐 `XtQuantAdapter` 的 `quotes()` 与 `query_open_orders()`。**仅 Windows 可导入，单元测试不碰它。**

**Files:**
- Modify: `src/qsys/agent/xtquant_adapter.py`
- Test: `tests/agent/test_xtquant_parsing.py`（只测纯函数解析逻辑，不导入 xtquant）

**Interfaces:**
- Consumes: `Quote`（Task 1）、`OpenOrder`（Task 3）
- Produces: 纯函数 `parse_tick(code, tick, detail) -> Quote`、`parse_open_order(raw) -> OpenOrder`（模块级，可脱离 xtquant 测试）；`XtQuantAdapter.quotes()` / `query_open_orders()` / `known_refs()`

- [ ] **Step 1: 写失败测试**

Create `tests/agent/test_xtquant_parsing.py`:

```python
"""只测纯解析函数。不导入 xtquant——单元测试不得依赖 Windows 环境。"""

from datetime import datetime, timedelta, timezone

from qsys.agent.xtquant_adapter import parse_open_order, parse_tick

TZ = timezone(timedelta(hours=8))


def tick(**kw) -> dict:
    base = dict(askPrice=[10.02, 10.03], bidPrice=[10.00, 9.99], lastPrice=10.01,
                timetag="20240103 10:00:00", upperLimit=11.0, lowerLimit=9.0)
    return {**base, **kw}


def test_parses_top_of_book():
    q = parse_tick("600036.SH", tick(), {})
    assert q.ask1 == 10.02
    assert q.bid1 == 10.00
    assert q.last == 10.01


def test_parses_string_timetag_with_timezone():
    q = parse_tick("600036.SH", tick(), {})
    assert q.timestamp == datetime(2024, 1, 3, 10, 0, tzinfo=TZ)


def test_parses_millisecond_timetag():
    q = parse_tick("600036.SH", tick(timetag=1704247200000), {})
    assert q.timestamp.tzinfo is not None


def test_tick_size_comes_from_instrument_detail():
    q = parse_tick("600036.SH", tick(), {"PriceTick": 0.001})
    assert q.tick_size == 0.001


def test_tick_size_defaults_to_one_cent():
    assert parse_tick("600036.SH", tick(), {}).tick_size == 0.01


def test_empty_book_means_suspended():
    q = parse_tick("600036.SH", tick(askPrice=[], bidPrice=[]), {})
    assert q.suspended is True


def test_non_empty_book_is_not_suspended():
    assert parse_tick("600036.SH", tick(), {}).suspended is False


def test_limit_up_detected_from_last_price():
    q = parse_tick("600036.SH", tick(lastPrice=11.0), {})
    assert q.limit_up is True
    assert q.limit_down is False


def test_limit_down_detected_from_last_price():
    q = parse_tick("600036.SH", tick(lastPrice=9.0), {})
    assert q.limit_down is True


def test_limits_fall_back_to_instrument_detail():
    q = parse_tick("600036.SH", tick(upperLimit=0, lastPrice=11.0),
                   {"UpStopPrice": 11.0})
    assert q.limit_up is True


def test_missing_limits_mean_no_limit_flags():
    q = parse_tick("600036.SH", tick(upperLimit=0, lowerLimit=0), {})
    assert q.limit_up is False and q.limit_down is False


def test_open_order_parsing_from_object():
    class Raw:
        order_id = 12345
        order_remark = "abc"
        stock_code = "600036.SH"
        order_status = 50

    o = parse_open_order(Raw())
    assert o.order_id == "12345"
    assert o.order_remark == "abc"
    assert o.symbol == "600036.SH"
    assert o.status == "50"


def test_open_order_parsing_from_dict():
    o = parse_open_order({"order_id": 1, "order_remark": None,
                          "stock_code": "000001.SZ", "order_status": "filled"})
    assert o.order_id == "1"
    assert o.order_remark == ""
    assert o.status == "filled"


def test_open_order_tolerates_missing_fields():
    o = parse_open_order({})
    assert o.order_id == ""
    assert o.symbol == ""
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/agent/test_xtquant_parsing.py -v`
Expected: FAIL —— `ImportError: cannot import name 'parse_tick'`

- [ ] **Step 3: 修改 `src/qsys/agent/xtquant_adapter.py`**

在文件顶部（`XtQuantAdapter` 之前）加入两个纯函数，它们**不导入 xtquant**，因此可在任何平台测试：

```python
from datetime import datetime, timedelta, timezone

from qsys.live.pricing import Quote
from qsys.live.risk import OpenOrder

_TZ = timezone(timedelta(hours=8))
_TIMETAG_FORMATS = ("%Y%m%d %H:%M:%S.%f", "%Y%m%d %H:%M:%S")


def _field(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _parse_timetag(raw) -> datetime:
    if isinstance(raw, str):
        for fmt in _TIMETAG_FORMATS:
            try:
                return datetime.strptime(raw, fmt).replace(tzinfo=_TZ)
            except ValueError:
                continue
        return datetime.fromtimestamp(0, _TZ)
    return datetime.fromtimestamp(float(raw or 0) / 1000.0, _TZ)


def parse_tick(code: str, tick: dict, detail: dict) -> Quote:
    """把 xtdata 的 tick + instrument detail 解析成 Quote。纯函数，可脱离 xtquant 测试。"""
    asks = tick.get("askPrice") or []
    bids = tick.get("bidPrice") or []
    last = float(tick.get("lastPrice") or 0)
    upper = float(tick.get("upperLimit") or detail.get("UpStopPrice") or 0)
    lower = float(tick.get("lowerLimit") or detail.get("DownStopPrice") or 0)
    return Quote(
        ask1=float(asks[0]) if asks else 0.0,
        bid1=float(bids[0]) if bids else 0.0,
        last=last,
        timestamp=_parse_timetag(tick.get("timetag")),
        tick_size=float(detail.get("PriceTick") or 0.01),
        suspended=not asks and not bids,
        limit_up=bool(upper and last >= upper),
        limit_down=bool(lower and last <= lower),
    )


def parse_open_order(raw) -> OpenOrder:
    """把 xttrader 的委托对象解析成 OpenOrder。纯函数，可脱离 xtquant 测试。"""
    return OpenOrder(
        order_id=str(_field(raw, "order_id", "") or ""),
        order_remark=str(_field(raw, "order_remark", "") or ""),
        symbol=str(_field(raw, "stock_code", "") or ""),
        status=str(_field(raw, "order_status", "") or ""),
    )
```

`XtQuantAdapter` 追加三个方法（订阅逻辑移植自 `qmt_gateway.quotes()`）：

```python
    def quotes(self, symbols):
        from xtquant import xtdata

        fresh = [s for s in symbols if s not in self._subscribed]
        for code in fresh:
            seq = xtdata.subscribe_quote(code, period="tick", count=-1)
            if seq is None or int(seq) < 0:
                raise RuntimeError(f"行情订阅失败: {code}")
            self._subscribed.add(code)
        if fresh:
            # miniQMT 订阅后第一笔 tick 是异步推送的
            import time

            time.sleep(1)
        ticks = xtdata.get_full_tick(list(symbols)) or {}
        return {
            code: parse_tick(code, ticks.get(code) or {},
                             xtdata.get_instrument_detail(code) or {})
            for code in symbols
        }

    def query_open_orders(self):
        return [parse_open_order(o) for o in self._trader.query_stock_orders(self._account, False)]

    def known_refs(self):
        return set(self._refs)
```

并在 `__init__` 中初始化 `self._subscribed: set[str] = set()` 与 `self._refs: set[tuple[str, str]] = set()`；`place_order` 成功后 `self._refs.add((str(order_id), remark))`（remark 由 `secrets.token_urlsafe(16)` 生成并传给 `order_stock`）。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/agent/test_xtquant_parsing.py -v`
Expected: PASS —— 15 passed

- [ ] **Step 5: 确认单元测试仍未导入 xtquant**

Run: `uv run pytest -q 2>&1 | grep -i xtquant || echo "未导入 xtquant"`
Expected: `未导入 xtquant`

- [ ] **Step 6: 提交**

```bash
git add src/qsys/agent/xtquant_adapter.py tests/agent/test_xtquant_parsing.py
git commit -m "feat(agent): xtquant 行情与委托解析（纯函数可测）"
```

---

## Task 9: A 股实盘约束与风控组装

把新增的约束与规则装进 A 股预设，形成实盘专用的组合。

**Files:**
- Modify: `src/qsys/markets/ashare.py`
- Modify: `src/qsys/live/risk.py`
- Test: `tests/markets/test_ashare.py`（追加）

**Interfaces:**
- Consumes: Task 2 / 3 的产出
- Produces: `ashare_live_pipeline(*, fee_buffer=0.001) -> ConstraintPipeline`（在 pre_trade 基础上加入 `position_ownership`）；`live_ashare_gate(kill_switch, calendar, adapter, **kw) -> RiskGate`（在默认闸门基础上加入 `foreign_order`）

- [ ] **Step 1: 追加失败测试**

Append to `tests/markets/test_ashare.py`:

```python


# --- 实盘专用组合（Task 9）---

from qsys.markets.ashare import ashare_live_pipeline


def test_live_pipeline_adds_position_ownership():
    assert "position_ownership" in ashare_live_pipeline().names


def test_live_pipeline_checks_ownership_before_clamping_quantity():
    """归属判定要在数量夹取之前——先确认能不能碰，再算能碰多少。"""
    names = ashare_live_pipeline().names
    assert names.index("position_ownership") < names.index("cash")
    assert names[-1] == "lot_size"


def test_live_pipeline_keeps_the_pre_trade_rules():
    assert set(ashare_pre_trade_pipeline().names) <= set(ashare_live_pipeline().names)


def test_live_pipeline_blocks_selling_a_foreign_position():
    p = Portfolio(100_000.0)
    ctx = ConstraintContext(
        portfolio=p, reference_price=10.0,
        broker_positions={CMB: 500}, broker_available={CMB: 500},
    )
    assert ashare_live_pipeline().apply(OrderIntent(CMB, Side.SELL, 500), ctx).is_rejected
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/markets/test_ashare.py -v`
Expected: FAIL —— `ImportError: cannot import name 'ashare_live_pipeline'`

- [ ] **Step 3: 追加实现**

在 `src/qsys/markets/ashare.py` 追加：

```python
def ashare_live_pipeline(*, fee_buffer: float = 0.001) -> ConstraintPipeline:
    """实盘约束流水线 = 下单前约束 + 持仓归属。

    归属判定排在数量夹取之前：先确认这只票能不能碰，再算能碰多少。
    """
    return ConstraintPipeline(
        [
            PositionOwnershipConstraint(),
            NoShortConstraint(),
            SettlementConstraint(),
            CashConstraint(fee_buffer=fee_buffer),
            LotSizeConstraint(lot=LOT_SIZE),
        ]
    )
```

并在 import 区补上 `PositionOwnershipConstraint`。

在 `src/qsys/live/risk.py` 追加：

```python
def live_ashare_gate(
    kill_switch: KillSwitch,
    calendar: TradingCalendar,
    adapter,
    *,
    max_position_weight: float = 0.20,
    max_daily_turnover: float = 1.0,
    max_daily_loss: float = 0.05,
    max_price_deviation: float = 0.10,
) -> RiskGate:
    """实盘风控闸门 = 默认闸门 + 外部委托检测。

    外部委托检测排在 kill switch 之后、其余之前：停机指令优先，
    其次就是「账户上有别人在操作」这种必须立刻停手的情形。

    显式列出全部规则而非改造 default_ashare_gate 的返回值——
    伸手进另一个对象的私有属性会让两处的顺序意图脱钩。
    """
    return RiskGate(
        [
            KillSwitchRule(kill_switch),
            ForeignOrderRule(adapter.query_open_orders, adapter.known_refs),
            TradingHoursRule(calendar),
            PriceDeviationRule(max_price_deviation),
            DailyLossCircuitBreaker(max_daily_loss),
            MaxPositionWeightRule(max_position_weight),
            MaxDailyTurnoverRule(max_daily_turnover),
        ]
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/markets/ tests/live/ -v`
Expected: PASS —— 全部通过

- [ ] **Step 5: 提交**

```bash
git add src/qsys/markets/ashare.py src/qsys/live/risk.py tests/markets/test_ashare.py
git commit -m "feat(markets): A 股实盘约束与风控组装"
```

---

## Task 10: 影子演练端到端

把全部安全机制串起来，模拟一个完整交易日的影子运行。

**Files:**
- Test: `tests/test_shadow_dryrun_e2e.py`

**Interfaces:**
- Consumes: Task 1–9 的全部产出
- Produces: 无新增生产代码

- [ ] **Step 1: 写端到端测试**

Create `tests/test_shadow_dryrun_e2e.py`:

```python
"""影子演练：全部安全机制串起来跑一个交易日。

验证移植进来的六项机制在组合使用时确实生效。
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from qsys.agent.adapter import FakeAdapter
from qsys.agent.locking import LockHeldError, single_instance
from qsys.backtest.fees import AShareFeeModel
from qsys.core.calendar import AShareCalendar
from qsys.core.constraints import ConstraintContext
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Order, OrderType, Side
from qsys.core.types import Market, Symbol
from qsys.live.config import StreamConfig
from qsys.live.kill_switch import KillSwitch
from qsys.live.pricing import Quote
from qsys.live.risk import OpenOrder, RiskContext, live_ashare_gate
from qsys.live.safety import SafetyError, SafetyPolicy, TradingMode
from qsys.live.shadow_broker import ShadowBroker
from qsys.live.transport import MemoryTransport
from qsys.markets.ashare import ashare_live_pipeline

TZ = timezone(timedelta(hours=8))
CMB = Symbol("600036", Market.SH)
PAB = Symbol("000001", Market.SZ)
D = date(2024, 1, 3)
CAL = AShareCalendar([D])
NOW = datetime(2024, 1, 3, 10, 0, tzinfo=TZ)
STREAMS = StreamConfig()


def quote(**kw) -> Quote:
    base = dict(ask1=10.02, bid1=10.00, last=10.01, timestamp=NOW, tick_size=0.01)
    return Quote(**{**base, **kw})


def rig(tmp_path, *, adapter=None, quotes=None, own=0):
    transport = MemoryTransport()
    portfolio = Portfolio(1_000_000.0)
    if own:
        from qsys.core.trading import Fill

        portfolio.apply_fill(Fill("seed", CMB, Side.BUY, own, 10.0, 0.0, NOW))
        portfolio.unfreeze()
    ks = KillSwitch(transport, local_path=tmp_path / "KILL")
    adapter = adapter or FakeAdapter(cash=1_000_000.0)
    gate = live_ashare_gate(ks, CAL, adapter)
    broker = ShadowBroker(
        transport, STREAMS, risk_gate=gate,
        risk_context_factory=lambda: RiskContext(
            portfolio=portfolio, now=NOW, reference_price=10.0, day_start_value=1e6
        ),
        quotes_provider=lambda syms: quotes if quotes is not None else {},
        fees=AShareFeeModel(), clock=lambda: NOW,
    )
    return broker, portfolio, transport, ks, adapter


def order(side=Side.BUY, qty=1000, oid="o1", symbol=CMB) -> Order:
    return Order(oid, symbol, side, qty, OrderType.MARKET, None, NOW)


def test_clean_buy_fills_into_the_virtual_ledger(tmp_path):
    b, portfolio, _, _, _ = rig(tmp_path, quotes={"600036.SH": quote()})
    b.submit(order())
    b.on_market_data(None)
    fills = b.poll_fills()
    assert len(fills) == 1
    portfolio.apply_fill(fills[0])
    assert portfolio.position(CMB).total_qty == 1000


def test_shadow_never_touches_the_order_stream(tmp_path):
    b, _, transport, _, _ = rig(tmp_path, quotes={"600036.SH": quote()})
    b.submit(order())
    b.on_market_data(None)
    b.poll_fills()
    assert transport.all_messages(STREAMS.orders) == []


def test_foreign_order_stops_everything(tmp_path):
    """账户上出现别人的未结委托 -> 全部停手。"""
    adapter = FakeAdapter(open_orders=[OpenOrder("X9", "", "600036.SH", "submitted")])
    b, _, _, _, _ = rig(tmp_path, adapter=adapter, quotes={"600036.SH": quote()})
    b.submit(order())
    assert b.intents == ()
    assert "foreign" in b.poll_rejects()[0].reason


def test_kill_switch_outranks_everything(tmp_path):
    b, _, _, ks, _ = rig(tmp_path, quotes={"600036.SH": quote()})
    ks.engage("停机演练")
    b.submit(order())
    assert "kill_switch" in b.poll_rejects()[0].reason


def test_suspended_stock_is_rejected_at_matching(tmp_path):
    b, _, _, _, _ = rig(tmp_path, quotes={"600036.SH": quote(suspended=True)})
    b.submit(order())
    b.on_market_data(None)
    assert b.poll_fills() == ()
    assert "SUSPENDED" in b.poll_rejects()[0].reason


def test_limit_up_blocks_buy_but_allows_sell(tmp_path):
    b, portfolio, _, _, _ = rig(tmp_path, quotes={"600036.SH": quote(limit_up=True)},
                                own=1000)
    b.submit(order(Side.BUY, 100, "buy"))
    b.submit(order(Side.SELL, 1000, "sell"))
    b.on_market_data(None)
    fills = {f.order_id for f in b.poll_fills()}
    assert "sell" in fills
    assert "buy" not in fills


def test_position_ownership_blocks_selling_someone_elses_holding(tmp_path):
    """券商显示有持仓，但不在我们的账本里 —— 不许卖。"""
    transport = MemoryTransport()
    portfolio = Portfolio(1_000_000.0)  # 账本为空
    ks = KillSwitch(transport, local_path=tmp_path / "KILL")
    adapter = FakeAdapter()
    b = ShadowBroker(
        transport, STREAMS, risk_gate=live_ashare_gate(ks, CAL, adapter),
        risk_context_factory=lambda: RiskContext(
            portfolio=portfolio, now=NOW, reference_price=10.0, day_start_value=1e6
        ),
        quotes_provider=lambda syms: {"600036.SH": quote()},
        fees=AShareFeeModel(), clock=lambda: NOW,
        constraints=ashare_live_pipeline(),
        constraint_context_factory=lambda: ConstraintContext(
            portfolio=portfolio, reference_price=10.0,
            broker_positions={CMB: 800}, broker_available={CMB: 800},
        ),
    )
    b.submit(order(Side.SELL, 500))
    assert b.intents == ()
    assert "not a strategy position" in b.poll_rejects()[0].reason


def test_account_lock_prevents_a_second_agent(tmp_path):
    path = tmp_path / "acct.lock"
    with single_instance(path):
        import multiprocessing

        def child(p, q):
            from qsys.agent.locking import LockHeldError, single_instance

            try:
                with single_instance(p):
                    q.put("acquired")
            except LockHeldError:
                q.put("blocked")

        q = multiprocessing.Queue()
        proc = multiprocessing.Process(target=child, args=(str(path), q))
        proc.start()
        proc.join(timeout=10)
        assert q.get(timeout=5) == "blocked"


def test_safety_policy_blocks_order_placement_in_shadow(tmp_path):
    policy = SafetyPolicy(TradingMode.SHADOW)
    with pytest.raises(SafetyError, match="write-protected"):
        policy.assert_may_place()


def test_safety_policy_rejects_an_unlisted_account():
    policy = SafetyPolicy(TradingMode.SHADOW, allowed_account_ids=("803010036",))
    with pytest.raises(SafetyError):
        policy.assert_account("111111111")


def test_a_full_shadow_session(tmp_path):
    """完整一天：买入成交、涨停被拒、停牌被拒，账本正确演进。"""
    quotes = {
        "600036.SH": quote(),
        "000001.SZ": quote(ask1=11.5, bid1=11.4, last=11.5, limit_up=True),
    }
    b, portfolio, transport, _, _ = rig(tmp_path, quotes=quotes)

    b.submit(order(Side.BUY, 1000, "a", CMB))
    b.submit(order(Side.BUY, 1000, "b", PAB))
    b.on_market_data(None)

    fills = b.poll_fills()
    rejects = b.poll_rejects()
    for f in fills:
        portfolio.apply_fill(f)

    assert [f.order_id for f in fills] == ["a"]
    assert any("LIMIT_UP" in r.reason for r in rejects)
    assert portfolio.position(CMB).total_qty == 1000
    assert portfolio.position(PAB).total_qty == 0
    assert transport.all_messages(STREAMS.orders) == []
```

- [ ] **Step 2: 运行测试确认通过**

Run: `uv run pytest tests/test_shadow_dryrun_e2e.py -v`
Expected: PASS —— 11 passed

- [ ] **Step 3: 运行全部测试**

Run: `uv run pytest -q`
Expected: PASS —— 前五个计划（647）+ 本计划新增项全部通过

- [ ] **Step 4: 提交**

```bash
git add tests/test_shadow_dryrun_e2e.py
git commit -m "test: 影子演练端到端（六项安全机制组合验证）"
```

---

## 完成标准

1. `uv run pytest` 全绿，前四个计划（647 项）无回归
2. **单元测试不导入 xtquant、不连 Redis、不触网** —— `grep -rn "xtquant" tests/` 只出现在 `test_xtquant_parsing.py` 的纯函数测试里且不实际导入 xtquant 包
3. **六项机制全部移植且各有测试**：盘口定价、持仓归属、外部委托、写保护、账户锁、行情解析
4. **写保护是类型**：`SafetyPolicy(SHADOW, orders_enabled=True)` 在**构造时**抛错，而非下单时
5. **影子模式会成交到虚拟账本** —— `test_clean_buy_fills_into_the_virtual_ledger` 通过，且 `orders` 流始终为空
6. **外部委托检测在查询失败时拒绝而非放行** —— 查不到不等于没有
7. **`C:\qmt-bridge` 未被改动** —— 它继续跑自己的 shadow 作为交叉对照

## 下一步

代码完成后进入 **Windows 部署与双影子并行**：

1. 在 Windows 上装 qsys（Python 3.11.9 已有；uv 走腾讯镜像；xtquant 用 `C:\qmt-bridge-private\xtquant_230825b`）
2. qsys 以 `TradingMode.SHADOW` + 独立锁文件启动，**不改 bridge 任何文件**
3. 两套 shadow 并行跑一周，每日对照两份报告
4. 差异逐项归因（定价、约束、账本），收敛后再谈 live

**live 前仍未解决的一件事**：`ExecutionAgent` 目前在下单成功后立即发布 `FillEvent`（假成交价）。xtquant 的成交回报是异步的，必须改成等回调。这条从计划 1-3 遗留至今，是 live 的前置条件。
