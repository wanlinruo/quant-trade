# 一期 1-2a：交易内核 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立市场中立的交易内核——回测时钟、账户与持仓记账、以及可插拔的交易约束系统（整手、不可做空、T+1 交收、涨跌停、停牌、可用资金），并用 A 股预设把它们组装起来。产出：给定行情与订单意图，账户状态推演正确、A 股交易规则强制正确。

**Architecture:** `core/` 只认识「约束」这个概念，不认识「涨跌停 10%」这条规则——所有市场参数由 `markets/ashare.py` 注入。约束以流水线方式串联，每条只能把数量**夹得更小**，绝不放大；最终的 `PipelineResult` 保留每条约束的判定，供结构化日志逐条追溯。`Portfolio` 提供冻结/解冻机制但不知道 T+1 是什么，解冻时机由调用方决定——这是多市场预留的关键。

**Tech Stack:** Python 3.11+ · uv · pytest · hypothesis · structlog

**Spec:** `docs/superpowers/specs/2026-09-06-personal-quant-system-design.md`（实现 §5.1 的 Clock、§5.3 交易约束、§5.4 下单转换中的数量夹取部分）

## Global Constraints

- Python **3.11+**（spec §11）
- `core/` 不得出现任何具体市场的常量或规则（涨跌停幅度、T+1、整手数），一律由构造参数注入；A 股具体数值只允许出现在 `markets/ashare.py`（spec §3.4）
- 约束在两处共用同一份代码：回测撮合前检查与实盘风控下单前检查。**任何约束都不得依赖「当前在回测中」这类信息**（spec §5.3）
- 约束只能把数量**夹小或拒绝**，绝不放大；`allowed_qty == 0` 表示拒绝（spec §5.3）
- 现金金额每次变动都取整到**分**——券商按分记账，逐笔取整比全程高精度更贴近真实账户，也让合成断言可以精确到分
- 单元测试不得发起网络请求，不得读取仓库文件
- 日志使用 **structlog** 结构化输出（spec §9）
- 环境隔离：本机 shell 可能激活着其它项目的 venv。所有命令前置
  `unset VIRTUAL_ENV; export PATH="$HOME/.local/bin:$(echo "$PATH" | tr ':' '\n' | grep -v 'project-002/.venv' | paste -sd: -)"`

---

## File Structure

```
src/qsys/core/
  trading.py        Side / OrderType / OrderIntent / Order / Fill / to_cents
  clock.py          Phase / Clock 抽象 / BacktestClock
  portfolio.py      Position / Portfolio（记账 + 冻结解冻机制）
  constraints.py    Constraint / Verdict / ConstraintContext / ConstraintPipeline
                    LotSizeConstraint / NoShortConstraint / SettlementConstraint
                    PriceLimitConstraint / SuspensionConstraint / CashConstraint
src/qsys/markets/
  __init__.py
  ashare.py         A 股预设：涨跌停表、整手数、约束组装顺序
tests/core/
  test_trading.py
  test_clock.py
  test_portfolio.py
  test_constraints_basic.py      整手 / 不可做空 / 可用资金
  test_constraints_market.py     交收 / 涨跌停 / 停牌
  test_constraint_pipeline.py
tests/markets/
  test_ashare.py                 A 股预设组装
tests/
  test_kernel_scenarios.py       合成场景端到端断言
```

**责任边界**：`trading.py` 只有值类型与取整，无行为；`portfolio.py` 只管记账，不判断能否交易；`constraints.py` 只做判定，不修改账户；`markets/ashare.py` 只提供参数与组装顺序，不含逻辑。

---

## Task 1: 交易值类型与现金取整

**Files:**
- Create: `src/qsys/core/trading.py`
- Test: `tests/core/test_trading.py`

**Interfaces:**
- Consumes: `Symbol`（计划 1-1 `qsys.core.types`）
- Produces: `Side` (StrEnum: BUY/SELL)、`OrderType` (StrEnum: MARKET/LIMIT)、`to_cents(float) -> float`、`OrderIntent(symbol, side, qty, order_type=MARKET, limit_price=None)` 带 `with_qty(int) -> OrderIntent`、`Order(order_id, symbol, side, qty, order_type, limit_price, created_at)`、`Fill(order_id, symbol, side, qty, price, fee, filled_at)` 带属性 `gross: float` 与 `cash_delta: float`

- [ ] **Step 1: 写失败测试**

Create `tests/core/test_trading.py`:

```python
from datetime import datetime

import pytest

from qsys.core.trading import Fill, Order, OrderIntent, OrderType, Side, to_cents
from qsys.core.types import Market, Symbol

CMB = Symbol("600036", Market.SH)
NOW = datetime(2024, 1, 2, 15, 0)


def test_to_cents_rounds_half_up():
    assert to_cents(1.005) == 1.01
    assert to_cents(1.004) == 1.00


def test_to_cents_rounds_negative_away_from_zero():
    assert to_cents(-1.005) == -1.01


def test_to_cents_is_idempotent():
    assert to_cents(to_cents(10.126)) == 10.13


def test_to_cents_handles_large_amounts():
    assert to_cents(1_234_567.891) == 1_234_567.89


def test_order_intent_rejects_non_positive_qty():
    with pytest.raises(ValueError, match="qty must be positive"):
        OrderIntent(CMB, Side.BUY, 0)


def test_limit_order_requires_limit_price():
    with pytest.raises(ValueError, match="limit order requires limit_price"):
        OrderIntent(CMB, Side.BUY, 100, OrderType.LIMIT)


def test_market_order_needs_no_limit_price():
    intent = OrderIntent(CMB, Side.BUY, 100)
    assert intent.order_type is OrderType.MARKET
    assert intent.limit_price is None


def test_with_qty_returns_new_intent():
    intent = OrderIntent(CMB, Side.BUY, 150)
    clamped = intent.with_qty(100)
    assert clamped.qty == 100
    assert intent.qty == 150
    assert clamped.symbol == intent.symbol
    assert clamped.side is intent.side


def test_intent_is_frozen():
    intent = OrderIntent(CMB, Side.BUY, 100)
    with pytest.raises(Exception):
        intent.qty = 200


def test_fill_gross_is_price_times_qty_in_cents():
    fill = Fill("o1", CMB, Side.BUY, 100, 27.583, 5.0, NOW)
    assert fill.gross == 2758.30


def test_buy_cash_delta_is_negative_and_includes_fee():
    fill = Fill("o1", CMB, Side.BUY, 100, 27.58, 5.0, NOW)
    assert fill.cash_delta == -2763.00


def test_sell_cash_delta_is_positive_and_net_of_fee():
    fill = Fill("o1", CMB, Side.SELL, 100, 27.58, 5.0, NOW)
    assert fill.cash_delta == 2753.00


def test_order_carries_creation_time():
    order = Order("o1", CMB, Side.BUY, 100, OrderType.MARKET, None, NOW)
    assert order.created_at == NOW
    assert order.order_id == "o1"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/core/test_trading.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.core.trading'`

- [ ] **Step 3: 实现 `src/qsys/core/trading.py`**

```python
"""交易值类型与现金取整。只有值，没有行为。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from qsys.core.types import Symbol

_CENT = Decimal("0.01")


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


def to_cents(amount: float) -> float:
    """现金金额取整到分。

    券商按分记账，每笔现金流动逐笔取整比全程高精度更贴近真实账户，
    也让合成数据断言可以精确到分。用 Decimal 仅为取得正确的四舍五入
    （float 的 round() 在 .5 上是银行家舍入）。
    """
    return float(Decimal(str(amount)).quantize(_CENT, rounding=ROUND_HALF_UP))


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """策略发出的下单意图，尚未经过约束检查。"""

    symbol: Symbol
    side: Side
    qty: int
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise ValueError(f"qty must be positive: {self.qty}")
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit order requires limit_price")

    def with_qty(self, qty: int) -> OrderIntent:
        return replace(self, qty=qty)


@dataclass(frozen=True, slots=True)
class Order:
    """经约束检查后提交给 Broker 的订单。"""

    order_id: str
    symbol: Symbol
    side: Side
    qty: int
    order_type: OrderType
    limit_price: float | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: str
    symbol: Symbol
    side: Side
    qty: int
    price: float
    fee: float
    filled_at: datetime

    @property
    def gross(self) -> float:
        """成交金额（不含费用）。"""
        return to_cents(self.price * self.qty)

    @property
    def cash_delta(self) -> float:
        """对现金的影响：买入为负、卖出为正，均已含费用。"""
        if self.side is Side.BUY:
            return to_cents(-(self.gross + self.fee))
        return to_cents(self.gross - self.fee)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/core/test_trading.py -v`
