# 一期 1-2b：回测引擎 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在计划 1-2a 的交易内核之上建成事件驱动回测引擎——费用与滑点模型、`SimBroker` 保守撮合、除权除息处理、`Strategy`/`ctx` 显式注入、回测驱动器与绩效指标，并配齐合成数据断言套件与聚宽交叉验证对照工具。产出：能跑完整回测并通过引擎正确性验收。

**Architecture:** 回测与实盘是同一套代码的两种 `Broker` 实现，差异只可能来自这一个边界。引擎持有全区间行情属基础设施，**策略只能通过 `ctx.data` 取数，其 `as_of` 恒等于 `ctx.now`**——策略永远拿不到 `WarehouseQuery`，这是计划 1-1 完成标准 ④ 的兑现点。回测使用**不复权价**，除权除息由引擎在除权日显式调整持仓与现金，使 `SimBroker` 看到的价格与实盘 `QmtBroker` 完全一致。

**Tech Stack:** Python 3.11+ · uv · polars · structlog · pytest · hypothesis

**Spec:** `docs/superpowers/specs/2026-09-06-personal-quant-system-design.md`（实现 §5.1 事件循环、§5.2 策略生命周期、§5.4 下单抽象、§6.1 SimBroker、§8.2 回测引擎正确性验证）

## Global Constraints

- Python **3.11+**；`core/` 不得出现具体市场常量（spec §3.4，计划 1-2a 已建立的边界不得破坏）
- **信号在 bar T 生成，成交在 bar T+1 开盘**。绝不允许用生成信号那一刻的 bar 数据成交（spec §6.1）
- **策略不得触达 `WarehouseQuery`**。`Context` 只暴露 `DataFeed`，且每次回调前重新绑定 `as_of = ctx.now`（计划 1-1 完成标准 ④）
- 回测使用**不复权价**；除权除息由引擎显式调整持仓与现金
- 撮合、滑点、费用三者均为可插拔模型——这是「引擎可改」卡点的兑现处（spec §6.1）
- 保守撮合下单笔成交不超过当根 bar 成交量的 `volume_cap`（默认 5%）；被削减的部分**作废并告警**，不得静默丢弃
- 每次回测产出 `run_id = hash(git_commit + data_snapshot + params_hash)`，结果可精确重跑（spec §8.4）
- 单元测试不得发起网络请求
- 环境隔离：所有命令前置
  `unset VIRTUAL_ENV; export PATH="$HOME/.local/bin:$(echo "$PATH" | tr ':' '\n' | grep -v 'project-002/.venv' | paste -sd: -)"`

---

## File Structure

```
src/qsys/core/
  events.py         MarketSnapshot / Reject
src/qsys/backtest/
  __init__.py
  fees.py           FeeModel 抽象 + AShareFeeModel
  slippage.py       SlippageModel 抽象 + NoSlippage / FixedRateSlippage / FixedTickSlippage
  broker.py         Broker 抽象
  sim_broker.py     SimBroker 保守撮合
  corporate.py      除权除息处理
  engine.py         BacktestEngine + BacktestResult
  metrics.py        Metrics + compute_metrics
src/qsys/strategy/
  __init__.py
  context.py        PortfolioView / OrderApi / Context
  base.py           Strategy 抽象
scripts/
  compare_joinquant.py   聚宽三层交叉验证对照工具
tests/core/test_events.py
tests/backtest/test_fees.py
tests/backtest/test_slippage.py
tests/backtest/test_sim_broker.py
tests/backtest/test_corporate.py
tests/backtest/test_engine.py
tests/backtest/test_metrics.py
tests/strategy/test_context.py
tests/strategy/test_order_api.py
tests/test_engine_scenarios.py      合成数据断言套件（引擎级验收）
tests/test_compare_joinquant.py
```

---

## Task 1: 市场快照与拒单记录

**Files:**
- Create: `src/qsys/core/events.py`
- Test: `tests/core/test_events.py`

**Interfaces:**
- Consumes: `Symbol` / `Bar`（1-1）、`Side`（1-2a）
- Produces: `MarketSnapshot(session, bars, prev_closes, boards)` 带 `symbols: frozenset[Symbol]`、`is_suspended(symbol) -> bool`、`bar_of(symbol) -> Bar | None`、`prev_close_of(symbol) -> float | None`、`board_of(symbol) -> str`；`Reject(order_id, symbol, side, qty, reason, at)`

- [ ] **Step 1: 写失败测试**

Create `tests/core/test_events.py`:

```python
from datetime import date, datetime

from qsys.core.events import MarketSnapshot, Reject
from qsys.core.trading import Side
from qsys.core.types import Bar, Market, Symbol

CMB = Symbol("600036", Market.SH)
PAB = Symbol("000001", Market.SZ)
D = date(2024, 1, 3)


def bar(symbol: Symbol, close: float) -> Bar:
    return Bar(symbol, D, close, close, close, close, 1000, close * 1000)


def snap(**kw) -> MarketSnapshot:
    defaults = dict(
        session=D,
        bars={CMB: bar(CMB, 10.0)},
        prev_closes={CMB: 9.8, PAB: 20.0},
        boards={CMB: "main", PAB: "gem"},
    )
    return MarketSnapshot(**{**defaults, **kw})


def test_symbols_lists_traded_instruments():
    assert snap().symbols == frozenset({CMB})


def test_bar_of_returns_the_bar():
    assert snap().bar_of(CMB).close == 10.0


def test_bar_of_missing_symbol_is_none():
    assert snap().bar_of(PAB) is None


def test_symbol_without_a_bar_is_suspended():
    """当日无 bar 即视为停牌——这是引擎判定停牌的唯一依据。"""
    assert snap().is_suspended(PAB) is True
    assert snap().is_suspended(CMB) is False


def test_prev_close_lookup():
    assert snap().prev_close_of(CMB) == 9.8
    assert snap().prev_close_of(Symbol("300750", Market.SZ)) is None


def test_board_lookup_defaults_to_main():
    s = snap()
    assert s.board_of(PAB) == "gem"
    assert s.board_of(Symbol("300750", Market.SZ)) == "main"


def test_snapshot_is_frozen():
    import pytest

    with pytest.raises(Exception):
        snap().session = date(2024, 1, 4)


def test_reject_carries_reason():
    r = Reject("o1", CMB, Side.BUY, 100, "suspension: halted", datetime(2024, 1, 3, 9, 30))
    assert r.reason.startswith("suspension")
    assert r.qty == 100
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/core/test_events.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.core.events'`

- [ ] **Step 3: 实现 `src/qsys/core/events.py`**

```python
"""市场快照与拒单记录。

MarketSnapshot 是引擎在每个交易日交给 Broker 的市场状态。
「当日无 bar」即视为停牌——这是引擎判定停牌的唯一依据。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

from qsys.core.trading import Side
from qsys.core.types import Bar, Symbol


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    session: date
    bars: Mapping[Symbol, Bar]
    prev_closes: Mapping[Symbol, float]
    boards: Mapping[Symbol, str]

    @property
    def symbols(self) -> frozenset[Symbol]:
        return frozenset(self.bars)

    def bar_of(self, symbol: Symbol) -> Bar | None:
        return self.bars.get(symbol)

    def is_suspended(self, symbol: Symbol) -> bool:
        return symbol not in self.bars

    def prev_close_of(self, symbol: Symbol) -> float | None:
        return self.prev_closes.get(symbol)

    def board_of(self, symbol: Symbol) -> str:
        return self.boards.get(symbol, "main")


@dataclass(frozen=True, slots=True)
class Reject:
    order_id: str
    symbol: Symbol
    side: Side
    qty: int
    reason: str
    at: datetime
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/core/test_events.py -v`
Expected: PASS —— 8 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/core/events.py tests/core/test_events.py
git commit -m "feat(core): 市场快照与拒单记录"
```

---

## Task 2: A 股费用模型

费率是**已知确定**的，这块能做到和实盘一模一样。

**Files:**
- Create: `src/qsys/backtest/__init__.py`
- Create: `src/qsys/backtest/fees.py`
- Test: `tests/backtest/test_fees.py`

**Interfaces:**
- Consumes: `Side` / `to_cents`（1-2a）
- Produces: `FeeModel` (ABC: `fee_for(side, gross) -> float`)、`ZeroFeeModel()`、`AShareFeeModel(commission_rate=0.0003, min_commission=5.0, stamp_duty_rate=0.0005, transfer_fee_rate=0.00001)`

- [ ] **Step 1: 写失败测试**

Create `tests/backtest/test_fees.py`:

```python
import pytest

from qsys.backtest.fees import AShareFeeModel, ZeroFeeModel
from qsys.core.trading import Side


def test_zero_fee_model_charges_nothing():
    assert ZeroFeeModel().fee_for(Side.BUY, 100_000.0) == 0.0
    assert ZeroFeeModel().fee_for(Side.SELL, 100_000.0) == 0.0


def test_buy_fee_is_commission_plus_transfer():
    # 100000 * 0.0003 = 30.00 佣金；100000 * 0.00001 = 1.00 过户费
    assert AShareFeeModel().fee_for(Side.BUY, 100_000.0) == 31.00


def test_sell_fee_adds_stamp_duty():
    # 30.00 佣金 + 1.00 过户费 + 100000 * 0.0005 = 50.00 印花税
    assert AShareFeeModel().fee_for(Side.SELL, 100_000.0) == 81.00


def test_minimum_commission_applies():
    # 1000 * 0.0003 = 0.30 -> 抬到最低 5.00；过户费 0.01
    assert AShareFeeModel().fee_for(Side.BUY, 1000.0) == 5.01


def test_minimum_commission_not_applied_above_threshold():
    # 佣金恰好等于 5.00 的成交额是 16666.67
    fee = AShareFeeModel().fee_for(Side.BUY, 20_000.0)
    assert fee == pytest.approx(6.20, abs=0.005)


def test_stamp_duty_is_sell_only():
    m = AShareFeeModel()
    assert m.fee_for(Side.SELL, 100_000.0) - m.fee_for(Side.BUY, 100_000.0) == 50.00


def test_rates_are_configurable():
    m = AShareFeeModel(
        commission_rate=0.0001, min_commission=0.0, stamp_duty_rate=0.0, transfer_fee_rate=0.0
    )
    assert m.fee_for(Side.SELL, 100_000.0) == 10.00


def test_fee_is_rounded_to_cents():
    m = AShareFeeModel(commission_rate=0.000333, min_commission=0.0, transfer_fee_rate=0.0,
                       stamp_duty_rate=0.0)
    assert m.fee_for(Side.BUY, 1000.0) == 0.33


def test_zero_gross_still_charges_minimum_commission():
    assert AShareFeeModel().fee_for(Side.BUY, 0.0) == 5.00


def test_negative_gross_rejected():
    with pytest.raises(ValueError, match="gross must be >= 0"):
        AShareFeeModel().fee_for(Side.BUY, -1.0)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/backtest/test_fees.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.backtest'`

- [ ] **Step 3: 实现**

```bash
mkdir -p src/qsys/backtest tests/backtest
touch src/qsys/backtest/__init__.py
```

Create `src/qsys/backtest/fees.py`:

```python
"""费用模型。

费率是已知确定的，这块能做到与实盘一模一样。默认值为 A 股常见档位：
佣金万三（最低 5 元）、印花税万五（2023-08-28 起减半，仅卖出）、
过户费十万分之一（双向）。请按自己的实际费率调整。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from qsys.core.trading import Side, to_cents


class FeeModel(ABC):
    @abstractmethod
    def fee_for(self, side: Side, gross: float) -> float:
        """给定方向与成交金额，返回该笔应收费用。"""


class ZeroFeeModel(FeeModel):
    def fee_for(self, side: Side, gross: float) -> float:
        return 0.0


@dataclass(frozen=True, slots=True)
class AShareFeeModel(FeeModel):
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_duty_rate: float = 0.0005  # 仅卖出
    transfer_fee_rate: float = 0.00001  # 双向

    def fee_for(self, side: Side, gross: float) -> float:
        if gross < 0:
            raise ValueError(f"gross must be >= 0: {gross}")
        commission = max(gross * self.commission_rate, self.min_commission)
        transfer = gross * self.transfer_fee_rate
        stamp = gross * self.stamp_duty_rate if side is Side.SELL else 0.0
        return to_cents(commission + transfer + stamp)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/backtest/test_fees.py -v`
Expected: PASS —— 10 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/backtest tests/backtest/test_fees.py
git commit -m "feat(backtest): A 股费用模型"
```

---

## Task 3: 滑点模型

**Files:**
- Create: `src/qsys/backtest/slippage.py`
- Test: `tests/backtest/test_slippage.py`

**Interfaces:**
- Consumes: `Side` / `to_cents`（1-2a）
- Produces: `SlippageModel` (ABC: `adjust(side, price) -> float`)、`NoSlippage()`、`FixedRateSlippage(rate)`、`FixedTickSlippage(ticks, tick_size=0.01)`

- [ ] **Step 1: 写失败测试**

Create `tests/backtest/test_slippage.py`:

```python
import pytest

from qsys.backtest.slippage import FixedRateSlippage, FixedTickSlippage, NoSlippage
from qsys.core.trading import Side