Expected: PASS —— 13 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/core/trading.py tests/core/test_trading.py
git commit -m "feat(core): 交易值类型与现金取整"
```

---

## Task 2: 回测时钟

时钟决定事件从哪来、时间怎么推进。日线回测下每个交易日分三个相位：盘前（可见数据截止前一交易日收盘）、bar（当日收盘数据可见）、收盘后。

**Files:**
- Create: `src/qsys/core/clock.py`
- Test: `tests/core/test_clock.py`

**Interfaces:**
- Consumes: `TradingCalendar`（计划 1-1 `qsys.core.calendar`）
- Produces: `Phase` (StrEnum: SESSION_START/BAR/SESSION_END)、`Clock` (ABC: 属性 `now: datetime`)、`BacktestClock(calendar, start, end)` 带 `ticks() -> Iterator[tuple[datetime, Phase]]`、`now`、`session: date | None`；类常量 `PRE_OPEN = time(9, 15)`、`POST_CLOSE = time(15, 30)`

- [ ] **Step 1: 写失败测试**

Create `tests/core/test_clock.py`:

```python
from datetime import date, datetime

import pytest

from qsys.core.calendar import AShareCalendar
from qsys.core.clock import BacktestClock, Phase

SESSIONS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
CAL = AShareCalendar(SESSIONS)


@pytest.fixture
def clock():
    return BacktestClock(CAL, SESSIONS[0], SESSIONS[-1])


def test_ticks_yield_three_phases_per_session(clock):
    ticks = list(clock.ticks())
    assert len(ticks) == 9


def test_phase_order_within_a_session(clock):
    phases = [phase for _, phase in clock.ticks()][:3]
    assert phases == [Phase.SESSION_START, Phase.BAR, Phase.SESSION_END]


def test_tick_times_match_phase(clock):
    ticks = list(clock.ticks())[:3]
    assert ticks[0][0] == datetime(2024, 1, 2, 9, 15)
    assert ticks[1][0] == datetime(2024, 1, 2, 15, 0)
    assert ticks[2][0] == datetime(2024, 1, 2, 15, 30)


def test_bar_phase_time_equals_calendar_close(clock):
    bar_times = [t for t, phase in clock.ticks() if phase is Phase.BAR]
    assert bar_times[0] == CAL.close_time(SESSIONS[0])


def test_now_advances_with_ticks(clock):
    seen = []
    for tick_time, _ in clock.ticks():
        seen.append((tick_time, clock.now))
    assert all(t == n for t, n in seen)


def test_now_before_first_tick_raises():
    clock = BacktestClock(CAL, SESSIONS[0], SESSIONS[-1])
    with pytest.raises(RuntimeError, match="clock has not started"):
        _ = clock.now


def test_session_tracks_current_trading_day(clock):
    sessions = []
    for _, phase in clock.ticks():
        if phase is Phase.BAR:
            sessions.append(clock.session)
    assert sessions == SESSIONS


def test_range_is_clipped_to_calendar(clock):
    narrow = BacktestClock(CAL, date(2024, 1, 3), date(2024, 1, 3))
    assert len({t.date() for t, _ in narrow.ticks()}) == 1


def test_empty_range_yields_nothing():
    empty = BacktestClock(CAL, date(2023, 1, 1), date(2023, 12, 31))
    assert list(empty.ticks()) == []


def test_ticks_are_monotonic(clock):
    times = [t for t, _ in clock.ticks()]
    assert times == sorted(times)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/core/test_clock.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.core.clock'`

- [ ] **Step 3: 实现 `src/qsys/core/clock.py`**

```python
"""时钟：决定事件从哪来、时间怎么推进。

策略代码对 BacktestClock 与（将来的）LiveClock 完全无感——实盘跑的是
回测验证过的同一条代码路径。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import date, datetime, time
from enum import StrEnum

from qsys.core.calendar import TradingCalendar


class Phase(StrEnum):
    SESSION_START = "session_start"  # 盘前：当日 bar 尚不可见
    BAR = "bar"  # 收盘：当日 bar 可见
    SESSION_END = "session_end"  # 收盘后：记录与复盘


class Clock(ABC):
    @property
    @abstractmethod
    def now(self) -> datetime: ...


class BacktestClock(Clock):
    PRE_OPEN = time(9, 15)
    POST_CLOSE = time(15, 30)

    def __init__(self, calendar: TradingCalendar, start: date, end: date) -> None:
        self._calendar = calendar
        self._sessions = calendar.sessions_in_range(start, end)
        self._now: datetime | None = None
        self._session: date | None = None

    @property
    def now(self) -> datetime:
        if self._now is None:
            raise RuntimeError("clock has not started; iterate ticks() first")
        return self._now

    @property
    def session(self) -> date | None:
        return self._session

    def ticks(self) -> Iterator[tuple[datetime, Phase]]:
        for session in self._sessions:
            self._session = session
            for moment, phase in (
                (datetime.combine(session, self.PRE_OPEN), Phase.SESSION_START),
                (self._calendar.close_time(session), Phase.BAR),
                (datetime.combine(session, self.POST_CLOSE), Phase.SESSION_END),
            ):
                self._now = moment
                yield moment, phase
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/core/test_clock.py -v`
Expected: PASS —— 10 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/core/clock.py tests/core/test_clock.py
git commit -m "feat(core): 回测时钟与交易相位"
```

---

## Task 3: 持仓与账户记账

`Portfolio` 提供冻结/解冻机制但**不知道 T+1 是什么**——买入只增加 `total_qty`，`unfreeze()` 才把可卖数量补齐。解冻时机由调用方决定：T+1 市场在每个交易日开始时调用，T+0 市场可在每次成交后调用。这是多市场预留的关键。

**Files:**
- Create: `src/qsys/core/portfolio.py`
- Test: `tests/core/test_portfolio.py`

**Interfaces:**
- Consumes: `Symbol`（1-1）、`Fill` / `Side` / `to_cents`（Task 1）
- Produces: `Position(symbol, total_qty=0, available_qty=0, cost_basis=0.0)` 带属性 `avg_cost: float`、`is_empty: bool`；`Portfolio(initial_cash)` 带 `cash: float`、`initial_cash: float`、`positions: dict[Symbol, Position]`、`position(symbol) -> Position`、`apply_fill(fill) -> None`、`unfreeze() -> None`、`market_value(prices) -> float`、`total_value(prices) -> float`

- [ ] **Step 1: 写失败测试**

Create `tests/core/test_portfolio.py`:

```python
from datetime import datetime

import pytest

from qsys.core.portfolio import Portfolio, Position
from qsys.core.trading import Fill, Side
from qsys.core.types import Market, Symbol

CMB = Symbol("600036", Market.SH)
PAB = Symbol("000001", Market.SZ)
NOW = datetime(2024, 1, 2, 15, 0)


def buy(qty: int, price: float, fee: float = 5.0, symbol: Symbol = CMB) -> Fill:
    return Fill("o", symbol, Side.BUY, qty, price, fee, NOW)


def sell(qty: int, price: float, fee: float = 5.0, symbol: Symbol = CMB) -> Fill:
    return Fill("o", symbol, Side.SELL, qty, price, fee, NOW)


def test_new_portfolio_holds_cash_only():
    p = Portfolio(100_000.0)
    assert p.cash == 100_000.0
    assert p.initial_cash == 100_000.0
    assert p.positions == {}


def test_position_of_unheld_symbol_is_empty():
    p = Portfolio(100_000.0)
    pos = p.position(CMB)
    assert pos.total_qty == 0
    assert pos.available_qty == 0
    assert pos.is_empty is True


def test_buy_reduces_cash_by_gross_plus_fee():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58))
    assert p.cash == 100_000.0 - 2758.00 - 5.0


def test_buy_increases_total_but_not_available():
    """T+1 的物理基础：买入的股份未解冻前不可卖。"""
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58))
    pos = p.position(CMB)
    assert pos.total_qty == 100
    assert pos.available_qty == 0


def test_unfreeze_makes_all_shares_available():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58))
    p.unfreeze()
    assert p.position(CMB).available_qty == 100


def test_cost_basis_includes_fee():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58))
    assert p.position(CMB).cost_basis == 2763.00
    assert p.position(CMB).avg_cost == 27.63


def test_second_buy_accumulates_cost_basis():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 10.00, fee=5.0))
    p.apply_fill(buy(100, 20.00, fee=5.0))
    pos = p.position(CMB)
    assert pos.total_qty == 200
    assert pos.cost_basis == 3010.00
    assert pos.avg_cost == 15.05


def test_sell_increases_cash_net_of_fee():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58))
    p.unfreeze()
    cash_before = p.cash
    p.apply_fill(sell(100, 30.00))
    assert p.cash == cash_before + 3000.00 - 5.0


def test_sell_reduces_total_and_available():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(200, 27.58))
    p.unfreeze()
    p.apply_fill(sell(50, 30.00))
    pos = p.position(CMB)
    assert pos.total_qty == 150
    assert pos.available_qty == 150


def test_sell_reduces_cost_basis_proportionally():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(200, 10.00, fee=0.0))
    p.unfreeze()
    p.apply_fill(sell(50, 30.00, fee=0.0))
    assert p.position(CMB).cost_basis == 1500.00


def test_selling_everything_drops_the_position():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58))
    p.unfreeze()
    p.apply_fill(sell(100, 30.00))
    assert CMB not in p.positions


def test_selling_more_than_held_raises():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58))
    p.unfreeze()
    with pytest.raises(ValueError, match="cannot sell 200"):
        p.apply_fill(sell(200, 30.00))


def test_market_value_sums_positions():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58, symbol=CMB))
    p.apply_fill(buy(200, 10.00, symbol=PAB))
    assert p.market_value({CMB: 30.0, PAB: 11.0}) == 3000.0 + 2200.0


def test_market_value_requires_a_price_for_every_holding():
    """缺价必须报错——静默跳过会让组合市值被低估。"""
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58))
    with pytest.raises(KeyError, match="600036.SH"):
        p.market_value({})


def test_total_value_is_cash_plus_market_value():
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58))
    assert p.total_value({CMB: 30.0}) == p.cash + 3000.0


def test_empty_portfolio_market_value_is_zero():
    assert Portfolio(100_000.0).market_value({}) == 0.0


def test_avg_cost_of_empty_position_is_zero():
    assert Position(CMB).avg_cost == 0.0


def test_position_returned_is_a_snapshot_not_a_handle():
    """外部拿到的持仓不应能改写账户内部状态。"""
    p = Portfolio(100_000.0)
    p.apply_fill(buy(100, 27.58))
    snapshot = p.position(CMB)
    snapshot.total_qty = 999
    assert p.position(CMB).total_qty == 100
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/core/test_portfolio.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.core.portfolio'`

- [ ] **Step 3: 实现 `src/qsys/core/portfolio.py`**

```python
"""账户与持仓记账。

本模块只管记账，不判断能否交易——那是 constraints 的职责。
它提供冻结/解冻机制但不知道 T+1 是什么：买入只增加 total_qty，
unfreeze() 才补齐可卖数量。解冻时机由调用方决定（T+1 市场在每个交易日
开始时调用；T+0 市场可在每次成交后调用），这是多市场预留的关键。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from qsys.core.trading import Fill, Side, to_cents
from qsys.core.types import Symbol


@dataclass(slots=True)
class Position:
    symbol: Symbol
    total_qty: int = 0
    available_qty: int = 0  # 已解冻、可卖出的数量
    cost_basis: float = 0.0  # 累计买入成本（含费用）

    @property
    def avg_cost(self) -> float:
        if self.total_qty == 0:
            return 0.0
        return to_cents(self.cost_basis / self.total_qty)

    @property
    def is_empty(self) -> bool:
        return self.total_qty == 0


class Portfolio:
    def __init__(self, initial_cash: float) -> None:
        self.cash = to_cents(initial_cash)
        self.initial_cash = self.cash
        self.positions: dict[Symbol, Position] = {}

    def position(self, symbol: Symbol) -> Position:
        """返回持仓快照。修改快照不影响账户内部状态。"""
        held = self.positions.get(symbol)
        return replace(held) if held is not None else Position(symbol)

    def apply_fill(self, fill: Fill) -> None:
        held = self.positions.get(fill.symbol) or Position(fill.symbol)

        if fill.side is Side.BUY:
            held.total_qty += fill.qty
            held.cost_basis = to_cents(held.cost_basis + fill.gross + fill.fee)
            # 不增加 available_qty —— 解冻由调用方通过 unfreeze() 决定
        else:
            if fill.qty > held.total_qty:
                raise ValueError(
                    f"cannot sell {fill.qty} of {fill.symbol}: only {held.total_qty} held"
                )
            ratio = (held.total_qty - fill.qty) / held.total_qty
            held.cost_basis = to_cents(held.cost_basis * ratio)
            held.total_qty -= fill.qty
            held.available_qty = max(0, held.available_qty - fill.qty)

        self.cash = to_cents(self.cash + fill.cash_delta)

        if held.is_empty:
            self.positions.pop(fill.symbol, None)
        else:
            self.positions[fill.symbol] = held

    def unfreeze(self) -> None:
        """把全部持仓标记为可卖。"""
        for held in self.positions.values():
            held.available_qty = held.total_qty

    def market_value(self, prices: Mapping[Symbol, float]) -> float:
        total = 0.0
        for symbol, held in self.positions.items():
            if symbol not in prices:
                raise KeyError(f"no price for held symbol {symbol}")
            total += held.total_qty * prices[symbol]
        return to_cents(total)

    def total_value(self, prices: Mapping[Symbol, float]) -> float:
        return to_cents(self.cash + self.market_value(prices))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/core/test_portfolio.py -v`
Expected: PASS —— 18 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/core/portfolio.py tests/core/test_portfolio.py
git commit -m "feat(core): 持仓与账户记账（含冻结解冻机制）"
```

---

## Task 4: 约束抽象与整手约束

**Files:**
- Create: `src/qsys/core/constraints.py`
- Test: `tests/core/test_constraints_basic.py`

**Interfaces:**
- Consumes: `OrderIntent` / `Side`（Task 1）、`Portfolio`（Task 3）
- Produces: `Verdict(allowed_qty, reason="")` 带 `is_rejected: bool`、`ConstraintContext(portfolio, reference_price, prev_close=None, board="main", is_suspended=False)`、`Constraint` (ABC: 类属性 `name: str`，方法 `apply(intent, ctx) -> Verdict`)、`LotSizeConstraint(lot=100)`

- [ ] **Step 1: 写失败测试**

Create `tests/core/test_constraints_basic.py`:

```python
import pytest

from qsys.core.constraints import ConstraintContext, LotSizeConstraint, Verdict
from qsys.core.portfolio import Portfolio
from qsys.core.trading import OrderIntent, Side
from qsys.core.types import Market, Symbol

CMB = Symbol("600036", Market.SH)


def ctx(cash: float = 100_000.0, price: float = 27.58, **kwargs) -> ConstraintContext:
    return ConstraintContext(portfolio=Portfolio(cash), reference_price=price, **kwargs)


def test_verdict_zero_qty_is_rejection():
    assert Verdict(0, "nope").is_rejected is True
    assert Verdict(100).is_rejected is False


def test_lot_size_passes_exact_multiple():
    v = LotSizeConstraint().apply(OrderIntent(CMB, Side.BUY, 200), ctx())
    assert v.allowed_qty == 200
    assert v.reason == ""


def test_lot_size_rounds_buy_down():
    v = LotSizeConstraint().apply(OrderIntent(CMB, Side.BUY, 250), ctx())
    assert v.allowed_qty == 200
    assert "lot" in v.reason


def test_lot_size_rejects_buy_below_one_lot():
    v = LotSizeConstraint().apply(OrderIntent(CMB, Side.BUY, 50), ctx())
    assert v.is_rejected
    assert "below one lot" in v.reason


def test_lot_size_does_not_constrain_sells():
    """A 股卖出可不足整手（零股须一次卖清），故不夹取。"""
    v = LotSizeConstraint().apply(OrderIntent(CMB, Side.SELL, 37), ctx())
    assert v.allowed_qty == 37


def test_lot_size_is_configurable():
    v = LotSizeConstraint(lot=1).apply(OrderIntent(CMB, Side.BUY, 37), ctx())
    assert v.allowed_qty == 37


def test_constraint_has_a_name():
    assert LotSizeConstraint().name == "lot_size"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/core/test_constraints_basic.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.core.constraints'`