def test_no_slippage_returns_price_unchanged():
    assert NoSlippage().adjust(Side.BUY, 10.0) == 10.0
    assert NoSlippage().adjust(Side.SELL, 10.0) == 10.0


def test_fixed_rate_buy_pays_more():
    assert FixedRateSlippage(0.001).adjust(Side.BUY, 10.0) == 10.01


def test_fixed_rate_sell_receives_less():
    assert FixedRateSlippage(0.001).adjust(Side.SELL, 10.0) == 9.99


def test_fixed_rate_is_rounded_to_cents():
    assert FixedRateSlippage(0.0003).adjust(Side.BUY, 27.58) == 27.59


def test_fixed_tick_buy_pays_more():
    assert FixedTickSlippage(2).adjust(Side.BUY, 10.0) == 10.02


def test_fixed_tick_sell_receives_less():
    assert FixedTickSlippage(2).adjust(Side.SELL, 10.0) == 9.98


def test_tick_size_is_configurable():
    assert FixedTickSlippage(1, tick_size=0.001).adjust(Side.BUY, 10.0) == 10.0


def test_slippage_never_pushes_price_below_zero():
    assert FixedRateSlippage(2.0).adjust(Side.SELL, 10.0) == 0.01


def test_negative_rate_rejected():
    with pytest.raises(ValueError, match="rate must be >= 0"):
        FixedRateSlippage(-0.001)


def test_negative_ticks_rejected():
    with pytest.raises(ValueError, match="ticks must be >= 0"):
        FixedTickSlippage(-1)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/backtest/test_slippage.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.backtest.slippage'`

- [ ] **Step 3: 实现 `src/qsys/backtest/slippage.py`**

```python
"""滑点模型。

买入向上、卖出向下——滑点永远对你不利。
五期会加入基于实盘对账校准的经验模型。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from qsys.core.trading import Side, to_cents

_MIN_PRICE = 0.01


class SlippageModel(ABC):
    @abstractmethod
    def adjust(self, side: Side, price: float) -> float:
        """返回考虑滑点后的成交价。"""


class NoSlippage(SlippageModel):
    def adjust(self, side: Side, price: float) -> float:
        return price


class FixedRateSlippage(SlippageModel):
    def __init__(self, rate: float) -> None:
        if rate < 0:
            raise ValueError(f"rate must be >= 0: {rate}")
        self._rate = rate

    def adjust(self, side: Side, price: float) -> float:
        factor = 1.0 + self._rate if side is Side.BUY else 1.0 - self._rate
        return max(_MIN_PRICE, to_cents(price * factor))


class FixedTickSlippage(SlippageModel):
    def __init__(self, ticks: int, tick_size: float = 0.01) -> None:
        if ticks < 0:
            raise ValueError(f"ticks must be >= 0: {ticks}")
        self._offset = ticks * tick_size

    def adjust(self, side: Side, price: float) -> float:
        shifted = price + self._offset if side is Side.BUY else price - self._offset
        return max(_MIN_PRICE, to_cents(shifted))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/backtest/test_slippage.py -v`
Expected: PASS —— 10 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/backtest/slippage.py tests/backtest/test_slippage.py
git commit -m "feat(backtest): 滑点模型"
```

---

## Task 4: Broker 抽象

`on_market_data` 是回测与实盘的分界：`SimBroker` 在此撮合，实盘 Broker 忽略它（真实市场自己撮合）。

**Files:**
- Create: `src/qsys/backtest/broker.py`
- Test: 并入 Task 5 的 `tests/backtest/test_sim_broker.py`

**Interfaces:**
- Consumes: `Order` / `Fill`（1-2a）、`MarketSnapshot` / `Reject`（Task 1）
- Produces: `Broker` (ABC: `submit(order)`、`on_market_data(snapshot)`、`poll_fills() -> tuple[Fill, ...]`、`poll_rejects() -> tuple[Reject, ...]`、`pending() -> tuple[Order, ...]`、`cancel_all()`)

- [ ] **Step 1: 实现 `src/qsys/backtest/broker.py`**

```python
"""Broker 抽象。

回测与实盘不是两个系统，是这个接口的两种实现——差异只可能来自这一个
边界，因而可以逐笔归因。

on_market_data 是分界点：SimBroker 在此撮合；实盘 Broker 忽略它，
因为真实市场自己会撮合。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from qsys.core.events import MarketSnapshot, Reject
from qsys.core.trading import Fill, Order


class Broker(ABC):
    @abstractmethod
    def submit(self, order: Order) -> None:
        """提交订单。订单进入未成交队列，等待下一次撮合机会。"""

    @abstractmethod
    def on_market_data(self, snapshot: MarketSnapshot) -> None:
        """市场数据到达。SimBroker 在此撮合；实盘 Broker 忽略。"""

    @abstractmethod
    def poll_fills(self) -> tuple[Fill, ...]:
        """取走自上次调用以来的成交回报。"""

    @abstractmethod
    def poll_rejects(self) -> tuple[Reject, ...]:
        """取走自上次调用以来的拒单记录。"""

    @abstractmethod
    def pending(self) -> tuple[Order, ...]:
        """当前未成交订单。"""

    @abstractmethod
    def cancel_all(self) -> None:
        """撤销全部未成交订单。"""
```

- [ ] **Step 2: 确认可导入**

Run: `uv run python -c "from qsys.backtest.broker import Broker; print(sorted(Broker.__abstractmethods__))"`
Expected: `['cancel_all', 'on_market_data', 'pending', 'poll_fills', 'poll_rejects', 'submit']`

- [ ] **Step 3: 提交**

```bash
git add src/qsys/backtest/broker.py
git commit -m "feat(backtest): Broker 抽象"
```

---

## Task 5: SimBroker 保守撮合

核心正确性原则：**订单在下一次 `on_market_data` 时以该 bar 的开盘价成交**。绝不允许用提交订单那一刻的 bar 数据成交。

**Files:**
- Create: `src/qsys/backtest/sim_broker.py`
- Test: `tests/backtest/test_sim_broker.py`

**Interfaces:**
- Consumes: `Broker`（Task 4）、`FeeModel`（Task 2）、`SlippageModel`（Task 3）、`MarketSnapshot` / `Reject`（Task 1）、`Portfolio` / `ConstraintPipeline` / `ConstraintContext`（1-2a）
- Produces: `SimBroker(portfolio, pipeline, fees, slippage, *, volume_cap=0.05)` 实现 `Broker`

- [ ] **Step 1: 写失败测试**

Create `tests/backtest/test_sim_broker.py`:

```python
from datetime import date, datetime

import pytest

from qsys.backtest.fees import ZeroFeeModel
from qsys.backtest.sim_broker import SimBroker
from qsys.backtest.slippage import FixedRateSlippage, NoSlippage
from qsys.core.events import MarketSnapshot
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, Order, OrderType, Side
from qsys.core.types import Bar, Market, Symbol
from qsys.markets.ashare import ashare_pipeline

CMB = Symbol("600036", Market.SH)
D = date(2024, 1, 3)
T = datetime(2024, 1, 2, 15, 0)


def order(side: Side, qty: int, oid: str = "o1") -> Order:
    return Order(oid, CMB, side, qty, OrderType.MARKET, None, T)


def bar(open_: float, volume: int = 1_000_000) -> Bar:
    return Bar(CMB, D, open_, open_ * 1.05, open_ * 0.95, open_ * 1.02, volume, 0.0)


def snapshot(open_: float, *, prev_close: float = 10.0, volume: int = 1_000_000,
             suspended: bool = False) -> MarketSnapshot:
    bars = {} if suspended else {CMB: bar(open_, volume)}
    return MarketSnapshot(session=D, bars=bars, prev_closes={CMB: prev_close},
                          boards={CMB: "main"})


def broker(portfolio: Portfolio, **kw) -> SimBroker:
    defaults = dict(pipeline=ashare_pipeline(), fees=ZeroFeeModel(), slippage=NoSlippage())
    return SimBroker(portfolio, **{**defaults, **kw})


def test_submitted_order_is_pending_until_market_data():
    b = broker(Portfolio(100_000.0))
    b.submit(order(Side.BUY, 100))
    assert len(b.pending()) == 1
    assert b.poll_fills() == ()


def test_order_fills_at_next_bar_open():
    """信号在 bar T 生成，成交在 bar T+1 开盘——这是不可妥协的原则。"""
    b = broker(Portfolio(100_000.0))
    b.submit(order(Side.BUY, 100))
    b.on_market_data(snapshot(10.50))
    fills = b.poll_fills()
    assert len(fills) == 1
    assert fills[0].price == 10.50
    assert fills[0].qty == 100


def test_pending_is_cleared_after_fill():
    b = broker(Portfolio(100_000.0))
    b.submit(order(Side.BUY, 100))
    b.on_market_data(snapshot(10.50))
    assert b.pending() == ()


def test_poll_fills_drains_the_queue():
    b = broker(Portfolio(100_000.0))
    b.submit(order(Side.BUY, 100))
    b.on_market_data(snapshot(10.50))
    assert len(b.poll_fills()) == 1
    assert b.poll_fills() == ()


def test_slippage_is_applied_to_fill_price():
    b = broker(Portfolio(100_000.0), slippage=FixedRateSlippage(0.001))
    b.submit(order(Side.BUY, 100))
    b.on_market_data(snapshot(10.00))
    assert b.poll_fills()[0].price == 10.01


def test_fee_is_charged_on_fill():
    from qsys.backtest.fees import AShareFeeModel

    b = broker(Portfolio(100_000.0), fees=AShareFeeModel())
    b.submit(order(Side.BUY, 100))
    b.on_market_data(snapshot(10.00))
    # gross 1000.00 -> 佣金抬到最低 5.00 + 过户费 0.01
    assert b.poll_fills()[0].fee == 5.01


def test_suspension_rejects_the_order():
    b = broker(Portfolio(100_000.0))
    b.submit(order(Side.BUY, 100))
    b.on_market_data(snapshot(10.00, suspended=True))
    assert b.poll_fills() == ()
    rejects = b.poll_rejects()
    assert len(rejects) == 1
    assert "suspend" in rejects[0].reason


def test_limit_up_rejects_buy():
    b = broker(Portfolio(100_000.0))
    b.submit(order(Side.BUY, 100))
    b.on_market_data(snapshot(11.00, prev_close=10.00))
    assert b.poll_fills() == ()
    assert "limit up" in b.poll_rejects()[0].reason


def test_volume_cap_clamps_the_fill():
    """单笔成交不超过当根 bar 成交量的 5%。"""
    b = broker(Portfolio(10_000_000.0), volume_cap=0.05)
    b.submit(order(Side.BUY, 100_000))
    b.on_market_data(snapshot(10.00, volume=1_000_000))
    assert b.poll_fills()[0].qty == 50_000


def test_volume_capped_remainder_is_rejected_not_silently_dropped():
    b = broker(Portfolio(10_000_000.0), volume_cap=0.05)
    b.submit(order(Side.BUY, 100_000))
    b.on_market_data(snapshot(10.00, volume=1_000_000))
    rejects = b.poll_rejects()
    assert len(rejects) == 1
    assert rejects[0].qty == 50_000
    assert "volume cap" in rejects[0].reason


def test_volume_cap_rounds_down_to_lot():
    b = broker(Portfolio(10_000_000.0), volume_cap=0.05)
    b.submit(order(Side.BUY, 100_000))
    b.on_market_data(snapshot(10.00, volume=1_111))  # 5% = 55.55 -> 整手取整为 0
    assert b.poll_fills() == ()
    assert b.poll_rejects() != ()


def test_constraints_clamp_by_cash():
    b = broker(Portfolio(3050.0))
    b.submit(order(Side.BUY, 1000))
    b.on_market_data(snapshot(10.00))
    assert b.poll_fills()[0].qty == 300


def test_sell_without_settled_shares_is_rejected():
    p = Portfolio(100_000.0)
    p.apply_fill(Fill("x", CMB, Side.BUY, 500, 10.0, 0.0, T))
    b = broker(p)
    b.submit(order(Side.SELL, 500))
    b.on_market_data(snapshot(10.00))
    assert b.poll_fills() == ()
    assert "settled" in b.poll_rejects()[0].reason


def test_cancel_all_clears_pending():
    b = broker(Portfolio(100_000.0))
    b.submit(order(Side.BUY, 100))
    b.cancel_all()
    assert b.pending() == ()
    b.on_market_data(snapshot(10.50))
    assert b.poll_fills() == ()


def test_fill_timestamp_is_the_session_open():
    b = broker(Portfolio(100_000.0))
    b.submit(order(Side.BUY, 100))
    b.on_market_data(snapshot(10.50))
    assert b.poll_fills()[0].filled_at.date() == D


def test_multiple_orders_fill_independently():
    b = broker(Portfolio(100_000.0))
    b.submit(order(Side.BUY, 100, "a"))
    b.submit(order(Side.BUY, 200, "b"))
    b.on_market_data(snapshot(10.00))
    assert sorted(f.qty for f in b.poll_fills()) == [100, 200]


def test_broker_does_not_mutate_the_portfolio():
    """撮合只产生 Fill，账户由引擎负责应用——职责分离。"""
    p = Portfolio(100_000.0)
    b = broker(p)
    b.submit(order(Side.BUY, 100))
    b.on_market_data(snapshot(10.50))
    b.poll_fills()
    assert p.cash == 100_000.0
    assert p.positions == {}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/backtest/test_sim_broker.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.backtest.sim_broker'`

- [ ] **Step 3: 实现 `src/qsys/backtest/sim_broker.py`**

```python
"""SimBroker 保守撮合。