- [ ] **Step 3: 实现 `src/qsys/core/constraints.py`（首段）**

```python
"""交易约束系统。

约束只做判定，不修改账户。每条约束只能把数量夹得更小或直接拒绝，
绝不放大。同一份约束代码在两处共用：回测撮合前检查、实盘风控下单前检查——
因此任何约束都不得依赖「当前在回测中」这类信息。

core 不认识「涨跌停 10%」这条规则，只认识「涨跌停」这个概念——
具体数值由 markets/ 注入。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from qsys.core.portfolio import Portfolio
from qsys.core.trading import OrderIntent, Side, to_cents


@dataclass(frozen=True, slots=True)
class Verdict:
    allowed_qty: int
    reason: str = ""

    @property
    def is_rejected(self) -> bool:
        return self.allowed_qty == 0


@dataclass(frozen=True, slots=True)
class ConstraintContext:
    portfolio: Portfolio
    reference_price: float  # 该订单预计成交价
    prev_close: float | None = None  # 涨跌停判定基准
    board: str = "main"
    is_suspended: bool = False


class Constraint(ABC):
    name: str

    @abstractmethod
    def apply(self, intent: OrderIntent, ctx: ConstraintContext) -> Verdict: ...


class LotSizeConstraint(Constraint):
    name = "lot_size"

    def __init__(self, lot: int = 100) -> None:
        if lot < 1:
            raise ValueError(f"lot must be >= 1: {lot}")
        self._lot = lot

    def apply(self, intent: OrderIntent, ctx: ConstraintContext) -> Verdict:
        # 卖出可不足整手（A 股零股须一次卖清），不夹取
        if intent.side is Side.SELL:
            return Verdict(intent.qty)

        allowed = (intent.qty // self._lot) * self._lot
        if allowed == 0:
            return Verdict(0, f"buy qty {intent.qty} below one lot ({self._lot})")
        if allowed < intent.qty:
            return Verdict(allowed, f"rounded down to lot multiple ({self._lot})")
        return Verdict(allowed)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/core/test_constraints_basic.py -v`
Expected: PASS —— 7 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/core/constraints.py tests/core/test_constraints_basic.py
git commit -m "feat(core): 约束抽象与整手约束"
```

---

## Task 5: 不可做空与可用资金约束

**Files:**
- Modify: `src/qsys/core/constraints.py`（追加两个类）
- Modify: `tests/core/test_constraints_basic.py`（追加测试）

**Interfaces:**
- Consumes: `Constraint` / `Verdict` / `ConstraintContext`（Task 4）
- Produces: `NoShortConstraint()`（`name = "no_short"`）、`CashConstraint(fee_buffer=0.001)`（`name = "cash"`）

- [ ] **Step 1: 追加失败测试**

Append to `tests/core/test_constraints_basic.py`:

```python


# --- NoShortConstraint / CashConstraint ---

from datetime import datetime

from qsys.core.constraints import CashConstraint, NoShortConstraint
from qsys.core.trading import Fill

NOW = datetime(2024, 1, 2, 15, 0)


def funded(qty: int, price: float = 10.0, cash: float = 100_000.0) -> Portfolio:
    p = Portfolio(cash)
    if qty:
        p.apply_fill(Fill("o", CMB, Side.BUY, qty, price, 0.0, NOW))
        p.unfreeze()
    return p


def test_no_short_allows_any_buy():
    c = ConstraintContext(portfolio=funded(0), reference_price=10.0)
    assert NoShortConstraint().apply(OrderIntent(CMB, Side.BUY, 500), c).allowed_qty == 500


def test_no_short_rejects_sell_without_position():
    c = ConstraintContext(portfolio=funded(0), reference_price=10.0)
    v = NoShortConstraint().apply(OrderIntent(CMB, Side.SELL, 100), c)
    assert v.is_rejected
    assert "no position" in v.reason


def test_no_short_clamps_sell_to_holding():
    c = ConstraintContext(portfolio=funded(300), reference_price=10.0)
    v = NoShortConstraint().apply(OrderIntent(CMB, Side.SELL, 500), c)
    assert v.allowed_qty == 300
    assert "300" in v.reason


def test_no_short_passes_sell_within_holding():
    c = ConstraintContext(portfolio=funded(300), reference_price=10.0)
    assert NoShortConstraint().apply(OrderIntent(CMB, Side.SELL, 200), c).allowed_qty == 200


def test_cash_allows_any_sell():
    c = ConstraintContext(portfolio=Portfolio(0.0), reference_price=10.0)
    assert CashConstraint().apply(OrderIntent(CMB, Side.SELL, 500), c).allowed_qty == 500


def test_cash_clamps_buy_to_affordable_quantity():
    # 1000 元现金，10 元/股，费用缓冲 0.1% -> 每股 10.01 -> 可买 99 股
    c = ConstraintContext(portfolio=Portfolio(1000.0), reference_price=10.0)
    v = CashConstraint().apply(OrderIntent(CMB, Side.BUY, 500), c)
    assert v.allowed_qty == 99
    assert "cash" in v.reason


def test_cash_rejects_when_nothing_affordable():
    c = ConstraintContext(portfolio=Portfolio(5.0), reference_price=10.0)
    v = CashConstraint().apply(OrderIntent(CMB, Side.BUY, 100), c)
    assert v.is_rejected
    assert "insufficient cash" in v.reason


def test_cash_passes_when_fully_affordable():
    c = ConstraintContext(portfolio=Portfolio(100_000.0), reference_price=10.0)
    assert CashConstraint().apply(OrderIntent(CMB, Side.BUY, 100), c).allowed_qty == 100


def test_cash_rejects_non_positive_reference_price():
    c = ConstraintContext(portfolio=Portfolio(100_000.0), reference_price=0.0)
    v = CashConstraint().apply(OrderIntent(CMB, Side.BUY, 100), c)
    assert v.is_rejected
    assert "reference price" in v.reason


def test_cash_buffer_is_configurable():
    c = ConstraintContext(portfolio=Portfolio(1000.0), reference_price=10.0)
    assert CashConstraint(fee_buffer=0.0).apply(OrderIntent(CMB, Side.BUY, 500), c).allowed_qty == 100
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/core/test_constraints_basic.py -v`
Expected: FAIL —— `ImportError: cannot import name 'CashConstraint'`

- [ ] **Step 3: 追加实现到 `src/qsys/core/constraints.py`**

```python


class NoShortConstraint(Constraint):
    """不可做空：卖出数量不得超过持仓。"""

    name = "no_short"

    def apply(self, intent: OrderIntent, ctx: ConstraintContext) -> Verdict:
        if intent.side is Side.BUY:
            return Verdict(intent.qty)

        held = ctx.portfolio.position(intent.symbol).total_qty
        if held <= 0:
            return Verdict(0, f"no position to sell in {intent.symbol}")
        if intent.qty > held:
            return Verdict(held, f"clamped to holding {held}")
        return Verdict(intent.qty)


class CashConstraint(Constraint):
    """可用资金：买入数量不得超过现金所能负担。

    fee_buffer 为费用预留比例——下单时费用尚未确定，留出余量避免成交后透支。
    """

    name = "cash"

    def __init__(self, fee_buffer: float = 0.001) -> None:
        if fee_buffer < 0:
            raise ValueError(f"fee_buffer must be >= 0: {fee_buffer}")
        self._buffer = fee_buffer

    def apply(self, intent: OrderIntent, ctx: ConstraintContext) -> Verdict:
        if intent.side is Side.SELL:
            return Verdict(intent.qty)

        unit = ctx.reference_price * (1.0 + self._buffer)
        if unit <= 0:
            return Verdict(0, f"invalid reference price: {ctx.reference_price}")

        affordable = int(ctx.portfolio.cash // unit)
        if affordable <= 0:
            return Verdict(
                0, f"insufficient cash: {ctx.portfolio.cash:.2f} cannot buy one share"
            )
        if intent.qty > affordable:
            return Verdict(affordable, f"clamped by cash to {affordable}")
        return Verdict(intent.qty)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/core/test_constraints_basic.py -v`
Expected: PASS —— 17 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/core/constraints.py tests/core/test_constraints_basic.py
git commit -m "feat(core): 不可做空与可用资金约束"
```

---

## Task 6: 交收、涨跌停与停牌约束

三条与市场规则相关的约束。涨跌停的方向性是最容易写反的地方：**涨停时买不到（卖得掉），跌停时卖不掉（买得到）**。

**Files:**
- Modify: `src/qsys/core/constraints.py`（追加三个类）
- Test: `tests/core/test_constraints_market.py`

**Interfaces:**
- Consumes: `Constraint` / `Verdict` / `ConstraintContext`（Task 4）
- Produces: `SettlementConstraint()`（`name = "settlement"`）、`SuspensionConstraint()`（`name = "suspension"`）、`PriceLimitConstraint(limits: Mapping[str, float], default_limit: float = 0.10)`（`name = "price_limit"`）带类方法 `limit_prices(prev_close, limit) -> tuple[float, float]`

- [ ] **Step 1: 写失败测试**

Create `tests/core/test_constraints_market.py`:

```python
from datetime import datetime

import pytest

from qsys.core.constraints import (
    ConstraintContext,
    PriceLimitConstraint,
    SettlementConstraint,
    SuspensionConstraint,
)
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, OrderIntent, Side
from qsys.core.types import Market, Symbol

CMB = Symbol("600036", Market.SH)
NOW = datetime(2024, 1, 2, 15, 0)
LIMITS = {"main": 0.10, "gem": 0.20, "star": 0.20, "bse": 0.30}


def holding(total: int, available: int, cash: float = 100_000.0) -> Portfolio:
    p = Portfolio(cash)
    if total:
        p.apply_fill(Fill("o", CMB, Side.BUY, total, 10.0, 0.0, NOW))
        if available:
            p.unfreeze()
            if available < total:
                p.positions[CMB].available_qty = available
    return p


# --- SettlementConstraint（T+1） ---


def test_settlement_allows_any_buy():
    c = ConstraintContext(portfolio=holding(0, 0), reference_price=10.0)
    assert SettlementConstraint().apply(OrderIntent(CMB, Side.BUY, 500), c).allowed_qty == 500


def test_settlement_rejects_sell_of_unsettled_shares():
    """当日买入当日卖出必须被拒绝。"""
    c = ConstraintContext(portfolio=holding(300, 0), reference_price=10.0)
    v = SettlementConstraint().apply(OrderIntent(CMB, Side.SELL, 100), c)
    assert v.is_rejected
    assert "settled" in v.reason


def test_settlement_clamps_sell_to_settled_quantity():
    c = ConstraintContext(portfolio=holding(300, 200), reference_price=10.0)
    v = SettlementConstraint().apply(OrderIntent(CMB, Side.SELL, 300), c)
    assert v.allowed_qty == 200


def test_settlement_passes_fully_settled_sell():
    c = ConstraintContext(portfolio=holding(300, 300), reference_price=10.0)
    assert SettlementConstraint().apply(OrderIntent(CMB, Side.SELL, 300), c).allowed_qty == 300


# --- SuspensionConstraint ---


def test_suspension_rejects_buy():
    c = ConstraintContext(portfolio=holding(0, 0), reference_price=10.0, is_suspended=True)
    v = SuspensionConstraint().apply(OrderIntent(CMB, Side.BUY, 100), c)
    assert v.is_rejected
    assert "suspended" in v.reason


def test_suspension_rejects_sell():
    c = ConstraintContext(portfolio=holding(300, 300), reference_price=10.0, is_suspended=True)
    assert SuspensionConstraint().apply(OrderIntent(CMB, Side.SELL, 100), c).is_rejected


def test_suspension_passes_when_trading():
    c = ConstraintContext(portfolio=holding(0, 0), reference_price=10.0)
    assert SuspensionConstraint().apply(OrderIntent(CMB, Side.BUY, 100), c).allowed_qty == 100


# --- PriceLimitConstraint ---


def test_limit_prices_are_rounded_to_cents():
    up, down = PriceLimitConstraint.limit_prices(9.99, 0.10)
    assert up == 10.99
    assert down == 8.99


def test_limit_prices_on_round_number():
    up, down = PriceLimitConstraint.limit_prices(10.00, 0.10)
    assert up == 11.00
    assert down == 9.00


def test_price_limit_rejects_buy_at_limit_up():
    """涨停时买不到。"""
    c = ConstraintContext(portfolio=holding(0, 0), reference_price=11.00, prev_close=10.00)
    v = PriceLimitConstraint(LIMITS).apply(OrderIntent(CMB, Side.BUY, 100), c)
    assert v.is_rejected
    assert "limit up" in v.reason


def test_price_limit_allows_sell_at_limit_up():
    """涨停时卖得掉。"""
    c = ConstraintContext(portfolio=holding(300, 300), reference_price=11.00, prev_close=10.00)
    assert PriceLimitConstraint(LIMITS).apply(OrderIntent(CMB, Side.SELL, 100), c).allowed_qty == 100


def test_price_limit_rejects_sell_at_limit_down():
    """跌停时卖不掉。"""
    c = ConstraintContext(portfolio=holding(300, 300), reference_price=9.00, prev_close=10.00)
    v = PriceLimitConstraint(LIMITS).apply(OrderIntent(CMB, Side.SELL, 100), c)
    assert v.is_rejected
    assert "limit down" in v.reason


def test_price_limit_allows_buy_at_limit_down():
    """跌停时买得到。"""
    c = ConstraintContext(portfolio=holding(0, 0), reference_price=9.00, prev_close=10.00)
    assert PriceLimitConstraint(LIMITS).apply(OrderIntent(CMB, Side.BUY, 100), c).allowed_qty == 100


def test_price_limit_passes_inside_the_band():
    c = ConstraintContext(portfolio=holding(300, 300), reference_price=10.50, prev_close=10.00)
    con = PriceLimitConstraint(LIMITS)
    assert con.apply(OrderIntent(CMB, Side.BUY, 100), c).allowed_qty == 100
    assert con.apply(OrderIntent(CMB, Side.SELL, 100), c).allowed_qty == 100


def test_price_limit_uses_board_specific_width():
    """创业板 20%：10.00 -> 12.00 才是涨停，11.00 仍可买入。"""
    c = ConstraintContext(
        portfolio=holding(0, 0), reference_price=11.00, prev_close=10.00, board="gem"
    )
    assert PriceLimitConstraint(LIMITS).apply(OrderIntent(CMB, Side.BUY, 100), c).allowed_qty == 100


def test_price_limit_falls_back_to_default_for_unknown_board():
    c = ConstraintContext(
        portfolio=holding(0, 0), reference_price=11.00, prev_close=10.00, board="unknown"
    )
    assert PriceLimitConstraint(LIMITS).apply(OrderIntent(CMB, Side.BUY, 100), c).is_rejected


def test_price_limit_passes_when_prev_close_unknown():
    """无前收（如上市首日）时不判定，交由其它约束处理。"""
    c = ConstraintContext(portfolio=holding(0, 0), reference_price=11.00, prev_close=None)
    assert PriceLimitConstraint(LIMITS).apply(OrderIntent(CMB, Side.BUY, 100), c).allowed_qty == 100
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/core/test_constraints_market.py -v`
Expected: FAIL —— `ImportError: cannot import name 'SettlementConstraint'`

- [ ] **Step 3: 追加实现到 `src/qsys/core/constraints.py`**

Task 4 的 import 区已一次性导入 `Mapping`、`Sequence` 与 `to_cents`，此处无需改动 import。追加三个类：

```python


class SettlementConstraint(Constraint):
    """交收约束：卖出数量不得超过已解冻（已交收）的份额。

    A 股装配此约束即为 T+1；T+0 市场不装配，或由调用方在每次成交后
    立即 unfreeze()。
    """

    name = "settlement"

    def apply(self, intent: OrderIntent, ctx: ConstraintContext) -> Verdict:
        if intent.side is Side.BUY:
            return Verdict(intent.qty)

        available = ctx.portfolio.position(intent.symbol).available_qty
        if available <= 0:
            return Verdict(0, f"no settled shares in {intent.symbol}")
        if intent.qty > available:
            return Verdict(available, f"clamped to settled {available}")
        return Verdict(intent.qty)


class SuspensionConstraint(Constraint):
    """停牌不可交易，买卖皆拒。"""

    name = "suspension"

    def apply(self, intent: OrderIntent, ctx: ConstraintContext) -> Verdict:
        if ctx.is_suspended:
            return Verdict(0, f"{intent.symbol} is suspended")
        return Verdict(intent.qty)


class PriceLimitConstraint(Constraint):
    """涨跌停约束。

    方向性是最容易写反的地方：
      涨停 —— 买单排队买不到，卖单成交得掉 -> 拒买、放行卖
      跌停 —— 卖单排队卖不掉，买单成交得到 -> 拒卖、放行买
    """

    name = "price_limit"

    def __init__(self, limits: Mapping[str, float], default_limit: float = 0.10) -> None:
        self._limits = dict(limits)
        self._default = default_limit

    @staticmethod
    def limit_prices(prev_close: float, limit: float) -> tuple[float, float]:
        """返回 (涨停价, 跌停价)，均四舍五入到分。"""
        return to_cents(prev_close * (1.0 + limit)), to_cents(prev_close * (1.0 - limit))

    def apply(self, intent: OrderIntent, ctx: ConstraintContext) -> Verdict:
        if ctx.prev_close is None or ctx.prev_close <= 0:
            # 上市首日等无前收的情形不判定，交由其它约束处理
            return Verdict(intent.qty)

        limit = self._limits.get(ctx.board, self._default)
        up, down = self.limit_prices(ctx.prev_close, limit)
        price = to_cents(ctx.reference_price)

        if intent.side is Side.BUY and price >= up:
            return Verdict(0, f"limit up at {up:.2f}: buy cannot fill")
        if intent.side is Side.SELL and price <= down:
            return Verdict(0, f"limit down at {down:.2f}: sell cannot fill")
        return Verdict(intent.qty)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/core/test_constraints_market.py -v`
Expected: PASS —— 17 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/core/constraints.py tests/core/test_constraints_market.py
git commit -m "feat(core): 交收、涨跌停与停牌约束"
```

---

## Task 7: 约束流水线

流水线把约束串联，每条只能把数量夹得更小。`PipelineResult` 保留每条约束的判定——这是 spec §9 结构化日志「每条约束的检查结果」的数据来源。

**Files:**
- Modify: `src/qsys/core/constraints.py`（追加）
- Test: `tests/core/test_constraint_pipeline.py`

**Interfaces:**
- Consumes: 全部约束类（Task 4–6）
- Produces: `PipelineResult(allowed_qty, checks: tuple[tuple[str, Verdict], ...])` 带 `is_rejected: bool`、`binding_reason: str`、`as_log_fields() -> dict[str, object]`；`ConstraintPipeline(constraints: Sequence[Constraint])` 带 `apply(intent, ctx) -> PipelineResult`、`names: tuple[str, ...]`

- [ ] **Step 1: 写失败测试**

Create `tests/core/test_constraint_pipeline.py`:

```python
from datetime import datetime

import pytest

from qsys.core.constraints import (
    CashConstraint,
    ConstraintContext,
    ConstraintPipeline,
    LotSizeConstraint,
    NoShortConstraint,
    PriceLimitConstraint,
    SettlementConstraint,
    SuspensionConstraint,
)
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, OrderIntent, Side
from qsys.core.types import Market, Symbol

CMB = Symbol("600036", Market.SH)
NOW = datetime(2024, 1, 2, 15, 0)
LIMITS = {"main": 0.10}

PIPELINE = ConstraintPipeline(
    [
        SuspensionConstraint(),
        PriceLimitConstraint(LIMITS),
        NoShortConstraint(),
        SettlementConstraint(),
        CashConstraint(),
        LotSizeConstraint(),
    ]
)


def held(total: int, settled: bool, cash: float) -> Portfolio:
    p = Portfolio(cash)
    if total:
        p.apply_fill(Fill("o", CMB, Side.BUY, total, 10.0, 0.0, NOW))
        if settled:
            p.unfreeze()
    return p


def test_clean_buy_passes_through():
    c = ConstraintContext(portfolio=held(0, False, 100_000.0), reference_price=10.0,
                          prev_close=10.0)
    r = PIPELINE.apply(OrderIntent(CMB, Side.BUY, 500), c)
    assert r.allowed_qty == 500
    assert r.is_rejected is False


def test_cash_then_lot_yields_whole_lots():
    """现金只够 99 股 -> 整手取整后为 0 -> 拒绝。整手必须排在现金之后。"""
    c = ConstraintContext(portfolio=held(0, False, 1000.0), reference_price=10.0,
                          prev_close=10.0)
    r = PIPELINE.apply(OrderIntent(CMB, Side.BUY, 500), c)
    assert r.is_rejected
    assert r.binding_reason.startswith("lot_size")


def test_cash_then_lot_yields_two_lots():
    c = ConstraintContext(portfolio=held(0, False, 3000.0), reference_price=10.0,
                          prev_close=10.0)
    r = PIPELINE.apply(OrderIntent(CMB, Side.BUY, 500), c)
    assert r.allowed_qty == 200


def test_first_rejection_stops_the_pipeline():
    c = ConstraintContext(portfolio=held(0, False, 100_000.0), reference_price=10.0,
                          prev_close=10.0, is_suspended=True)
    r = PIPELINE.apply(OrderIntent(CMB, Side.BUY, 500), c)
    assert r.is_rejected
    assert r.binding_reason.startswith("suspension")
    assert [name for name, _ in r.checks] == ["suspension"]


def test_checks_record_every_constraint_that_ran():
    c = ConstraintContext(portfolio=held(0, False, 100_000.0), reference_price=10.0,
                          prev_close=10.0)
    r = PIPELINE.apply(OrderIntent(CMB, Side.BUY, 500), c)
    assert [name for name, _ in r.checks] == list(PIPELINE.names)


def test_binding_reason_names_the_tightest_constraint():
    c = ConstraintContext(portfolio=held(500, True, 100_000.0), reference_price=10.0,
                          prev_close=10.0)
    r = PIPELINE.apply(OrderIntent(CMB, Side.SELL, 900), c)
    assert r.allowed_qty == 500
    assert r.binding_reason.startswith("no_short")


def test_settlement_blocks_same_day_sell():
    c = ConstraintContext(portfolio=held(500, False, 100_000.0), reference_price=10.0,
                          prev_close=10.0)
    r = PIPELINE.apply(OrderIntent(CMB, Side.SELL, 500), c)
    assert r.is_rejected
    assert r.binding_reason.startswith("settlement")


def test_no_binding_reason_when_nothing_clamped():
    c = ConstraintContext(portfolio=held(0, False, 100_000.0), reference_price=10.0,
                          prev_close=10.0)
    r = PIPELINE.apply(OrderIntent(CMB, Side.BUY, 500), c)
    assert r.binding_reason == ""


def test_as_log_fields_is_flat_and_serializable():
    c = ConstraintContext(portfolio=held(0, False, 3000.0), reference_price=10.0,
                          prev_close=10.0)
    fields = PIPELINE.apply(OrderIntent(CMB, Side.BUY, 500), c).as_log_fields()
    assert fields["allowed_qty"] == 200
    assert isinstance(fields["checks"], str)
    assert "cash" in fields["checks"]


def test_empty_pipeline_passes_everything():
    c = ConstraintContext(portfolio=held(0, False, 0.0), reference_price=10.0)
    r = ConstraintPipeline([]).apply(OrderIntent(CMB, Side.BUY, 500), c)
    assert r.allowed_qty == 500
    assert r.checks == ()


def test_constraint_cannot_increase_quantity():
    """流水线必须防御性地拒绝放大数量的约束实现。"""

    class BadConstraint(SuspensionConstraint):
        name = "bad"

        def apply(self, intent, ctx):
            from qsys.core.constraints import Verdict

            return Verdict(intent.qty * 10)

    c = ConstraintContext(portfolio=held(0, False, 100_000.0), reference_price=10.0)
    with pytest.raises(ValueError, match="cannot increase"):
        ConstraintPipeline([BadConstraint()]).apply(OrderIntent(CMB, Side.BUY, 100), c)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/core/test_constraint_pipeline.py -v`
Expected: FAIL —— `ImportError: cannot import name 'ConstraintPipeline'`

- [ ] **Step 3: 追加实现到 `src/qsys/core/constraints.py`**

import 区无需改动（Task 4 已导入 `Sequence`），直接追加：

```python


@dataclass(frozen=True, slots=True)
class PipelineResult:
    allowed_qty: int
    checks: tuple[tuple[str, Verdict], ...] = ()

    @property
    def is_rejected(self) -> bool:
        return self.allowed_qty == 0

    @property
    def binding_reason(self) -> str:
        """最终把数量夹到最小的那条约束，格式为 '<name>: <reason>'。"""
        for name, verdict in reversed(self.checks):
            if verdict.reason:
                return f"{name}: {verdict.reason}"
        return ""

    def as_log_fields(self) -> dict[str, object]:
        """摊平成结构化日志字段。"""
        return {
            "allowed_qty": self.allowed_qty,
            "binding_reason": self.binding_reason,
            "checks": "; ".join(f"{n}={v.allowed_qty}" for n, v in self.checks),
        }


class ConstraintPipeline:
    """按序施加约束，每条只能把数量夹得更小。

    任一条判定为 0 即立即停止——后续约束的判定没有意义，
    且能让 binding_reason 准确指向真正的拒绝原因。
    """

    def __init__(self, constraints: Sequence[Constraint]) -> None:
        self._constraints = tuple(constraints)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self._constraints)

    def apply(self, intent: OrderIntent, ctx: ConstraintContext) -> PipelineResult:
        qty = intent.qty
        checks: list[tuple[str, Verdict]] = []

        for constraint in self._constraints:
            verdict = constraint.apply(intent.with_qty(qty), ctx)
            if verdict.allowed_qty > qty:
                raise ValueError(
                    f"constraint {constraint.name!r} cannot increase qty "
                    f"{qty} -> {verdict.allowed_qty}"
                )
            checks.append((constraint.name, verdict))
            qty = verdict.allowed_qty
            if qty == 0:
                break

        return PipelineResult(allowed_qty=qty, checks=tuple(checks))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/core/test_constraint_pipeline.py -v`
Expected: PASS —— 11 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/core/constraints.py tests/core/test_constraint_pipeline.py
git commit -m "feat(core): 约束流水线与判定追溯"
```