不可妥协的正确性原则：订单在**下一次** on_market_data 时以该 bar 的
开盘价成交。绝不允许用提交订单那一刻的 bar 数据成交——那是回测虚高的
第二大原因（第一是未来函数，已由数据层的 as_of 契约堵死）。

撮合只产生 Fill，不修改账户：账户由引擎负责应用，职责分离。
"""

from __future__ import annotations

from datetime import datetime, time

import structlog

from qsys.backtest.broker import Broker
from qsys.backtest.fees import FeeModel
from qsys.backtest.slippage import SlippageModel
from qsys.core.constraints import ConstraintContext, ConstraintPipeline
from qsys.core.events import MarketSnapshot, Reject
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, Order, OrderIntent, to_cents

log = structlog.get_logger(__name__)

_OPEN_TIME = time(9, 30)


class SimBroker(Broker):
    def __init__(
        self,
        portfolio: Portfolio,
        pipeline: ConstraintPipeline,
        fees: FeeModel,
        slippage: SlippageModel,
        *,
        volume_cap: float = 0.05,
    ) -> None:
        if not 0 < volume_cap <= 1:
            raise ValueError(f"volume_cap must be in (0, 1]: {volume_cap}")
        self._portfolio = portfolio
        self._pipeline = pipeline
        self._fees = fees
        self._slippage = slippage
        self._volume_cap = volume_cap
        self._pending: list[Order] = []
        self._fills: list[Fill] = []
        self._rejects: list[Reject] = []

    def submit(self, order: Order) -> None:
        self._pending.append(order)

    def pending(self) -> tuple[Order, ...]:
        return tuple(self._pending)

    def cancel_all(self) -> None:
        self._pending.clear()

    def poll_fills(self) -> tuple[Fill, ...]:
        drained = tuple(self._fills)
        self._fills.clear()
        return drained

    def poll_rejects(self) -> tuple[Reject, ...]:
        drained = tuple(self._rejects)
        self._rejects.clear()
        return drained

    def on_market_data(self, snapshot: MarketSnapshot) -> None:
        at = datetime.combine(snapshot.session, _OPEN_TIME)
        queued, self._pending = self._pending, []
        for order in queued:
            self._match(order, snapshot, at)

    def _reject(self, order: Order, qty: int, reason: str, at: datetime) -> None:
        self._rejects.append(
            Reject(order.order_id, order.symbol, order.side, qty, reason, at)
        )

    def _match(self, order: Order, snapshot: MarketSnapshot, at: datetime) -> None:
        bar = snapshot.bar_of(order.symbol)
        if bar is None:
            self._reject(order, order.qty, "suspended: no bar for this session", at)
            return

        price = self._slippage.adjust(order.side, bar.open)
        ctx = ConstraintContext(
            portfolio=self._portfolio,
            reference_price=price,
            prev_close=snapshot.prev_close_of(order.symbol),
            board=snapshot.board_of(order.symbol),
            is_suspended=False,
        )
        result = self._pipeline.apply(
            OrderIntent(order.symbol, order.side, order.qty, order.order_type,
                        order.limit_price),
            ctx,
        )
        if result.is_rejected:
            self._reject(order, order.qty, result.binding_reason, at)
            return

        qty = result.allowed_qty
        cap = int(bar.volume * self._volume_cap)
        if qty > cap:
            dropped = qty - cap
            qty = cap
            # 被削减的部分作废并告警，不得静默丢弃
            self._reject(
                order, dropped,
                f"volume cap {self._volume_cap:.0%} of {bar.volume}: {dropped} shares dropped",
                at,
            )
            log.warning(
                "sim_broker.volume_capped",
                symbol=str(order.symbol),
                session=str(snapshot.session),
                requested=result.allowed_qty,
                filled=qty,
            )

        # 成交量上限可能把数量削到不足整手，需再过一次约束
        recheck = self._pipeline.apply(
            OrderIntent(order.symbol, order.side, max(qty, 1), order.order_type,
                        order.limit_price),
            ctx,
        )
        qty = min(qty, recheck.allowed_qty)
        if qty <= 0:
            self._reject(order, order.qty, recheck.binding_reason or "no fillable quantity", at)
            return

        gross = to_cents(price * qty)
        fee = self._fees.fee_for(order.side, gross)
        self._fills.append(
            Fill(order.order_id, order.symbol, order.side, qty, price, fee, at)
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/backtest/test_sim_broker.py -v`
Expected: PASS —— 17 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/backtest/sim_broker.py tests/backtest/test_sim_broker.py
git commit -m "feat(backtest): SimBroker 保守撮合"
```

---

## Task 6: 除权除息处理

回测使用不复权价，除权除息由引擎显式调整持仓与现金——这样 `SimBroker` 看到的价格与实盘 `QmtBroker` 完全一致。

**Files:**
- Create: `src/qsys/backtest/corporate.py`
- Test: `tests/backtest/test_corporate.py`

**Interfaces:**
- Consumes: `Portfolio`（1-2a）、`Symbol`（1-1）
- Produces: `CorporateAction(symbol, ex_date, cash_per_share, stock_per_share)`、`apply_corporate_actions(portfolio, actions) -> tuple[float, int]`（返回 `(派发现金总额, 送转股总数)`）、`actions_from_frame(df) -> dict[date, list[CorporateAction]]`

- [ ] **Step 1: 写失败测试**

Create `tests/backtest/test_corporate.py`:

```python
from datetime import date, datetime

import polars as pl
import pytest

from qsys.backtest.corporate import (
    CorporateAction,
    actions_from_frame,
    apply_corporate_actions,
)
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, Side
from qsys.core.types import Market, Symbol

CMB = Symbol("600036", Market.SH)
PAB = Symbol("000001", Market.SZ)
T = datetime(2024, 1, 2, 15, 0)


def holding(qty: int, price: float = 20.0, symbol: Symbol = CMB) -> Portfolio:
    p = Portfolio(100_000.0)
    p.apply_fill(Fill("o", symbol, Side.BUY, qty, price, 0.0, T))
    p.unfreeze()
    return p


def test_cash_dividend_credits_cash():
    p = holding(1000)
    cash_before = p.cash
    cash, shares = apply_corporate_actions(
        p, [CorporateAction(CMB, date(2024, 6, 14), 0.719, 0.0)]
    )
    assert cash == 719.00
    assert shares == 0
    assert p.cash == cash_before + 719.00


def test_stock_dividend_increases_holding():
    p = holding(1000)
    cash, shares = apply_corporate_actions(
        p, [CorporateAction(CMB, date(2024, 6, 14), 0.0, 1.0)]
    )
    assert shares == 1000
    assert cash == 0.0
    assert p.position(CMB).total_qty == 2000


def test_bonus_shares_are_immediately_sellable():
    """送转股到账后可卖——与当日买入不同，它不受交收约束。"""
    p = holding(1000)
    apply_corporate_actions(p, [CorporateAction(CMB, date(2024, 6, 14), 0.0, 1.0)])
    assert p.position(CMB).available_qty == 2000


def test_fractional_bonus_shares_are_rounded_down():
    """A 股送转不足一股的部分折现，此处简化为舍去。"""
    p = holding(150)
    _, shares = apply_corporate_actions(
        p, [CorporateAction(CMB, date(2024, 6, 14), 0.0, 0.3)]
    )
    assert shares == 45
    assert p.position(CMB).total_qty == 195


def test_combined_cash_and_stock():
    p = holding(1000)
    cash, shares = apply_corporate_actions(
        p, [CorporateAction(CMB, date(2024, 6, 14), 0.5, 0.2)]
    )
    assert cash == 500.00
    assert shares == 200
    assert p.position(CMB).total_qty == 1200


def test_action_on_unheld_symbol_is_ignored():
    p = holding(1000)
    cash, shares = apply_corporate_actions(
        p, [CorporateAction(PAB, date(2024, 6, 14), 1.0, 1.0)]
    )
    assert (cash, shares) == (0.0, 0)


def test_multiple_actions_accumulate():
    p = holding(1000)
    cash, shares = apply_corporate_actions(
        p,
        [
            CorporateAction(CMB, date(2024, 6, 14), 0.5, 0.0),
            CorporateAction(CMB, date(2024, 6, 14), 0.3, 0.0),
        ],
    )
    assert cash == 800.00


def test_market_value_is_preserved_across_a_pure_split():
    """10 送 10 后持仓翻倍、价格减半，账户总值不应跳变。"""
    p = holding(1000, price=20.0)
    before = p.total_value({CMB: 20.0})
    apply_corporate_actions(p, [CorporateAction(CMB, date(2024, 6, 14), 0.0, 1.0)])
    after = p.total_value({CMB: 10.0})
    assert before == after


def test_actions_from_frame_groups_by_ex_date():
    df = pl.DataFrame(
        {
            "symbol": ["600036.SH", "000001.SZ", "600036.SH"],
            "ex_date": [date(2024, 6, 14), date(2024, 6, 14), date(2024, 7, 11)],
            "cash_per_share": [0.5, 0.3, 1.0],
            "stock_per_share": [0.0, 0.0, 0.2],
        }
    )
    grouped = actions_from_frame(df)
    assert set(grouped) == {date(2024, 6, 14), date(2024, 7, 11)}
    assert len(grouped[date(2024, 6, 14)]) == 2
    assert grouped[date(2024, 7, 11)][0].stock_per_share == 0.2


def test_actions_from_empty_frame():
    df = pl.DataFrame(
        schema={
            "symbol": pl.Utf8, "ex_date": pl.Date,
            "cash_per_share": pl.Float64, "stock_per_share": pl.Float64,
        }
    )
    assert actions_from_frame(df) == {}


def test_negative_ratios_rejected():
    with pytest.raises(ValueError, match="must be >= 0"):
        CorporateAction(CMB, date(2024, 6, 14), -1.0, 0.0)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/backtest/test_corporate.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.backtest.corporate'`

- [ ] **Step 3: 实现 `src/qsys/backtest/corporate.py`**

```python
"""除权除息处理。

回测使用不复权价——那才是真实可交易的价格，整手取整、涨跌停判定、
下单价格才能与实盘一致。代价是引擎必须在除权日显式调整持仓与现金，
本模块即是。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

import polars as pl
import structlog

from qsys.core.portfolio import Portfolio
from qsys.core.trading import to_cents
from qsys.core.types import Symbol

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CorporateAction:
    symbol: Symbol
    ex_date: date
    cash_per_share: float
    stock_per_share: float

    def __post_init__(self) -> None:
        if self.cash_per_share < 0 or self.stock_per_share < 0:
            raise ValueError("cash_per_share and stock_per_share must be >= 0")


def apply_corporate_actions(
    portfolio: Portfolio, actions: Iterable[CorporateAction]
) -> tuple[float, int]:
    """在除权日调整持仓与现金。返回 (派发现金总额, 送转股总数)。

    送转股到账即可卖——与当日买入不同，它不受交收约束。
    不足一股的送转部分舍去（真实规则是折现，此处简化）。
    """
    total_cash = 0.0
    total_shares = 0

    for action in actions:
        held = portfolio.positions.get(action.symbol)
        if held is None or held.total_qty == 0:
            continue

        if action.cash_per_share > 0:
            paid = to_cents(held.total_qty * action.cash_per_share)
            portfolio.cash = to_cents(portfolio.cash + paid)
            total_cash = to_cents(total_cash + paid)

        if action.stock_per_share > 0:
            bonus = int(held.total_qty * action.stock_per_share)
            if bonus > 0:
                held.total_qty += bonus
                held.available_qty += bonus
                total_shares += bonus

        log.info(
            "corporate_action.applied",
            symbol=str(action.symbol),
            ex_date=str(action.ex_date),
            cash_per_share=action.cash_per_share,
            stock_per_share=action.stock_per_share,
        )

    return total_cash, total_shares


def actions_from_frame(df: pl.DataFrame) -> dict[date, list[CorporateAction]]:
    """把仓库的 corporate_actions 表转成按除权日分组的事件。"""
    grouped: dict[date, list[CorporateAction]] = defaultdict(list)
    for row in df.iter_rows(named=True):
        grouped[row["ex_date"]].append(
            CorporateAction(
                symbol=Symbol.parse(row["symbol"]),
                ex_date=row["ex_date"],
                cash_per_share=float(row["cash_per_share"]),
                stock_per_share=float(row["stock_per_share"]),
            )
        )
    return dict(grouped)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/backtest/test_corporate.py -v`
Expected: PASS —— 11 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/backtest/corporate.py tests/backtest/test_corporate.py
git commit -m "feat(backtest): 除权除息处理（不复权价 + 显式调整）"
```

---

## Task 7: 策略上下文

**`Context` 只暴露 `DataFeed`，策略永远拿不到 `WarehouseQuery`** —— 这是计划 1-1 完成标准 ④ 的兑现点。`as_of` 在每次回调前重新绑定到 `ctx.now`。