---

## Task 8: A 股市场预设

`core/` 到此为止不含任何 A 股数值。本任务把它们集中到 `markets/ashare.py`——将来接币圈/美股只需新增一个同形状的模块。

**Files:**
- Create: `src/qsys/markets/__init__.py`
- Create: `src/qsys/markets/ashare.py`
- Test: `tests/markets/test_ashare.py`

**Interfaces:**
- Consumes: 全部约束类与 `ConstraintPipeline`（Task 4–7）
- Produces: `PRICE_LIMITS: dict[str, float]`、`LOT_SIZE: int = 100`、`ashare_pipeline(*, fee_buffer: float = 0.001) -> ConstraintPipeline`

- [ ] **Step 1: 写失败测试**

Create `tests/markets/test_ashare.py`:

```python
from datetime import datetime

from qsys.core.constraints import ConstraintContext
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, OrderIntent, Side
from qsys.core.types import Market, Symbol
from qsys.markets.ashare import LOT_SIZE, PRICE_LIMITS, ashare_pipeline

CMB = Symbol("600036", Market.SH)
NOW = datetime(2024, 1, 2, 15, 0)


def test_price_limits_cover_every_board_the_data_layer_emits():
    """板块取值必须与计划 1-1 的 _board_of 输出对齐。"""
    assert set(PRICE_LIMITS) == {"main", "gem", "star", "bse", "bshare"}


def test_price_limit_values():
    assert PRICE_LIMITS["main"] == 0.10
    assert PRICE_LIMITS["gem"] == 0.20
    assert PRICE_LIMITS["star"] == 0.20
    assert PRICE_LIMITS["bse"] == 0.30
    assert PRICE_LIMITS["bshare"] == 0.10


def test_lot_size_is_one_hundred():
    assert LOT_SIZE == 100


def test_pipeline_order_puts_lot_size_last():
    """整手必须最后施加，否则现金夹取会留下零股。"""
    assert ashare_pipeline().names == (
        "suspension",
        "price_limit",
        "no_short",
        "settlement",
        "cash",
        "lot_size",
    )


def test_pipeline_enforces_t_plus_one():
    p = Portfolio(100_000.0)
    p.apply_fill(Fill("o", CMB, Side.BUY, 500, 10.0, 0.0, NOW))
    ctx = ConstraintContext(portfolio=p, reference_price=10.0, prev_close=10.0)
    assert ashare_pipeline().apply(OrderIntent(CMB, Side.SELL, 500), ctx).is_rejected


def test_pipeline_enforces_lot_size():
    ctx = ConstraintContext(
        portfolio=Portfolio(100_000.0), reference_price=10.0, prev_close=10.0
    )
    r = ashare_pipeline().apply(OrderIntent(CMB, Side.BUY, 250), ctx)
    assert r.allowed_qty == 200


def test_pipeline_enforces_gem_wider_band():
    ctx = ConstraintContext(
        portfolio=Portfolio(100_000.0), reference_price=11.5, prev_close=10.0, board="gem"
    )
    assert ashare_pipeline().apply(OrderIntent(CMB, Side.BUY, 100), ctx).allowed_qty == 100


def test_fee_buffer_is_configurable():
    ctx = ConstraintContext(
        portfolio=Portfolio(1000.0), reference_price=10.0, prev_close=10.0
    )
    assert ashare_pipeline(fee_buffer=0.0).apply(OrderIntent(CMB, Side.BUY, 500), ctx).allowed_qty == 100
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/markets/test_ashare.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.markets'`