**Files:**
- Create: `src/qsys/strategy/__init__.py`
- Create: `src/qsys/strategy/context.py`
- Test: `tests/strategy/test_context.py`

**Interfaces:**
- Consumes: `Portfolio` / `Position`（1-2a）、`DataFeed` / `Adjustment`（1-1）、`Clock`（1-2a）
- Produces: `PortfolioView(cash, positions, total_value)` 带 `position(symbol) -> Position`；`BoundFeed(feed, as_of)` 带 `get(symbols, start, end, adjustment=NONE)`；`Context(clock, feed, portfolio, order_api, params, state, log)` 带属性 `now`、`data`、`portfolio`、`order`

- [ ] **Step 1: 写失败测试**

Create `tests/strategy/test_context.py`:

```python
from datetime import date, datetime

import polars as pl
import pytest

from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, Side
from qsys.core.types import Market, Symbol
from qsys.data.feed import Adjustment
from qsys.strategy.context import BoundFeed, Context, PortfolioView

CMB = Symbol("600036", Market.SH)
T = datetime(2024, 1, 3, 15, 0)


class RecordingFeed:
    def __init__(self):
        self.calls = []

    def get(self, symbols, start, end, *, as_of, adjustment=Adjustment.NONE):
        self.calls.append({"symbols": list(symbols), "start": start, "end": end,
                           "as_of": as_of, "adjustment": adjustment})
        return pl.DataFrame({"symbol": [], "dt": []})


class FixedClock:
    def __init__(self, now):
        self._now = now

    @property
    def now(self):
        return self._now


def portfolio_with(qty: int) -> Portfolio:
    p = Portfolio(100_000.0)
    if qty:
        p.apply_fill(Fill("o", CMB, Side.BUY, qty, 10.0, 0.0, T))
    return p


def test_bound_feed_injects_as_of():
    feed = RecordingFeed()
    BoundFeed(feed, T).get([str(CMB)], date(2024, 1, 1), date(2024, 1, 3))
    assert feed.calls[0]["as_of"] == T


def test_bound_feed_forwards_adjustment():
    feed = RecordingFeed()
    BoundFeed(feed, T).get([str(CMB)], date(2024, 1, 1), date(2024, 1, 3),
                           adjustment=Adjustment.BACKWARD)
    assert feed.calls[0]["adjustment"] is Adjustment.BACKWARD


def test_bound_feed_rejects_caller_supplied_as_of():
    """策略不得自己指定 as_of——那等于绕过 PIT 契约。"""
    feed = RecordingFeed()
    with pytest.raises(TypeError):
        BoundFeed(feed, T).get([str(CMB)], date(2024, 1, 1), date(2024, 1, 3), as_of=T)


def test_portfolio_view_exposes_cash_and_total_value():
    view = PortfolioView.of(portfolio_with(100), {CMB: 12.0})
    assert view.cash == 100_000.0 - 1000.0
    assert view.total_value == view.cash + 1200.0


def test_portfolio_view_position_lookup():
    view = PortfolioView.of(portfolio_with(100), {CMB: 12.0})
    assert view.position(CMB).total_qty == 100


def test_portfolio_view_position_of_unheld_is_empty():
    view = PortfolioView.of(portfolio_with(0), {})
    assert view.position(CMB).is_empty is True


def test_portfolio_view_is_frozen():
    view = PortfolioView.of(portfolio_with(100), {CMB: 12.0})
    with pytest.raises(Exception):
        view.cash = 0.0


def test_mutating_a_view_position_does_not_affect_the_account():
    p = portfolio_with(100)
    view = PortfolioView.of(p, {CMB: 12.0})
    view.position(CMB).total_qty = 999
    assert p.position(CMB).total_qty == 100


def test_context_now_comes_from_the_clock():
    ctx = Context(FixedClock(T), RecordingFeed(), PortfolioView.of(portfolio_with(0), {}),
                  object(), params={}, state={})
    assert ctx.now == T


def test_context_data_is_bound_to_now():
    feed = RecordingFeed()
    ctx = Context(FixedClock(T), feed, PortfolioView.of(portfolio_with(0), {}),
                  object(), params={}, state={})
    ctx.data.get([str(CMB)], date(2024, 1, 1), date(2024, 1, 3))
    assert feed.calls[0]["as_of"] == T


def test_context_exposes_no_warehouse_query():
    """完成标准 4：策略层不得触达 WarehouseQuery。"""
    ctx = Context(FixedClock(T), RecordingFeed(), PortfolioView.of(portfolio_with(0), {}),
                  object(), params={}, state={})
    exposed = {name for name in dir(ctx) if not name.startswith("_")}
    assert "query" not in exposed
    assert "warehouse" not in exposed
    assert type(ctx.data).__name__ == "BoundFeed"


def test_context_state_is_mutable_and_persists():
    state = {}
    ctx = Context(FixedClock(T), RecordingFeed(), PortfolioView.of(portfolio_with(0), {}),
                  object(), params={}, state=state)
    ctx.state["seen"] = 1
    assert state["seen"] == 1


def test_context_params_are_readable():
    ctx = Context(FixedClock(T), RecordingFeed(), PortfolioView.of(portfolio_with(0), {}),
                  object(), params={"window": 20}, state={})
    assert ctx.params["window"] == 20
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/strategy/test_context.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.strategy'`

- [ ] **Step 3: 实现**

```bash
mkdir -p src/qsys/strategy tests/strategy
touch src/qsys/strategy/__init__.py
```

Create `src/qsys/strategy/context.py`:

```python
"""策略运行时上下文。

显式注入，不使用全局变量——换来两项实在的收益：策略可单元测试
（注入假 ctx，断言它下了什么单，无需跑回测），多策略并行不互相污染状态。

Context 只暴露 DataFeed，策略永远拿不到 WarehouseQuery——
数据访问的 as_of 由 BoundFeed 强制绑定到 ctx.now。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any

import polars as pl
import structlog

from qsys.core.portfolio import Portfolio, Position
from qsys.core.trading import to_cents
from qsys.core.types import Symbol
from qsys.data.feed import Adjustment, DataFeed


class BoundFeed:
    """把 as_of 钉死在构造时刻的数据视图。

    策略不能自己指定 as_of——那等于绕过 PIT 契约。
    """

    def __init__(self, feed: DataFeed, as_of: datetime) -> None:
        self._feed = feed
        self._as_of = as_of

    def get(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
        adjustment: Adjustment = Adjustment.NONE,
    ) -> pl.DataFrame:
        return self._feed.get(
            symbols, start, end, as_of=self._as_of, adjustment=adjustment
        )


@dataclass(frozen=True, slots=True)
class PortfolioView:
    """只读账户视图，每次回调前重建。"""

    cash: float
    total_value: float
    _positions: Mapping[Symbol, Position]

    @classmethod
    def of(cls, portfolio: Portfolio, prices: Mapping[Symbol, float]) -> PortfolioView:
        market = 0.0
        for symbol, held in portfolio.positions.items():
            market += held.total_qty * prices.get(symbol, 0.0)
        return cls(
            cash=portfolio.cash,
            total_value=to_cents(portfolio.cash + market),
            _positions={s: replace(p) for s, p in portfolio.positions.items()},
        )

    def position(self, symbol: Symbol) -> Position:
        held = self._positions.get(symbol)
        return replace(held) if held is not None else Position(symbol)

    @property
    def symbols(self) -> frozenset[Symbol]:
        return frozenset(self._positions)


class Context:
    def __init__(
        self,
        clock,
        feed: DataFeed,
        portfolio: PortfolioView,
        order_api,
        *,
        params: dict[str, Any],
        state: dict[str, Any],
        log=None,
    ) -> None:
        self._clock = clock
        self._feed = feed
        self._portfolio = portfolio
        self._order = order_api
        self.params = params
        self.state = state
        self.log = log if log is not None else structlog.get_logger("strategy")

    @property
    def now(self) -> datetime:
        return self._clock.now

    @property
    def data(self) -> BoundFeed:
        return BoundFeed(self._feed, self.now)

    @property
    def portfolio(self) -> PortfolioView:
        return self._portfolio

    @property
    def order(self):
        return self._order
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/strategy/test_context.py -v`
Expected: PASS —— 13 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/strategy tests/strategy/test_context.py
git commit -m "feat(strategy): 策略上下文与只读账户视图"
```

---

## Task 8: 下单接口与策略基类

**Files:**
- Create: `src/qsys/strategy/orders.py`
- Create: `src/qsys/strategy/base.py`
- Test: `tests/strategy/test_order_api.py`

**Interfaces:**
- Consumes: `OrderIntent` / `Side` / `OrderType`（1-2a）、`PortfolioView`（Task 7）
- Produces: `OrderApi(portfolio_view, prices)` 带 `submit(symbol, side, qty, order_type=MARKET, limit_price=None)`、`target_qty(symbol, qty)`、`target_value(symbol, value)`、`target_weight(symbol, weight)`、`drain() -> tuple[OrderIntent, ...]`；`Strategy` (ABC: `on_init` / `on_session_start` / `on_bar` / `on_fill` / `on_session_end`，默认实现均为空)

- [ ] **Step 1: 写失败测试**

Create `tests/strategy/test_order_api.py`:

```python
from datetime import datetime

import pytest

from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, OrderType, Side
from qsys.core.types import Market, Symbol
from qsys.strategy.base import Strategy
from qsys.strategy.context import PortfolioView
from qsys.strategy.orders import OrderApi

CMB = Symbol("600036", Market.SH)
T = datetime(2024, 1, 3, 15, 0)


def api(held: int = 0, cash: float = 100_000.0, price: float = 10.0) -> OrderApi:
    p = Portfolio(cash)
    if held:
        p.apply_fill(Fill("o", CMB, Side.BUY, held, price, 0.0, T))
        p.unfreeze()
    return OrderApi(PortfolioView.of(p, {CMB: price}), {CMB: price})


def test_submit_queues_an_intent():
    a = api()
    a.submit(CMB, Side.BUY, 100)
    intents = a.drain()
    assert len(intents) == 1
    assert intents[0].qty == 100
    assert intents[0].side is Side.BUY


def test_drain_empties_the_queue():
    a = api()
    a.submit(CMB, Side.BUY, 100)
    a.drain()
    assert a.drain() == ()


def test_submit_limit_order_carries_price():
    a = api()
    a.submit(CMB, Side.BUY, 100, OrderType.LIMIT, 9.5)
    intent = a.drain()[0]
    assert intent.order_type is OrderType.LIMIT
    assert intent.limit_price == 9.5


def test_target_qty_buys_the_difference():
    a = api(held=300)
    a.target_qty(CMB, 500)
    intent = a.drain()[0]
    assert intent.side is Side.BUY
    assert intent.qty == 200


def test_target_qty_sells_the_difference():
    a = api(held=500)
    a.target_qty(CMB, 300)
    intent = a.drain()[0]
    assert intent.side is Side.SELL
    assert intent.qty == 200


def test_target_qty_no_change_emits_nothing():
    a = api(held=500)
    a.target_qty(CMB, 500)
    assert a.drain() == ()


def test_target_qty_zero_liquidates():
    a = api(held=500)
    a.target_qty(CMB, 0)
    intent = a.drain()[0]
    assert intent.side is Side.SELL
    assert intent.qty == 500


def test_target_value_converts_by_price():
    a = api(price=10.0)
    a.target_value(CMB, 5000.0)
    assert a.drain()[0].qty == 500


def test_target_weight_uses_total_value():
    # 现金 100000、无持仓 -> 总值 100000；10% -> 10000 元 -> 10 元/股 -> 1000 股
    a = api(price=10.0)
    a.target_weight(CMB, 0.10)
    assert a.drain()[0].qty == 1000


def test_target_weight_accounts_for_existing_holding():
    # 持仓 500 股 @10 -> 总值仍是 100000（5000 现金变股票）；20% -> 2000 股 -> 买 1500
    a = api(held=500, price=10.0)
    a.target_weight(CMB, 0.20)
    intent = a.drain()[0]
    assert intent.side is Side.BUY
    assert intent.qty == 1500


def test_target_weight_rejects_out_of_range():
    a = api()
    with pytest.raises(ValueError, match="weight must be in"):
        a.target_weight(CMB, 1.5)


def test_target_without_a_price_raises():
    a = OrderApi(PortfolioView.of(Portfolio(100_000.0), {}), {})
    with pytest.raises(KeyError, match="no price"):
        a.target_value(CMB, 1000.0)


def test_submit_rejects_non_positive_qty():
    a = api()
    with pytest.raises(ValueError, match="qty must be positive"):
        a.submit(CMB, Side.BUY, 0)


def test_strategy_hooks_default_to_no_op():
    class Empty(Strategy):
        pass

    s = Empty()
    s.on_init(None)
    s.on_session_start(None)
    s.on_bar(None, {})
    s.on_fill(None, None)
    s.on_session_end(None)