- [ ] **Step 3: 实现**

```bash
mkdir -p src/qsys/markets tests/markets
touch src/qsys/markets/__init__.py
```

Create `src/qsys/markets/ashare.py`:

```python
"""A 股市场预设。

core/ 不认识这些数值——它们只出现在这里。将来接币圈或美股，
新增一个同形状的模块即可，core/ 无需改动。

板块取值与计划 1-1 数据层 `_board_of()` 的输出一一对应。
"""

from __future__ import annotations

from qsys.core.constraints import (
    CashConstraint,
    ConstraintPipeline,
    LotSizeConstraint,
    NoShortConstraint,
    PriceLimitConstraint,
    SettlementConstraint,
    SuspensionConstraint,
)

# 日涨跌幅上限。ST 股为 5%，但一期无 ST 历史数据，暂按板块处理。
PRICE_LIMITS: dict[str, float] = {
    "main": 0.10,
    "gem": 0.20,
    "star": 0.20,
    "bse": 0.30,
    "bshare": 0.10,
}

LOT_SIZE = 100


def ashare_pipeline(*, fee_buffer: float = 0.001) -> ConstraintPipeline:
    """A 股约束流水线。

    顺序有实质意义：硬拒绝在前（停牌、涨跌停），数量夹取在后，
    **整手必须最后**——否则现金夹取会留下无法成交的零股。
    """
    return ConstraintPipeline(
        [
            SuspensionConstraint(),
            PriceLimitConstraint(PRICE_LIMITS),
            NoShortConstraint(),
            SettlementConstraint(),
            CashConstraint(fee_buffer=fee_buffer),
            LotSizeConstraint(lot=LOT_SIZE),
        ]
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/markets/test_ashare.py -v`
Expected: PASS —— 8 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/markets tests/markets
git commit -m "feat(markets): A 股约束预设与流水线组装"
```

---

## Task 9: 合成场景端到端断言

前八个任务逐件测试。本任务把它们串成完整交易日序列，断言账户状态**精确到分**。这是 spec §8.2 第一腿（合成数据断言）在内核层的落地。

**Files:**
- Test: `tests/test_kernel_scenarios.py`

**Interfaces:**
- Consumes: 全部内核组件（Task 1–8）
- Produces: 无新增生产代码——纯验收测试

- [ ] **Step 1: 写场景测试**

Create `tests/test_kernel_scenarios.py`:

```python
"""合成场景端到端断言：账户状态精确到分。

这些测试枯燥，但它们是敢用这套内核下真钱的理由。
"""

from datetime import date, datetime

import pytest

from qsys.core.calendar import AShareCalendar
from qsys.core.clock import BacktestClock, Phase
from qsys.core.constraints import ConstraintContext
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Fill, OrderIntent, Side
from qsys.core.types import Market, Symbol
from qsys.markets.ashare import ashare_pipeline

CMB = Symbol("600036", Market.SH)
SESSIONS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
CAL = AShareCalendar(SESSIONS)
PIPE = ashare_pipeline()


def ctx(p: Portfolio, price: float, prev_close: float | None = None, **kw):
    return ConstraintContext(
        portfolio=p, reference_price=price, prev_close=prev_close, **kw
    )