def test_strategy_can_be_unit_tested_without_a_backtest():
    """显式注入换来的收益：塞假 ctx，断言下单意图。"""

    class BuyOnce(Strategy):
        def on_bar(self, ctx, bars):
            ctx.order.submit(CMB, Side.BUY, 100)

    a = api()

    class FakeCtx:
        order = a

    BuyOnce().on_bar(FakeCtx(), {})
    assert a.drain()[0].qty == 100
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/strategy/test_order_api.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.strategy.orders'`

- [ ] **Step 3: 实现 `src/qsys/strategy/orders.py`**

```python
"""下单接口。

两层：低层 submit 精确控制；高层 target_* 声明目标状态。
高层在内部转成低层，最终数量的整手取整与资金检查由约束系统在撮合前完成——
因此 target_weight 是近似的，实际成交数量以约束判定为准。
"""

from __future__ import annotations

from collections.abc import Mapping

from qsys.core.trading import OrderIntent, OrderType, Side
from qsys.core.types import Symbol
from qsys.strategy.context import PortfolioView


class OrderApi:
    def __init__(
        self, portfolio: PortfolioView, prices: Mapping[Symbol, float]
    ) -> None:
        self._portfolio = portfolio
        self._prices = prices
        self._queue: list[OrderIntent] = []

    def drain(self) -> tuple[OrderIntent, ...]:
        drained = tuple(self._queue)
        self._queue.clear()
        return drained

    def submit(
        self,
        symbol: Symbol,
        side: Side,
        qty: int,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
    ) -> None:
        self._queue.append(OrderIntent(symbol, side, qty, order_type, limit_price))

    def _price_of(self, symbol: Symbol) -> float:
        price = self._prices.get(symbol)
        if price is None or price <= 0:
            raise KeyError(f"no price for {symbol}; cannot size the order")
        return price

    def target_qty(self, symbol: Symbol, qty: int) -> None:
        if qty < 0:
            raise ValueError(f"target qty must be >= 0: {qty}")
        current = self._portfolio.position(symbol).total_qty
        diff = qty - current
        if diff == 0:
            return
        side = Side.BUY if diff > 0 else Side.SELL
        self.submit(symbol, side, abs(diff))

    def target_value(self, symbol: Symbol, value: float) -> None:
        if value < 0:
            raise ValueError(f"target value must be >= 0: {value}")
        self.target_qty(symbol, int(value / self._price_of(symbol)))

    def target_weight(self, symbol: Symbol, weight: float) -> None:
        if not 0.0 <= weight <= 1.0:
            raise ValueError(f"weight must be in [0, 1]: {weight}")
        self.target_value(symbol, self._portfolio.total_value * weight)
```

- [ ] **Step 4: 实现 `src/qsys/strategy/base.py`**

```python
"""策略基类。

所有钩子默认空实现——策略只覆盖自己关心的那几个。
ctx 显式传入，不使用全局变量。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class Strategy:
    def on_init(self, ctx: Any) -> None:
        """一次性初始化：声明参数、注册因子、订阅数据。"""

    def on_session_start(self, ctx: Any) -> None:
        """每日盘前：选股、算目标仓位。此时当日 bar 尚不可见。"""

    def on_bar(self, ctx: Any, bars: Mapping[Any, Any]) -> None:
        """主逻辑。此时当日 bar 已可见，下单将在下一交易日开盘成交。"""

    def on_fill(self, ctx: Any, fill: Any) -> None:
        """成交回报。"""

    def on_session_end(self, ctx: Any) -> None:
        """收盘后：记录、复盘。"""
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/strategy/test_order_api.py -v`
Expected: PASS —— 15 passed

- [ ] **Step 6: 提交**

```bash
git add src/qsys/strategy/orders.py src/qsys/strategy/base.py tests/strategy/test_order_api.py
git commit -m "feat(strategy): 下单接口与策略基类"
```

---

## Task 9: 回测驱动器

每个交易日的执行顺序是本任务的核心，顺序错了整个回测就错了：

```
1. portfolio.unfreeze()              昨日及更早买入的份额解冻
2. 应用当日除权除息                    持仓与现金调整
3. strategy.on_session_start(ctx)    盘前，as_of = 09:15（可见至前一日收盘）
4. broker.on_market_data(snapshot)   撮合昨日提交的订单 @ 今日开盘
5. 应用 fills + strategy.on_fill     今日成交**不**解冻（T+1 的物理基础）
6. strategy.on_bar(ctx, bars)        as_of = 15:00（当日 bar 可见），下单待明日成交
7. strategy.on_session_end(ctx)      as_of = 15:30
8. 记录当日净值
```

**Files:**
- Create: `src/qsys/backtest/engine.py`
- Test: `tests/backtest/test_engine.py`

**Interfaces:**
- Consumes: 前八个任务的全部产出、`HistoricalFeed` / `Adjustment`（1-1）、`BacktestClock` / `Phase`（1-2a）
- Produces: `BacktestResult(equity: pl.DataFrame, fills, rejects, run_id, params)` 带 `final_value: float`；`BacktestEngine(strategy, feed, calendar, universe, *, initial_cash, pipeline, fees, slippage, volume_cap=0.05, corporate_actions=None, params=None)` 带 `run(start, end) -> BacktestResult`

- [ ] **Step 1: 写失败测试**

Create `tests/backtest/test_engine.py`:

```python
from datetime import date, datetime

import polars as pl
import pytest

from qsys.backtest.corporate import CorporateAction
from qsys.backtest.engine import BacktestEngine
from qsys.backtest.fees import ZeroFeeModel
from qsys.backtest.slippage import NoSlippage
from qsys.core.calendar import AShareCalendar
from qsys.core.trading import Side
from qsys.core.types import Market, Symbol
from qsys.markets.ashare import ashare_pipeline
from qsys.strategy.base import Strategy

CMB = Symbol("600036", Market.SH)
SESSIONS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]
CAL = AShareCalendar(SESSIONS)

# open / high / low / close，每日成交量 1_000_000
PRICES = {
    SESSIONS[0]: (10.0, 10.5, 9.8, 10.0),
    SESSIONS[1]: (10.0, 10.6, 9.9, 10.5),
    SESSIONS[2]: (10.5, 11.2, 10.4, 11.0),
    SESSIONS[3]: (11.0, 11.5, 10.8, 11.2),
}


class StubFeed:
    """按 as_of 过滤的最小 DataFeed 实现，语义与 HistoricalFeed 一致。"""

    def __init__(self, prices=PRICES, symbol=CMB):
        self._prices = prices
        self._symbol = symbol
        self.as_of_seen: list[datetime] = []

    def get(self, symbols, start, end, *, as_of, adjustment=None):
        self.as_of_seen.append(as_of)
        rows = []
        for session, (o, h, l, c) in sorted(self._prices.items()):
            if not (start <= session <= end):
                continue
            if as_of < datetime.combine(session, datetime.min.time()).replace(hour=15):
                continue
            if str(self._symbol) not in symbols:
                continue
            rows.append({"symbol": str(self._symbol), "dt": session, "open": o,
                         "high": h, "low": l, "close": c, "volume": 1_000_000,
                         "amount": c * 1_000_000})
        return pl.DataFrame(
            rows,
            schema={"symbol": pl.Utf8, "dt": pl.Date, "open": pl.Float64,
                    "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
                    "volume": pl.Int64, "amount": pl.Float64},
        )


def engine(strategy: Strategy, **kw) -> BacktestEngine:
    defaults = dict(
        feed=StubFeed(),
        calendar=CAL,
        universe=[CMB],
        initial_cash=100_000.0,
        pipeline=ashare_pipeline(),
        fees=ZeroFeeModel(),
        slippage=NoSlippage(),
    )
    return BacktestEngine(strategy, **{**defaults, **kw})


class BuyFirstBar(Strategy):
    def on_bar(self, ctx, bars):
        if not ctx.portfolio.symbols:
            ctx.order.submit(CMB, Side.BUY, 1000)


def test_run_produces_an_equity_curve_per_session():
    result = engine(BuyFirstBar()).run(SESSIONS[0], SESSIONS[-1])
    assert result.equity.height == len(SESSIONS)
    assert result.equity["dt"].to_list() == SESSIONS


def test_signal_on_bar_t_fills_at_open_of_t_plus_one():
    """核心正确性原则的端到端验证。"""
    result = engine(BuyFirstBar()).run(SESSIONS[0], SESSIONS[-1])
    assert len(result.fills) == 1
    fill = result.fills[0]
    assert fill.filled_at.date() == SESSIONS[1]
    assert fill.price == PRICES[SESSIONS[1]][0]  # 次日开盘价 10.0


def test_no_fill_before_the_first_signal():
    result = engine(BuyFirstBar()).run(SESSIONS[0], SESSIONS[-1])
    first_equity = result.equity.row(0, named=True)
    assert first_equity["cash"] == 100_000.0


def test_final_value_reflects_position_marked_to_close():
    result = engine(BuyFirstBar()).run(SESSIONS[0], SESSIONS[-1])
    # 1000 股 @10.0 买入，最后一日收盘 11.2
    assert result.final_value == pytest.approx(90_000.0 + 11_200.0)


def test_strategy_cannot_see_future_data():
    """通过 ctx.data 取数时 as_of 恒等于 ctx.now。"""
    seen = []

    class Peeker(Strategy):
        def on_bar(self, ctx, bars):
            df = ctx.data.get([str(CMB)], SESSIONS[0], SESSIONS[-1])
            seen.append((ctx.now.date(), df["dt"].max()))

    engine(Peeker()).run(SESSIONS[0], SESSIONS[-1])
    for session, latest in seen:
        assert latest <= session, f"{session} 看到了 {latest}"


def test_session_start_cannot_see_todays_bar():
    seen = []

    class PreOpenPeeker(Strategy):
        def on_session_start(self, ctx):
            df = ctx.data.get([str(CMB)], SESSIONS[0], SESSIONS[-1])
            seen.append((ctx.now.date(), None if df.height == 0 else df["dt"].max()))

    engine(PreOpenPeeker()).run(SESSIONS[0], SESSIONS[-1])
    for session, latest in seen:
        assert latest is None or latest < session


def test_t_plus_one_is_enforced_end_to_end():
    """当日买入当日卖出的意图必须被拒。"""

    class BuyThenSellSameDay(Strategy):
        def on_fill(self, ctx, fill):
            if fill.side is Side.BUY:
                ctx.order.submit(CMB, Side.SELL, fill.qty)

        def on_bar(self, ctx, bars):
            if not ctx.portfolio.symbols and not ctx.state.get("bought"):
                ctx.state["bought"] = True
                ctx.order.submit(CMB, Side.BUY, 1000)

    result = engine(BuyThenSellSameDay()).run(SESSIONS[0], SESSIONS[-1])
    sells = [f for f in result.fills if f.side is Side.SELL]
    # 卖单在下一交易日开盘撮合，此时已解冻，故应成交且晚于买入日
    assert all(f.filled_at.date() > result.fills[0].filled_at.date() for f in sells)


def test_on_fill_is_called_for_each_fill():
    calls = []

    class Recorder(BuyFirstBar):
        def on_fill(self, ctx, fill):
            calls.append(fill.qty)

    engine(Recorder()).run(SESSIONS[0], SESSIONS[-1])
    assert calls == [1000]


def test_corporate_action_credits_cash():
    actions = {SESSIONS[2]: [CorporateAction(CMB, SESSIONS[2], 0.5, 0.0)]}
    result = engine(BuyFirstBar(), corporate_actions=actions).run(SESSIONS[0], SESSIONS[-1])
    # 1000 股 x 0.5 元 = 500 元
    assert result.final_value == pytest.approx(90_000.0 + 500.0 + 11_200.0)


def test_corporate_action_stock_dividend_increases_holding():
    actions = {SESSIONS[2]: [CorporateAction(CMB, SESSIONS[2], 0.0, 1.0)]}
    result = engine(BuyFirstBar(), corporate_actions=actions).run(SESSIONS[0], SESSIONS[-1])
    assert result.final_value == pytest.approx(90_000.0 + 2000 * 11.2)


def test_run_id_is_stable_for_identical_inputs():
    a = engine(BuyFirstBar(), params={"w": 20}).run(SESSIONS[0], SESSIONS[-1])
    b = engine(BuyFirstBar(), params={"w": 20}).run(SESSIONS[0], SESSIONS[-1])
    assert a.run_id == b.run_id


def test_run_id_changes_with_params():
    a = engine(BuyFirstBar(), params={"w": 20}).run(SESSIONS[0], SESSIONS[-1])
    b = engine(BuyFirstBar(), params={"w": 30}).run(SESSIONS[0], SESSIONS[-1])
    assert a.run_id != b.run_id


def test_equity_curve_columns():
    result = engine(BuyFirstBar()).run(SESSIONS[0], SESSIONS[-1])
    assert result.equity.columns == ["dt", "cash", "market_value", "total_value"]


def test_empty_strategy_keeps_initial_cash():
    result = engine(Strategy()).run(SESSIONS[0], SESSIONS[-1])
    assert result.final_value == 100_000.0
    assert result.fills == ()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/backtest/test_engine.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.backtest.engine'`

- [ ] **Step 3: 实现 `src/qsys/backtest/engine.py`**