def test_t_plus_one_full_cycle():
    """买入 -> 当日不可卖 -> 次日解冻 -> 可卖。"""
    p = Portfolio(100_000.0)

    # D1 买入 500 股 @10.00，费用 5 元
    r = PIPE.apply(OrderIntent(CMB, Side.BUY, 500), ctx(p, 10.00, 9.50))
    assert r.allowed_qty == 500
    p.apply_fill(Fill("o1", CMB, Side.BUY, 500, 10.00, 5.0, datetime(2024, 1, 2, 15, 0)))
    assert p.cash == 100_000.0 - 5000.00 - 5.0
    assert p.position(CMB).total_qty == 500
    assert p.position(CMB).available_qty == 0

    # D1 当日想卖 -> 被交收约束拒绝
    assert PIPE.apply(OrderIntent(CMB, Side.SELL, 500), ctx(p, 10.00, 9.50)).is_rejected

    # D2 开盘解冻
    p.unfreeze()
    assert p.position(CMB).available_qty == 500

    # D2 卖出 500 股 @11.00，费用 6 元
    r = PIPE.apply(OrderIntent(CMB, Side.SELL, 500), ctx(p, 11.00, 10.00))
    assert r.allowed_qty == 500
    p.apply_fill(Fill("o2", CMB, Side.SELL, 500, 11.00, 6.0, datetime(2024, 1, 3, 15, 0)))

    assert p.cash == 100_000.0 - 5005.0 + 5500.00 - 6.0
    assert CMB not in p.positions
    assert p.total_value({}) == 100_489.00


def test_limit_up_blocks_buy_but_not_sell():
    p = Portfolio(100_000.0)
    p.apply_fill(Fill("o", CMB, Side.BUY, 500, 10.0, 0.0, datetime(2024, 1, 2, 15, 0)))
    p.unfreeze()

    at_limit = ctx(p, 11.00, 10.00)  # 主板 10% -> 涨停 11.00
    assert PIPE.apply(OrderIntent(CMB, Side.BUY, 100), at_limit).is_rejected
    assert PIPE.apply(OrderIntent(CMB, Side.SELL, 500), at_limit).allowed_qty == 500


def test_limit_down_blocks_sell_but_not_buy():
    p = Portfolio(100_000.0)
    p.apply_fill(Fill("o", CMB, Side.BUY, 500, 10.0, 0.0, datetime(2024, 1, 2, 15, 0)))
    p.unfreeze()

    at_limit = ctx(p, 9.00, 10.00)  # 跌停 9.00
    assert PIPE.apply(OrderIntent(CMB, Side.SELL, 500), at_limit).is_rejected
    assert PIPE.apply(OrderIntent(CMB, Side.BUY, 100), at_limit).allowed_qty == 100


def test_suspension_blocks_both_directions():
    p = Portfolio(100_000.0)
    p.apply_fill(Fill("o", CMB, Side.BUY, 500, 10.0, 0.0, datetime(2024, 1, 2, 15, 0)))
    p.unfreeze()

    halted = ctx(p, 10.00, 10.00, is_suspended=True)
    assert PIPE.apply(OrderIntent(CMB, Side.BUY, 100), halted).is_rejected
    assert PIPE.apply(OrderIntent(CMB, Side.SELL, 100), halted).is_rejected


def test_cash_and_lot_size_interact_correctly():
    """3050 元、10 元/股：现金夹到 304 股，整手取整到 300 股。"""
    p = Portfolio(3050.0)
    r = PIPE.apply(OrderIntent(CMB, Side.BUY, 1000), ctx(p, 10.00, 10.00))
    assert r.allowed_qty == 300


def test_partial_sell_keeps_proportional_cost_basis():
    p = Portfolio(100_000.0)
    p.apply_fill(Fill("o1", CMB, Side.BUY, 1000, 10.00, 0.0, datetime(2024, 1, 2, 15, 0)))
    p.unfreeze()
    p.apply_fill(Fill("o2", CMB, Side.SELL, 400, 12.00, 0.0, datetime(2024, 1, 3, 15, 0)))

    pos = p.position(CMB)
    assert pos.total_qty == 600
    assert pos.cost_basis == 6000.00
    assert pos.avg_cost == 10.00


def test_holding_through_a_split_keeps_market_value():
    """10 送 10：持仓翻倍、单价减半，市值不变。

    内核不处理送股本身（那是引擎的职责），此处断言的是
    记账在数量与价格同时变化时仍然自洽。
    """
    p = Portfolio(100_000.0)
    p.apply_fill(Fill("o", CMB, Side.BUY, 1000, 20.00, 0.0, datetime(2024, 1, 2, 15, 0)))
    before = p.market_value({CMB: 20.00})

    p.apply_fill(Fill("split", CMB, Side.BUY, 1000, 0.0, 0.0, datetime(2024, 1, 4, 15, 0)))
    after = p.market_value({CMB: 10.00})

    assert before == after == 20_000.00


def test_clock_drives_a_three_day_session_sequence():
    clock = BacktestClock(CAL, SESSIONS[0], SESSIONS[-1])
    p = Portfolio(100_000.0)
    unfreeze_count = 0

    for _, phase in clock.ticks():
        if phase is Phase.SESSION_START:
            p.unfreeze()
            unfreeze_count += 1

    assert unfreeze_count == 3
    assert clock.now == datetime(2024, 1, 4, 15, 30)


def test_cannot_sell_more_than_held_even_if_constraints_bypassed():
    """记账层的最后一道防线：绕过约束直接记账也必须报错。"""
    p = Portfolio(100_000.0)
    p.apply_fill(Fill("o", CMB, Side.BUY, 100, 10.0, 0.0, datetime(2024, 1, 2, 15, 0)))
    p.unfreeze()
    with pytest.raises(ValueError, match="cannot sell 200"):
        p.apply_fill(Fill("x", CMB, Side.SELL, 200, 10.0, 0.0, datetime(2024, 1, 3, 15, 0)))


def test_round_trip_pnl_is_exact_to_the_cent():
    """完整一轮买卖后现金余额必须精确到分。"""
    p = Portfolio(50_000.0)
    p.apply_fill(Fill("b", CMB, Side.BUY, 300, 27.583, 5.0, datetime(2024, 1, 2, 15, 0)))
    p.unfreeze()
    p.apply_fill(Fill("s", CMB, Side.SELL, 300, 30.117, 9.03, datetime(2024, 1, 3, 15, 0)))

    # 买入 gross = to_cents(27.583*300) = 8274.90 ; 卖出 gross = to_cents(30.117*300) = 9035.10
    assert p.cash == 50_000.0 - 8274.90 - 5.0 + 9035.10 - 9.03
    assert p.cash == 50_746.17
```

- [ ] **Step 2: 运行场景测试**

Run: `uv run pytest tests/test_kernel_scenarios.py -v`
Expected: PASS —— 10 passed

若任一断言失败，**先手算核对预期值再改代码**——这些数字是本计划的验收基准，不得为了让测试通过而调整预期。

- [ ] **Step 3: 运行全部测试**

Run: `uv run pytest -q`
Expected: PASS —— 计划 1-1 的 146 项 + 本计划新增项全部通过

- [ ] **Step 4: 提交**

```bash
git add tests/test_kernel_scenarios.py
git commit -m "test: 交易内核合成场景端到端断言"
```

---

## 完成标准

1. `uv run pytest` 全绿，且计划 1-1 的 146 项测试无回归
2. `grep -rn "0\.10\|0\.20\|0\.30\|100" src/qsys/core/constraints.py` 除默认参数外**不出现 A 股具体数值**——市场参数只允许存在于 `markets/ashare.py`
3. `ashare_pipeline().names` 顺序为 `suspension → price_limit → no_short → settlement → cash → lot_size`，且 `lot_size` 必须最后
4. T+1 场景断言通过：当日买入当日卖出被拒，次日解冻后可卖
5. 涨跌停方向正确：涨停拒买放行卖，跌停拒卖放行买
6. 完整买卖一轮后现金余额**精确到分**（`test_round_trip_pnl_is_exact_to_the_cent`）
7. `PipelineResult.as_log_fields()` 可直接喂给 structlog，包含每条约束的判定

## 下一步

进入 **计划 1-2b：回测引擎** —— 事件循环、`Broker` 抽象、费用与滑点模型、`SimBroker` 保守撮合（下一根 bar 开盘成交 + 成交量占比上限）、`Strategy`/`ctx`、回测驱动器、绩效指标，以及聚宽交叉验证对照工具（工具先建好，待浏览器可用时再跑）。