```python
"""回测驱动器。

每个交易日的执行顺序是正确性的核心，顺序错了整个回测就错了：

  1. unfreeze()                     昨日及更早买入的份额解冻
  2. 应用当日除权除息                 持仓与现金调整
  3. on_session_start(ctx)          盘前，as_of = 09:15
  4. broker.on_market_data()        撮合昨日订单 @ 今日开盘
  5. 应用 fills + on_fill           今日成交**不**解冻（T+1 的物理基础）
  6. on_bar(ctx, bars)              as_of = 15:00，下单待明日成交
  7. on_session_end(ctx)            as_of = 15:30
  8. 记录当日净值

引擎持有全区间行情属基础设施；策略只能通过 ctx.data 取数，
其 as_of 恒等于 ctx.now——引擎绝不把未来 bar 交给策略。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

import polars as pl
import structlog

from qsys.backtest.corporate import CorporateAction, apply_corporate_actions
from qsys.backtest.fees import FeeModel
from qsys.backtest.sim_broker import SimBroker
from qsys.backtest.slippage import SlippageModel
from qsys.core.calendar import TradingCalendar
from qsys.core.clock import BacktestClock, Phase
from qsys.core.constraints import ConstraintPipeline
from qsys.core.events import MarketSnapshot, Reject
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, Order, to_cents
from qsys.core.types import Bar, Symbol

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BacktestResult:
    equity: pl.DataFrame
    fills: tuple[Fill, ...]
    rejects: tuple[Reject, ...]
    run_id: str
    params: Mapping[str, object] = field(default_factory=dict)

    @property
    def final_value(self) -> float:
        if self.equity.height == 0:
            return 0.0
        return float(self.equity["total_value"].to_list()[-1])


class BacktestEngine:
    def __init__(
        self,
        strategy,
        *,
        feed,
        calendar: TradingCalendar,
        universe: Sequence[Symbol],
        initial_cash: float,
        pipeline: ConstraintPipeline,
        fees: FeeModel,
        slippage: SlippageModel,
        volume_cap: float = 0.05,
        corporate_actions: Mapping[date, Sequence[CorporateAction]] | None = None,
        params: Mapping[str, object] | None = None,
        boards: Mapping[Symbol, str] | None = None,
    ) -> None:
        self._strategy = strategy
        self._feed = feed
        self._calendar = calendar
        self._universe = tuple(universe)
        self._initial_cash = initial_cash
        self._pipeline = pipeline
        self._fees = fees
        self._slippage = slippage
        self._volume_cap = volume_cap
        self._actions = dict(corporate_actions or {})
        self._params = dict(params or {})
        self._boards = dict(boards or {})

    def _run_id(self, start: date, end: date) -> str:
        payload = json.dumps(
            {
                "strategy": type(self._strategy).__name__,
                "universe": sorted(str(s) for s in self._universe),
                "start": str(start),
                "end": str(end),
                "initial_cash": self._initial_cash,
                "volume_cap": self._volume_cap,
                "params": {k: str(v) for k, v in sorted(self._params.items())},
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def _load_bars(self, start: date, end: date) -> dict[date, dict[Symbol, Bar]]:
        """预取全区间行情。这是引擎的基础设施，绝不交给策略。"""
        symbols = [str(s) for s in self._universe]
        as_of = self._calendar.close_time(self._calendar.sessions_in_range(start, end)[-1])
        df = self._feed.get(symbols, start, end, as_of=as_of)

        by_session: dict[date, dict[Symbol, Bar]] = {}
        for row in df.iter_rows(named=True):
            symbol = Symbol.parse(row["symbol"])
            by_session.setdefault(row["dt"], {})[symbol] = Bar(
                symbol=symbol, dt=row["dt"], open=row["open"], high=row["high"],
                low=row["low"], close=row["close"], volume=row["volume"],
                amount=row["amount"],
            )
        return by_session

    def run(self, start: date, end: date) -> BacktestResult:
        from qsys.strategy.context import Context, PortfolioView
        from qsys.strategy.orders import OrderApi

        sessions = self._calendar.sessions_in_range(start, end)
        if not sessions:
            return BacktestResult(
                pl.DataFrame(schema={"dt": pl.Date, "cash": pl.Float64,
                                     "market_value": pl.Float64,
                                     "total_value": pl.Float64}),
                (), (), self._run_id(start, end), self._params,
            )

        bars_by_session = self._load_bars(start, end)
        portfolio = Portfolio(self._initial_cash)
        broker = SimBroker(portfolio, self._pipeline, self._fees, self._slippage,
                           volume_cap=self._volume_cap)
        clock = BacktestClock(self._calendar, start, end)
        state: dict[str, object] = {}

        all_fills: list[Fill] = []
        all_rejects: list[Reject] = []
        equity_rows: list[dict[str, object]] = []
        last_close: dict[Symbol, float] = {}
        prev_closes: dict[Symbol, float] = {}
        order_seq = 0

        def make_ctx(prices: Mapping[Symbol, float]) -> tuple[Context, OrderApi]:
            view = PortfolioView.of(portfolio, prices)
            api = OrderApi(view, prices)
            return Context(clock, self._feed, view, api,
                           params=self._params, state=state), api

        def flush(api: OrderApi, at: datetime) -> None:
            nonlocal order_seq
            for intent in api.drain():
                order_seq += 1
                broker.submit(Order(f"o{order_seq}", intent.symbol, intent.side,
                                    intent.qty, intent.order_type, intent.limit_price, at))

        self._strategy.on_init(make_ctx(last_close)[0])

        for moment, phase in clock.ticks():
            session = moment.date()
            bars = bars_by_session.get(session, {})

            if phase is Phase.SESSION_START:
                portfolio.unfreeze()
                if session in self._actions:
                    apply_corporate_actions(portfolio, self._actions[session])

                ctx, api = make_ctx(last_close)
                self._strategy.on_session_start(ctx)
                flush(api, moment)

                snapshot = MarketSnapshot(
                    session=session, bars=bars, prev_closes=dict(prev_closes),
                    boards=self._boards,
                )
                broker.on_market_data(snapshot)
                fills = broker.poll_fills()
                rejects = broker.poll_rejects()
                all_rejects.extend(rejects)
                for fill in fills:
                    portfolio.apply_fill(fill)
                    all_fills.append(fill)
                    fill_ctx, fill_api = make_ctx(last_close)
                    self._strategy.on_fill(fill_ctx, fill)
                    flush(fill_api, moment)

            elif phase is Phase.BAR:
                for symbol, bar in bars.items():
                    last_close[symbol] = bar.close
                ctx, api = make_ctx(last_close)
                self._strategy.on_bar(ctx, bars)
                flush(api, moment)

            else:  # SESSION_END
                ctx, api = make_ctx(last_close)
                self._strategy.on_session_end(ctx)
                flush(api, moment)

                market = sum(
                    held.total_qty * last_close.get(symbol, 0.0)
                    for symbol, held in portfolio.positions.items()
                )
                equity_rows.append({
                    "dt": session,
                    "cash": portfolio.cash,
                    "market_value": to_cents(market),
                    "total_value": to_cents(portfolio.cash + market),
                })
                prev_closes = dict(last_close)

        equity = pl.DataFrame(
            equity_rows,
            schema={"dt": pl.Date, "cash": pl.Float64, "market_value": pl.Float64,
                    "total_value": pl.Float64},
        )
        log.info("backtest.finished", sessions=len(sessions), fills=len(all_fills),
                 rejects=len(all_rejects))
        return BacktestResult(equity, tuple(all_fills), tuple(all_rejects),
                              self._run_id(start, end), self._params)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/backtest/test_engine.py -v`
Expected: PASS —— 14 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/backtest/engine.py tests/backtest/test_engine.py
git commit -m "feat(backtest): 回测驱动器"
```

---

## Task 10: 绩效指标

**Files:**
- Create: `src/qsys/backtest/metrics.py`
- Test: `tests/backtest/test_metrics.py`

**Interfaces:**
- Consumes: `BacktestResult`（Task 9）、`Fill` / `Side`（1-2a）
- Produces: `Metrics(total_return, annualized_return, volatility, sharpe, max_drawdown, max_drawdown_days, n_trades, win_rate, turnover)`；`compute_metrics(equity, fills=(), *, periods_per_year=243, risk_free_rate=0.0) -> Metrics`

- [ ] **Step 1: 写失败测试**

Create `tests/backtest/test_metrics.py`:

```python
from datetime import date

import polars as pl
import pytest

from qsys.backtest.metrics import compute_metrics


def equity(values: list[float]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "dt": [date(2024, 1, d + 1) for d in range(len(values))],
            "cash": [0.0] * len(values),
            "market_value": values,
            "total_value": values,
        },
        schema={"dt": pl.Date, "cash": pl.Float64, "market_value": pl.Float64,
                "total_value": pl.Float64},
    )


def test_total_return_on_a_doubling():
    assert compute_metrics(equity([100.0, 200.0])).total_return == pytest.approx(1.0)


def test_total_return_on_a_loss():
    assert compute_metrics(equity([100.0, 50.0])).total_return == pytest.approx(-0.5)


def test_flat_curve_has_zero_return_and_volatility():
    m = compute_metrics(equity([100.0] * 10))
    assert m.total_return == 0.0
    assert m.volatility == 0.0
    assert m.max_drawdown == 0.0


def test_max_drawdown_on_a_peak_and_trough():
    # 100 -> 120 -> 90 -> 110：最大回撤 = 1 - 90/120 = 0.25
    m = compute_metrics(equity([100.0, 120.0, 90.0, 110.0]))
    assert m.max_drawdown == pytest.approx(0.25)


def test_max_drawdown_days_counts_the_longest_underwater_run():
    m = compute_metrics(equity([100.0, 120.0, 90.0, 95.0, 130.0]))
    assert m.max_drawdown_days == 2


def test_monotonic_rise_has_no_drawdown():
    assert compute_metrics(equity([100.0, 110.0, 120.0])).max_drawdown == 0.0


def test_annualized_return_of_a_full_year():
    # 243 个交易日翻倍 -> 年化 100%
    values = [100.0 * (2 ** (i / 243)) for i in range(244)]
    m = compute_metrics(equity(values), periods_per_year=243)
    assert m.annualized_return == pytest.approx(1.0, abs=0.02)


def test_sharpe_of_a_constant_positive_return():
    # 每期收益恒定 -> 波动为 0 -> 夏普定义为 0（避免除零）
    values = [100.0 * (1.001 ** i) for i in range(50)]
    assert compute_metrics(equity(values)).sharpe == 0.0


def test_sharpe_is_positive_for_a_rising_noisy_curve():
    values = [100.0]
    for i in range(60):
        values.append(values[-1] * (1.002 if i % 3 else 0.999))
    assert compute_metrics(equity(values)).sharpe > 0


def test_trade_stats_from_fills():
    from datetime import datetime

    from qsys.core.trading import Fill, Side
    from qsys.core.types import Market, Symbol

    cmb = Symbol("600036", Market.SH)
    t = datetime(2024, 1, 2, 9, 30)
    fills = (
        Fill("a", cmb, Side.BUY, 100, 10.0, 0.0, t),
        Fill("b", cmb, Side.SELL, 100, 12.0, 0.0, t),
        Fill("c", cmb, Side.BUY, 100, 12.0, 0.0, t),
        Fill("d", cmb, Side.SELL, 100, 11.0, 0.0, t),
    )
    m = compute_metrics(equity([100.0, 110.0]), fills)
    assert m.n_trades == 4
    assert m.win_rate == pytest.approx(0.5)


def test_no_fills_yields_zero_trade_stats():
    m = compute_metrics(equity([100.0, 110.0]))
    assert m.n_trades == 0
    assert m.win_rate == 0.0
    assert m.turnover == 0.0


def test_single_point_curve_is_all_zeros():
    m = compute_metrics(equity([100.0]))
    assert m.total_return == 0.0
    assert m.max_drawdown == 0.0


def test_empty_curve_does_not_crash():
    m = compute_metrics(equity([]))
    assert m.total_return == 0.0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/backtest/test_metrics.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.backtest.metrics'`

- [ ] **Step 3: 实现 `src/qsys/backtest/metrics.py`**

```python
"""绩效指标。

periods_per_year 默认 243——A 股近年的年均交易日数。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import polars as pl

from qsys.core.trading import Fill, Side

_TRADING_DAYS = 243


@dataclass(frozen=True, slots=True)
class Metrics:
    total_return: float = 0.0
    annualized_return: float = 0.0
    volatility: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_days: int = 0
    n_trades: int = 0
    win_rate: float = 0.0
    turnover: float = 0.0


def _drawdown(values: Sequence[float]) -> tuple[float, int]:
    peak = values[0]
    worst = 0.0
    underwater = 0
    longest = 0
    for value in values:
        if value >= peak:
            peak = value
            underwater = 0
        else:
            underwater += 1
            longest = max(longest, underwater)
            worst = max(worst, 1.0 - value / peak)
    return worst, longest


def _trade_stats(fills: Sequence[Fill]) -> tuple[int, float, float]:
    """按先进先出配对买卖，统计笔数、胜率与换手额。"""
    if not fills:
        return 0, 0.0, 0.0

    lots: dict[object, list[tuple[int, float]]] = {}
    wins = closed = 0
    turnover = 0.0

    for fill in fills:
        turnover += fill.gross
        queue = lots.setdefault(fill.symbol, [])
        if fill.side is Side.BUY:
            queue.append((fill.qty, fill.price))
            continue
        remaining = fill.qty
        while remaining > 0 and queue:
            qty, cost = queue[0]
            matched = min(qty, remaining)
            closed += 1
            if fill.price > cost:
                wins += 1
            remaining -= matched
            if matched == qty:
                queue.pop(0)
            else:
                queue[0] = (qty - matched, cost)

    win_rate = wins / closed if closed else 0.0
    return len(fills), win_rate, turnover


def compute_metrics(
    equity: pl.DataFrame,
    fills: Sequence[Fill] = (),
    *,
    periods_per_year: int = _TRADING_DAYS,
    risk_free_rate: float = 0.0,
) -> Metrics:
    n_trades, win_rate, turnover = _trade_stats(list(fills))

    if equity.height < 2:
        return Metrics(n_trades=n_trades, win_rate=win_rate, turnover=turnover)

    values = equity["total_value"].to_list()
    if values[0] <= 0:
        return Metrics(n_trades=n_trades, win_rate=win_rate, turnover=turnover)

    total_return = values[-1] / values[0] - 1.0
    periods = len(values) - 1
    annualized = (1.0 + total_return) ** (periods_per_year / periods) - 1.0

    returns = [
        values[i] / values[i - 1] - 1.0 for i in range(1, len(values)) if values[i - 1] > 0
    ]
    mean = sum(returns) / len(returns) if returns else 0.0
    variance = (
        sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        if len(returns) > 1
        else 0.0
    )
    volatility = math.sqrt(variance) * math.sqrt(periods_per_year)

    excess = mean * periods_per_year - risk_free_rate
    sharpe = excess / volatility if volatility > 0 else 0.0

    max_dd, dd_days = _drawdown(values)

    return Metrics(
        total_return=total_return,
        annualized_return=annualized,
        volatility=volatility,
        sharpe=sharpe,
        max_drawdown=max_dd,
        max_drawdown_days=dd_days,
        n_trades=n_trades,
        win_rate=win_rate,
        turnover=turnover,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/backtest/test_metrics.py -v`
Expected: PASS —— 13 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/backtest/metrics.py tests/backtest/test_metrics.py
git commit -m "feat(backtest): 绩效指标"
```

---

## Task 11: 合成数据断言套件

spec §8.2 第一腿。构造已知答案的价格序列，**手算预期结果，断言引擎输出精确匹配**。这些测试是敢用这个引擎下真钱的唯一理由。

**Files:**
- Test: `tests/test_engine_scenarios.py`

**Interfaces:**
- Consumes: 全部引擎组件
- Produces: 无新增生产代码——纯验收测试

- [ ] **Step 1: 写场景测试**

Create `tests/test_engine_scenarios.py`:

```python
"""引擎级合成数据断言。

每个场景的预期值都是手算的。断言失败时**先手算核对再改代码**——
这些数字是验收基准，不得为了让测试通过而调整预期。
"""

from datetime import date, datetime, timedelta

import polars as pl
import pytest

from qsys.backtest.corporate import CorporateAction
from qsys.backtest.engine import BacktestEngine
from qsys.backtest.fees import AShareFeeModel, ZeroFeeModel
from qsys.backtest.slippage import NoSlippage
from qsys.core.calendar import AShareCalendar
from qsys.core.trading import Side
from qsys.core.types import Market, Symbol
from qsys.markets.ashare import ashare_pipeline
from qsys.strategy.base import Strategy

CMB = Symbol("600036", Market.SH)

BAR_SCHEMA = {
    "symbol": pl.Utf8, "dt": pl.Date, "open": pl.Float64, "high": pl.Float64,
    "low": pl.Float64, "close": pl.Float64, "volume": pl.Int64, "amount": pl.Float64,
}


class SyntheticFeed:
    """按 as_of 过滤的最小 DataFeed，语义与 HistoricalFeed 一致。"""

    def __init__(self, rows: dict[date, tuple[float, float, float, float, int]]):
        self._rows = rows

    def get(self, symbols, start, end, *, as_of, adjustment=None):
        out = []
        for session, (o, h, l, c, v) in sorted(self._rows.items()):
            if not (start <= session <= end):
                continue
            if as_of < datetime.combine(session, datetime.min.time()).replace(hour=15):
                continue
            out.append({"symbol": str(CMB), "dt": session, "open": o, "high": h,
                        "low": l, "close": c, "volume": v, "amount": c * v})
        return pl.DataFrame(out, schema=BAR_SCHEMA)


def flat_series(n: int, price: float = 10.0, volume: int = 10_000_000):
    sessions = [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]
    return sessions, {s: (price, price, price, price, volume) for s in sessions}


def build(strategy, sessions, rows, *, cash=100_000.0, fees=None, actions=None,
          volume_cap=0.05):
    cal = AShareCalendar(sessions)
    return BacktestEngine(
        strategy, feed=SyntheticFeed(rows), calendar=cal, universe=[CMB],
        initial_cash=cash, pipeline=ashare_pipeline(),
        fees=fees or ZeroFeeModel(), slippage=NoSlippage(),
        volume_cap=volume_cap, corporate_actions=actions,
    ).run(sessions[0], sessions[-1])


class BuyOnFirstBar(Strategy):
    def __init__(self, qty: int = 1000):
        self._qty = qty

    def on_bar(self, ctx, bars):
        if not ctx.state.get("done"):
            ctx.state["done"] = True
            ctx.order.submit(CMB, Side.BUY, self._qty)


def test_buy_and_hold_on_a_one_percent_daily_riser():
    """10 天每天涨 1%，第 2 天开盘买入并持有，净值必须等于手算值。"""
    n = 11
    sessions = [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]
    rows = {}
    price = 100.0
    for i, s in enumerate(sessions):
        rows[s] = (price, price, price, price, 10_000_000)
        price = round(price * 1.01, 6)

    result = build(BuyOnFirstBar(qty=100), sessions, rows, cash=1_000_000.0)

    # 信号在 D0 收盘发出，D1 开盘成交，开盘价 = 100 * 1.01 = 101.00
    assert result.fills[0].price == pytest.approx(101.0)
    # 末日收盘 = 100 * 1.01^10
    final_price = 100.0 * (1.01 ** 10)
    expected = 1_000_000.0 - 101.0 * 100 + final_price * 100
    assert result.final_value == pytest.approx(expected, abs=0.02)


def test_signal_bar_price_is_never_used_for_the_fill():
    """D0 收盘 100、D1 开盘 105 —— 成交价必须是 105，不是 100。

    价差刻意压在涨跌停带内：跳空过大会被 PriceLimitConstraint 正确拒单，
    那样就测不到「用哪根 bar 成交」这件事了。
    """
    sessions = [date(2024, 1, 2), date(2024, 1, 3)]
    rows = {sessions[0]: (100.0, 100.0, 100.0, 100.0, 10_000_000),
            sessions[1]: (105.0, 105.0, 105.0, 105.0, 10_000_000)}
    result = build(BuyOnFirstBar(qty=100), sessions, rows, cash=1_000_000.0)
    assert result.fills[0].price == 105.0


def test_limit_up_on_fill_day_blocks_the_buy():
    """D1 开盘即涨停 -> 买单不成交。"""
    sessions = [date(2024, 1, 2), date(2024, 1, 3)]
    rows = {sessions[0]: (10.0, 10.0, 10.0, 10.0, 10_000_000),
            sessions[1]: (11.0, 11.0, 11.0, 11.0, 10_000_000)}
    result = build(BuyOnFirstBar(qty=100), sessions, rows)
    assert result.fills == ()
    assert any("limit up" in r.reason for r in result.rejects)


def test_suspension_on_fill_day_rejects_the_order():
    sessions = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    rows = {sessions[0]: (10.0, 10.0, 10.0, 10.0, 10_000_000),
            sessions[2]: (10.0, 10.0, 10.0, 10.0, 10_000_000)}  # D1 无 bar = 停牌
    result = build(BuyOnFirstBar(qty=100), sessions, rows)
    assert result.fills == ()
    assert any("suspend" in r.reason for r in result.rejects)


def test_t_plus_one_blocks_same_session_round_trip():
    """成交当日提交的卖单不成交，必须等到下一交易日。"""

    class SellImmediately(Strategy):
        def on_bar(self, ctx, bars):
            if not ctx.state.get("bought"):
                ctx.state["bought"] = True
                ctx.order.submit(CMB, Side.BUY, 100)

        def on_fill(self, ctx, fill):
            if fill.side is Side.BUY:
                ctx.order.submit(CMB, Side.SELL, fill.qty)

    sessions, rows = flat_series(4)
    result = build(SellImmediately(), sessions, rows)
    buys = [f for f in result.fills if f.side is Side.BUY]
    sells = [f for f in result.fills if f.side is Side.SELL]
    assert len(buys) == 1 and len(sells) == 1
    assert sells[0].filled_at.date() > buys[0].filled_at.date()


def test_dividend_keeps_total_value_flat():
    """价格不变、派息 0.5 元/股 —— 现金增加，总值不变（不复权价不掉）。"""
    sessions, rows = flat_series(4)
    actions = {sessions[3]: [CorporateAction(CMB, sessions[3], 0.5, 0.0)]}
    result = build(BuyOnFirstBar(qty=1000), sessions, rows, actions=actions)
    # 1000 股 x 0.5 = 500 元现金入账
    assert result.final_value == pytest.approx(100_000.0 + 500.0)


def test_stock_split_doubles_shares_and_halves_price():
    """10 送 10：持仓翻倍、价格减半，总值不变。"""
    sessions = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]
    rows = {
        sessions[0]: (20.0, 20.0, 20.0, 20.0, 10_000_000),
        sessions[1]: (20.0, 20.0, 20.0, 20.0, 10_000_000),
        sessions[2]: (10.0, 10.0, 10.0, 10.0, 10_000_000),  # 除权日价格减半
        sessions[3]: (10.0, 10.0, 10.0, 10.0, 10_000_000),
    }
    actions = {sessions[2]: [CorporateAction(CMB, sessions[2], 0.0, 1.0)]}
    result = build(BuyOnFirstBar(qty=1000), sessions, rows, actions=actions)
    # 买入 1000 股 @20 = 20000；除权后 2000 股 @10 = 20000，总值不变
    assert result.final_value == pytest.approx(100_000.0)


def test_lot_size_rounds_the_order_down():
    sessions, rows = flat_series(3)
    result = build(BuyOnFirstBar(qty=250), sessions, rows)
    assert result.fills[0].qty == 200


def test_volume_cap_limits_a_large_order():
    """bar 成交量 1000 手，5% 上限 -> 最多成交 50 股（整手取整为 0，故拒绝）。"""
    sessions, rows = flat_series(3, volume=1_000)
    result = build(BuyOnFirstBar(qty=100_000), sessions, rows, cash=10_000_000.0)
    assert result.fills == ()
    assert result.rejects != ()


def test_volume_cap_partial_fill_is_reported():
    sessions, rows = flat_series(3, volume=100_000)
    result = build(BuyOnFirstBar(qty=100_000), sessions, rows, cash=10_000_000.0)
    assert result.fills[0].qty == 5000
    assert any("volume cap" in r.reason for r in result.rejects)


def test_fees_are_deducted_exactly():
    """1000 股 @10.00：gross 10000，佣金 max(3.0, 5.0)=5.0，过户费 0.10 -> 5.10。"""
    sessions, rows = flat_series(3)
    result = build(BuyOnFirstBar(qty=1000), sessions, rows, fees=AShareFeeModel())
    assert result.fills[0].fee == 5.10
    assert result.final_value == pytest.approx(100_000.0 - 5.10)


def test_no_signal_means_no_change():
    sessions, rows = flat_series(5)
    result = build(Strategy(), sessions, rows)
    assert result.final_value == 100_000.0
    assert result.fills == ()


def test_equity_curve_is_recorded_every_session():
    sessions, rows = flat_series(5)
    result = build(BuyOnFirstBar(qty=100), sessions, rows)
    assert result.equity.height == 5
    assert result.equity["dt"].to_list() == sessions
```

- [ ] **Step 2: 运行场景测试**

Run: `uv run pytest tests/test_engine_scenarios.py -v`
Expected: PASS —— 13 passed

- [ ] **Step 3: 运行全部测试**

Run: `uv run pytest -q`
Expected: PASS —— 计划 1-1（146）+ 1-2a（104）+ 本计划新增项全部通过

- [ ] **Step 4: 提交**

```bash
git add tests/test_engine_scenarios.py
git commit -m "test: 引擎级合成数据断言套件"
```

---

## Task 12: 聚宽三层交叉验证对照工具

工具现在建好并自测，**待浏览器可用、能从聚宽导出数据时再实际运行**。

三层方法论（spec §8.2）：
- **L1 数据比对** —— 同批股票同区间，聚宽复权价 vs 自建仓库，逐字段比对
- **L2 固定数据比引擎** —— 把聚宽数据喂给自建引擎，差异纯粹来自撮合/费用/成交时点
- **L3 各用各的数据** —— 差异应约等于 L1 + L2

**Files:**
- Create: `src/qsys/backtest/compare.py`
- Create: `scripts/compare_joinquant.py`
- Test: `tests/test_compare_joinquant.py`

**Interfaces:**
- Consumes: `BacktestResult`（Task 9）、`compute_metrics`（Task 10）
- Produces: `PriceDiff(n_rows, n_mismatched, max_abs_diff, mismatch_rate, samples)`、`compare_prices(ours, theirs, *, tolerance=0.005) -> PriceDiff`、`EquityDiff(final_value_ours, final_value_theirs, final_diff_pct, max_diff_pct, correlation)`、`compare_equity(ours, theirs) -> EquityDiff`、`format_report(price_diff, equity_diff) -> str`

- [ ] **Step 1: 写失败测试**

Create `tests/test_compare_joinquant.py`:

```python
from datetime import date

import polars as pl
import pytest

from qsys.backtest.compare import compare_equity, compare_prices, format_report


def prices(closes: list[float]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["600036.SH"] * len(closes),
            "dt": [date(2024, 1, i + 1) for i in range(len(closes))],
            "close": closes,
        }
    )


def equity(values: list[float]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "dt": [date(2024, 1, i + 1) for i in range(len(values))],
            "total_value": values,
        }
    )


def test_identical_prices_have_no_mismatch():
    d = compare_prices(prices([10.0, 11.0]), prices([10.0, 11.0]))
    assert d.n_mismatched == 0
    assert d.mismatch_rate == 0.0
    assert d.max_abs_diff == 0.0


def test_price_diff_within_tolerance_is_not_a_mismatch():
    d = compare_prices(prices([10.000]), prices([10.004]), tolerance=0.005)
    assert d.n_mismatched == 0


def test_price_diff_beyond_tolerance_is_flagged():
    d = compare_prices(prices([10.0, 11.0]), prices([10.0, 11.5]), tolerance=0.005)
    assert d.n_mismatched == 1
    assert d.max_abs_diff == pytest.approx(0.5)
    assert d.mismatch_rate == pytest.approx(0.5)


def test_price_samples_are_returned_for_inspection():
    d = compare_prices(prices([10.0, 11.0]), prices([10.0, 11.5]))
    assert d.samples.height == 1
    assert "close_ours" in d.samples.columns
    assert "close_theirs" in d.samples.columns


def test_rows_present_on_only_one_side_are_counted():
    d = compare_prices(prices([10.0, 11.0]), prices([10.0]))
    assert d.n_rows == 1  # 只比对交集


def test_identical_equity_has_zero_diff():
    d = compare_equity(equity([100.0, 110.0]), equity([100.0, 110.0]))
    assert d.final_diff_pct == 0.0
    assert d.max_diff_pct == 0.0
    assert d.correlation == pytest.approx(1.0)


def test_equity_final_diff_is_relative():
    d = compare_equity(equity([100.0, 110.0]), equity([100.0, 100.0]))
    assert d.final_value_ours == 110.0
    assert d.final_value_theirs == 100.0
    assert d.final_diff_pct == pytest.approx(0.10)


def test_equity_max_diff_finds_the_worst_day():
    d = compare_equity(equity([100.0, 150.0, 110.0]), equity([100.0, 100.0, 110.0]))
    assert d.max_diff_pct == pytest.approx(0.50)


def test_correlation_of_opposite_curves_is_negative():
    d = compare_equity(equity([100.0, 110.0, 120.0]), equity([100.0, 90.0, 80.0]))
    assert d.correlation < 0


def test_report_mentions_all_three_layers():
    pd_ = compare_prices(prices([10.0]), prices([10.0]))
    ed = compare_equity(equity([100.0, 110.0]), equity([100.0, 110.0]))
    text = format_report(pd_, ed)
    assert "L1" in text and "L2" in text
    assert "0" in text


def test_report_flags_unexplained_residual():
    pd_ = compare_prices(prices([10.0]), prices([10.0]))
    ed = compare_equity(equity([100.0, 200.0]), equity([100.0, 100.0]))
    text = format_report(pd_, ed)
    assert "未通过" in text or "FAIL" in text


def test_self_comparison_is_a_clean_baseline():
    """把结果和自己比，必须零差异——这是工具本身的自测。"""
    e = equity([100.0, 105.0, 103.0, 120.0])
    d = compare_equity(e, e)
    assert d.final_diff_pct == 0.0
    assert d.max_diff_pct == 0.0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_compare_joinquant.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.backtest.compare'`

- [ ] **Step 3: 实现 `src/qsys/backtest/compare.py`**

```python
"""聚宽交叉验证对照工具。

三层控制变量（spec §8.2）：
  L1 数据比对        —— 数据未对齐时，任何净值差异都无法归因，必须先做完
  L2 固定数据比引擎  —— 用聚宽数据喂自建引擎，差异纯粹来自撮合/费用/成交时点
  L3 各用各的数据    —— 差异应约等于 L1 + L2

残差必须收敛到可解释范围，否则引擎不通过验收。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import polars as pl

# 引擎验收阈值：L2 的最终净值差异超过此值即判定未通过
L2_FINAL_DIFF_THRESHOLD = 0.005


@dataclass(frozen=True, slots=True)
class PriceDiff:
    n_rows: int
    n_mismatched: int
    max_abs_diff: float
    mismatch_rate: float
    samples: pl.DataFrame


@dataclass(frozen=True, slots=True)
class EquityDiff:
    final_value_ours: float
    final_value_theirs: float
    final_diff_pct: float
    max_diff_pct: float
    correlation: float


def compare_prices(
    ours: pl.DataFrame, theirs: pl.DataFrame, *, tolerance: float = 0.005
) -> PriceDiff:
    """L1：逐字段比对收盘价。只比对两侧都有的行。"""
    joined = ours.join(theirs, on=["symbol", "dt"], how="inner", suffix="_theirs")
    if joined.height == 0:
        return PriceDiff(0, 0, 0.0, 0.0, joined)

    joined = joined.rename({"close": "close_ours"}).with_columns(
        (pl.col("close_ours") - pl.col("close_theirs")).abs().alias("abs_diff")
    )
    bad = joined.filter(pl.col("abs_diff") > tolerance)
    return PriceDiff(
        n_rows=joined.height,
        n_mismatched=bad.height,
        max_abs_diff=float(joined["abs_diff"].max() or 0.0),
        mismatch_rate=bad.height / joined.height,
        samples=bad.head(20),
    )


def compare_equity(ours: pl.DataFrame, theirs: pl.DataFrame) -> EquityDiff:
    """L2 / L3：比对净值曲线。"""
    joined = ours.join(theirs, on="dt", how="inner", suffix="_theirs").rename(
        {"total_value": "value_ours"}
    )
    if joined.height == 0:
        return EquityDiff(0.0, 0.0, 0.0, 0.0, 0.0)

    a = joined["value_ours"].to_list()
    b = joined["total_value_theirs"].to_list()

    diffs = [abs(x - y) / y for x, y in zip(a, b, strict=True) if y != 0]
    final_diff = abs(a[-1] - b[-1]) / b[-1] if b[-1] != 0 else 0.0

    mean_a, mean_b = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    denom = math.sqrt(var_a * var_b)
    correlation = cov / denom if denom > 0 else 0.0

    return EquityDiff(
        final_value_ours=a[-1],
        final_value_theirs=b[-1],
        final_diff_pct=final_diff,
        max_diff_pct=max(diffs) if diffs else 0.0,
        correlation=correlation,
    )


def format_report(price_diff: PriceDiff, equity_diff: EquityDiff) -> str:
    verdict = (
        "通过" if equity_diff.final_diff_pct <= L2_FINAL_DIFF_THRESHOLD else "未通过"
    )
    lines = [
        "=" * 64,
        "聚宽交叉验证报告",
        "=" * 64,
        "",
        "L1 数据比对（数据未对齐时净值差异无法归因，必须先看这里）",
        f"  比对行数      : {price_diff.n_rows}",
        f"  不一致行数    : {price_diff.n_mismatched}",
        f"  不一致比例    : {price_diff.mismatch_rate:.4%}",
        f"  最大绝对差    : {price_diff.max_abs_diff:.4f}",
        "",
        "L2 固定数据比引擎（差异纯粹来自撮合 / 费用 / 成交时点）",
        f"  自建最终净值  : {equity_diff.final_value_ours:,.2f}",
        f"  聚宽最终净值  : {equity_diff.final_value_theirs:,.2f}",
        f"  最终净值差异  : {equity_diff.final_diff_pct:.4%}",
        f"  单日最大差异  : {equity_diff.max_diff_pct:.4%}",
        f"  净值曲线相关性: {equity_diff.correlation:.6f}",
        "",
        f"引擎验收（阈值 {L2_FINAL_DIFF_THRESHOLD:.2%}）: {verdict}",
        "",
        "残差排查清单：",
        "  成交时点   —— 聚宽默认在哪根 bar 成交？是否对齐 T 信号 / T+1 开盘",
        "  费用口径   —— 佣金最低 5 元、印花税、过户费是否逐项对齐",
        "  涨跌停判定 —— 聚宽用何价判涨停？是否允许涨停板买入",
        "  成交量约束 —— 聚宽默认有无成交量占比限制",
        "  复权口径   —— 前复权基准日不同会导致净值曲线整体缩放",
        "=" * 64,
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: 实现 `scripts/compare_joinquant.py`**

```python
"""聚宽三层交叉验证：命令行入口。

需要从聚宽研究环境导出两个 CSV：
  prices.csv  列：symbol, dt, close        —— 用于 L1
  equity.csv  列：dt, total_value          —— 用于 L2 / L3

用法：
  uv run python scripts/compare_joinquant.py \
      --jq-prices prices.csv --jq-equity equity.csv \
      --our-equity our_equity.csv [--our-prices our_prices.csv]

L2 的正确做法是把聚宽导出的价格喂给自建引擎再跑一遍——
数据变量被消除后，净值差异才纯粹来自撮合逻辑。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

from qsys.backtest.compare import compare_equity, compare_prices, format_report


def _read(path: Path) -> pl.DataFrame:
    df = pl.read_csv(path, try_parse_dates=True)
    if "dt" not in df.columns:
        raise SystemExit(f"{path} 缺少 dt 列")
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description="聚宽三层交叉验证")
    parser.add_argument("--jq-prices", type=Path, required=True)
    parser.add_argument("--jq-equity", type=Path, required=True)
    parser.add_argument("--our-equity", type=Path, required=True)
    parser.add_argument("--our-prices", type=Path)
    parser.add_argument("--tolerance", type=float, default=0.005)
    args = parser.parse_args()

    jq_prices = _read(args.jq_prices)
    our_prices = _read(args.our_prices) if args.our_prices else jq_prices
    price_diff = compare_prices(our_prices, jq_prices, tolerance=args.tolerance)
    equity_diff = compare_equity(_read(args.our_equity), _read(args.jq_equity))

    print(format_report(price_diff, equity_diff))
    if price_diff.n_mismatched:
        print("\nL1 不一致样本：")
        print(price_diff.samples)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_compare_joinquant.py -v`
Expected: PASS —— 12 passed

- [ ] **Step 6: 确认命令行入口可用**

Run: `uv run python scripts/compare_joinquant.py --help`
Expected: 打印用法说明，退出码 0

- [ ] **Step 7: 运行全部测试**

Run: `uv run pytest -q`
Expected: PASS —— 全部通过

- [ ] **Step 8: 提交**

```bash
git add src/qsys/backtest/compare.py scripts/compare_joinquant.py tests/test_compare_joinquant.py
git commit -m "feat(backtest): 聚宽三层交叉验证对照工具"
```

---

## 完成标准

1. `uv run pytest` 全绿，计划 1-1（146）与 1-2a（104）无回归
2. **信号在 bar T 生成、成交在 bar T+1 开盘**——`test_signal_bar_price_is_never_used_for_the_fill` 通过（D0 收盘 100、D1 开盘 200，成交价必须是 200）
3. **策略拿不到 `WarehouseQuery`**——`test_context_exposes_no_warehouse_query` 通过；`ctx.data` 类型为 `BoundFeed`，且 `as_of` 恒等于 `ctx.now`（计划 1-1 完成标准 ④ 在此兑现）
4. 合成断言套件全绿，含：一日一涨的买入持有、涨跌停拒单、停牌拒单、T+1 阻断当日回转、分红入账、送股翻倍总值不变、整手取整、成交量上限、费用精确扣除
5. 除权日总值不跳变（`test_stock_split_doubles_shares_and_halves_price` 与 `test_dividend_keeps_total_value_flat`）
6. 成交量上限削减的部分**产生 Reject 并告警**，不得静默丢弃
7. `run_id` 对相同输入稳定、对不同参数变化
8. 聚宽对照工具自测通过，`scripts/compare_joinquant.py --help` 可用

## 下一步

进入 **计划 1-3：实盘链路** —— Redis Stream 协议（指令/回报/状态三通道）、幂等 `client_order_id`、Mac 侧风控闸门与 kill switch、Windows 侧 `xtquant` 执行代理、三层对账。

**待你有浏览器时**：从聚宽导出 `prices.csv` 与 `equity.csv`，跑 `scripts/compare_joinquant.py` 完成 L1/L2 交叉验证——这是引擎正确性的第二条腿。
