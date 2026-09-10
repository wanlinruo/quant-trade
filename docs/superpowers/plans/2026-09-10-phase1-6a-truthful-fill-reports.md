# 一期 1-6a：成交回报真实化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `ExecutionAgent` 发布的成交回报等于券商实际发生的成交——含部分成交、未成交、废单、撤单与逐笔费用——取代当前"下单成功即宣告全额成交"的假回报。

**Architecture:** 券商回报是异步的，但**不用回调**：`ExecutionAgent` 每轮轮询
`query_stock_trades` / `query_stock_orders`，用 `traded_id` 去重。轮询天然可重启
（回调会在代理重启时丢事件），且去重键由券商提供，不需要我们自己保证顺序。
纯逻辑放进 `FillTracker`（无 I/O，可全量单测），持久化去重台账放进 `ReportLedger`，
Windows 专有的字段翻译放进 `XtQuantAdapter`。

**Tech Stack:** Python 3.11+ · uv · pydantic · pytest（Windows 侧仅需 stdlib + redis-py + xtquant）

**Spec:** `docs/superpowers/specs/2026-09-06-personal-quant-system-design.md`（§6.2 QmtBroker、§8 测试、§10 一期验收）

**本期不含（后续两段）：**

- **1-6b 实盘运行器与实时行情通道** —— 核心侧目前**没有任何** live runner：
  `LiveBroker` / `ShadowBroker` 有单测但无人接线。还需要 Windows → Linux 的
  实时报价通道（09:30 竞价价、检查点时刻价）。
- **1-6c 影子周与三层对账验收** —— 影子连续一周、三层对账零不一致、
  小资金真实开平仓一轮。

没有 1-6a，后两段拿到的成交价是编的，对账会在错误数据上"通过"。

## Global Constraints

- Python **3.11+**；依赖管理用 **uv**；所有命令以 `uv run` 前缀执行
- **Windows 侧执行代理的依赖必须保持最小**：只允许 stdlib、`pydantic`、`redis-py`、`xtquant`。
  本计划允许新增 `qsys.backtest.fees` 与 `qsys.core.trading` / `qsys.core.types` 三个
  导入——三者均为纯 stdlib（已核实：`qsys/__init__.py`、`qsys/backtest/__init__.py`、
  `qsys/core/__init__.py` 全为空文件，不会连带拉起 polars）。Task 5 有一条测试钉死这一点。
- **单元测试不得导入 `xtquant`**，一律用 `FakeAdapter`（`src/qsys/agent/adapter.py` 既有约定）
- **单元测试不得连接真实 Redis**，一律用 `MemoryTransport`
- 注释与文档用中文，说明**为什么**而非**是什么**（既有代码风格）
- `docs/reference/joinquant-structural-v1.2.py` **不得修改**
- 部署地址只允许出现在 gitignored 的 `config/deploy.local.toml`

## 已核实的 xtquant 事实（不要再猜，也不要凭记忆改）

在 Windows 机器上自省得到，写计划时的实测结果：

```
order_stock(self, account, stock_code, order_type, order_volume,
            price_type, price, strategy_name='', order_remark='')

query_stock_trades(self, account)                       -> list[XtTrade]
query_stock_orders(self, account, cancelable_only=False) -> list[XtOrder]

XtTrade  字段: stock_code traded_id traded_time traded_price traded_volume
               traded_amount order_id order_sysid order_type strategy_name order_remark
XtOrder  字段: stock_code order_id order_sysid order_time order_type order_volume
               price_type price traded_volume traded_price order_status status_msg
               strategy_name order_remark
XtAsset  字段: cash frozen_cash market_value total_asset

委托状态常量（xtconstant）:
  48 ORDER_UNREPORTED    未报        进行中
  49 ORDER_WAIT_REPORTING 待报       进行中
  50 ORDER_REPORTED      已报        进行中
  51 ORDER_REPORTED_CANCEL 已报待撤  进行中
  52 ORDER_PARTSUCC_CANCEL 部成待撤  终态
  53 ORDER_PART_CANCEL   部撤        终态
  54 ORDER_CANCELED      已撤        终态
  55 ORDER_PART_SUCC     部成        进行中
  56 ORDER_SUCCEEDED     已成        终态
  57 ORDER_JUNK          废单        终态

委托方向: 23 买 / 24 卖（_SIDE_TO_XT 既有常量）
```

**两条关键结论：**

1. `order_remark` 会原样出现在 `XtOrder` 和 `XtTrade` 上 —— `client_order_id`
   直接搭车带过去，不需要额外维护 broker_order_id → client_order_id 映射表。
   券商对 `order_remark` 长度有限制（常见 24 字节），Task 8 的 id 格式为此设计。
2. **`XtTrade` 没有手续费字段。** 费用只能用我们自己的成本模型算。
   这不是妥协——费率是确定的，`AShareFeeModel` 算出来的就是券商会扣的，
   Task 2 处理"最低佣金按笔委托而非按笔成交"这个坑。

---

## File Structure

| 文件 | 职责 |
|---|---|
| `src/qsys/agent/fill_tracker.py` | **新建**。纯逻辑：给定券商成交/委托快照与已上报集合，产出待发布的 Fill/Reject。无 I/O。 |
| `src/qsys/agent/report_ledger.py` | **新建**。去重台账：哪些 `traded_id` 已上报、哪些委托已终结。按交易日分键，落在 Transport 上。 |
| `src/qsys/core/order_ids.py` | **新建**。委托号工厂。回测用可复现序号，实盘用跨重启唯一号。 |
| `src/qsys/live/messages.py` | 改。`FillEvent` 加 `broker_trade_id`；`RejectEvent` 加 `side` / `unfilled_qty`。 |
| `src/qsys/agent/adapter.py` | 改。`BrokerAdapter` 加 `query_trades()` / `query_orders()`；`FakeAdapter` 支持可编排的成交序列。 |
| `src/qsys/agent/execution_agent.py` | 改。删除假 Fill 发布；新增 `poll_reports()`。 |
| `src/qsys/agent/xtquant_mapping.py` | **新建**。xtquant 数据结构到我们类型的翻译。纯函数，不导入 xtquant，可在 CI 上测。 |
| `src/qsys/agent/xtquant_adapter.py` | 改。实现两个查询、下单带 `order_remark`。只剩调 xtquant 的那几行。 |
| `src/qsys/live/live_broker.py` | 改。`_pending` 支持部分成交，累计到位才出队。 |
| `src/qsys/live/config.py` | 改。新增 `[fees]` 段。 |
| `scripts/run_agent.py` | 改。循环里调 `poll_reports()`；从配置构造费用模型。 |
| `config/deploy.example.toml` | 改。补 `[fees]` 示例。 |

新建测试：`tests/agent/test_fill_tracker.py`、`tests/agent/test_report_ledger.py`、
`tests/agent/test_xtquant_mapping.py`、`tests/core/test_order_ids.py`。改动测试：`tests/agent/test_execution_agent.py`、
`tests/live/test_live_broker.py`、`tests/live/test_messages.py`、`tests/live/test_config.py`。

---

## Task 1: 消息协议扩容

**Files:**
- Modify: `src/qsys/live/messages.py:47-74`
- Test: `tests/live/test_messages.py`

**Interfaces:**
- Produces: `FillEvent.broker_trade_id: str | None`、`RejectEvent.side: Literal["buy","sell"] | None`、
  `RejectEvent.unfilled_qty: int | None`

`AccountSnapshot` **本期不动**。加 `frozen_cash` / `total_asset` 确实有用，
但本期没有任何代码会写入它们——加了就是占位字段。留给 1-6b 第一层对账那一步，
届时 `query_account` 的返回值会跟着一起改。

新增字段一律带默认值：跨机协议在升级期间两侧版本必然不同步，
旧代理发来的旧消息不能让新核心崩掉。

- [ ] **Step 1: 写失败的测试**

在 `tests/live/test_messages.py` 末尾追加：

```python
def test_fill_event_carries_the_broker_trade_id():
    """traded_id 是券商给的去重键，必须能穿过协议传到核心侧。"""
    event = FillEvent(
        client_order_id="c-1", symbol="600036.SH", side="buy", qty=100,
        price=41.5, fee=5.0, filled_at=datetime(2026, 9, 11, 9, 30),
        broker_order_id="B1", broker_trade_id="T-9001",
    )
    assert FillEvent.from_payload(event.to_payload()).broker_trade_id == "T-9001"


def test_old_fill_without_broker_trade_id_still_parses():
    """升级期间旧代理发来的消息不能让新核心崩掉。"""
    event = FillEvent(
        client_order_id="c-1", symbol="600036.SH", side="buy", qty=100,
        price=41.5, fee=5.0, filled_at=datetime(2026, 9, 11, 9, 30),
    )
    assert FillEvent.from_payload(event.to_payload()).broker_trade_id is None


def test_reject_event_carries_the_unfilled_remainder():
    """撤单后剩余未成交的股数要能传回，否则核心无法归还冻结额度。"""
    event = RejectEvent(
        client_order_id="c-1", symbol="600036.SH", reason="已撤: 委托 1000 股，成交 300 股",
        at=datetime(2026, 9, 11, 14, 30), side="buy", unfilled_qty=700,
    )
    parsed = RejectEvent.from_payload(event.to_payload())
    assert parsed.unfilled_qty == 700
    assert parsed.side == "buy"

```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/live/test_messages.py -q
```

Expected: FAIL，`ValidationError: Unexpected keyword argument 'broker_trade_id'`

- [ ] **Step 3: 改协议**

`src/qsys/live/messages.py`，把 `FillEvent` 与 `RejectEvent` 改成：

```python
class FillEvent(Message):
    client_order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: int
    price: float
    fee: float
    filled_at: datetime
    broker_order_id: str | None = None
    # 券商成交编号。去重键由券商提供，不需要我们保证顺序或唯一性。
    broker_trade_id: str | None = None


class RejectEvent(Message):
    client_order_id: str
    symbol: str
    reason: str
    at: datetime
    # 部分成交后撤单时，剩余未成交的股数。核心据此归还冻结额度。
    side: Literal["buy", "sell"] | None = None
    unfilled_qty: int | None = None

```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/live/test_messages.py -q
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/qsys/live/messages.py tests/live/test_messages.py
git commit -m "feat(live): 协议补上成交编号与未成交余量

新增字段全部带默认值——跨机协议升级期间两侧版本必然不同步，
旧代理发来的旧消息不能让新核心崩掉。"
```

---

## Task 2: FillTracker —— 从券商快照推导待发布回报

**Files:**
- Create: `src/qsys/agent/fill_tracker.py`
- Test: `tests/agent/test_fill_tracker.py`

**Interfaces:**
- Consumes: `FillEvent` / `RejectEvent`（Task 1）；`AShareFeeModel` 与 `Side`（既有）
- Produces:
  - `BrokerTrade(traded_id: str, client_order_id: str, broker_order_id: str, symbol: str, side: str, price: float, qty: int, at: datetime)`
  - `BrokerOrder(broker_order_id: str, client_order_id: str, symbol: str, side: str, ordered_qty: int, traded_qty: int, status: int, status_msg: str)`
  - `TrackerOutput(fills: tuple[FillEvent, ...], rejects: tuple[RejectEvent, ...], new_trade_ids: tuple[str, ...], closed_order_ids: tuple[str, ...])`
  - `FillTracker(fee_model).observe(trades, orders, *, seen_trade_ids: frozenset[str], closed_order_ids: frozenset[str], now: datetime) -> TrackerOutput`
  - `TERMINAL_STATUSES: frozenset[int]`、`STATUS_NAMES: dict[int, str]`

**这一步最容易做错的地方：最低佣金是按笔委托算的，不是按笔成交算的。**
一笔 1000 股的委托分三次成交，券商只收一次 5 元最低佣金。逐笔独立调
`fee_for` 会收三次。做法是按**累计成交额**算累计费用，再减去该委托已上报的费用。

- [ ] **Step 1: 写失败的测试**

创建 `tests/agent/test_fill_tracker.py`：

```python
from datetime import datetime

from qsys.agent.fill_tracker import (
    BrokerOrder,
    BrokerTrade,
    FillTracker,
    TERMINAL_STATUSES,
)
from qsys.backtest.fees import AShareFeeModel

T = datetime(2026, 9, 11, 9, 30)
FEES = AShareFeeModel()


def tracker() -> FillTracker:
    return FillTracker(FEES)


def trade(tid, oid="c-1", qty=100, price=10.0, at=T, side="buy"):
    return BrokerTrade(traded_id=tid, client_order_id=oid, broker_order_id="B1",
                       symbol="600036.SH", side=side, price=price, qty=qty, at=at)


def order(oid="c-1", ordered=100, traded=100, status=56, side="buy"):
    return BrokerOrder(broker_order_id="B1", client_order_id=oid,
                       symbol="600036.SH", side=side, ordered_qty=ordered,
                       traded_qty=traded, status=status, status_msg="")


def test_a_new_trade_becomes_a_fill():
    out = tracker().observe([trade("T1")], [order()],
                            seen_trade_ids=frozenset(), closed_order_ids=frozenset(),
                            now=T)
    assert len(out.fills) == 1
    assert out.fills[0].client_order_id == "c-1"
    assert out.fills[0].qty == 100
    assert out.fills[0].price == 10.0
    assert out.fills[0].broker_trade_id == "T1"
    assert out.new_trade_ids == ("T1",)


def test_an_already_reported_trade_is_not_reported_again():
    """轮询会反复看到同一笔成交——去重靠券商给的 traded_id。"""
    out = tracker().observe([trade("T1")], [order()],
                            seen_trade_ids=frozenset({"T1"}),
                            closed_order_ids=frozenset(), now=T)
    assert out.fills == ()
    assert out.new_trade_ids == ()


def test_minimum_commission_is_charged_once_per_order_not_per_trade():
    """一笔委托分三次成交，5 元最低佣金只收一次。"""
    trades = [trade("T1", qty=100), trade("T2", qty=100), trade("T3", qty=100)]
    out = tracker().observe(trades, [order(ordered=300, traded=300)],
                            seen_trade_ids=frozenset(),
                            closed_order_ids=frozenset(), now=T)
    total = sum(f.fee for f in out.fills)
    # 3000 元买入：佣金 max(0.9, 5) = 5，过户费 0.03，合计 5.03
    assert round(total, 2) == 5.03


def test_fee_already_reported_is_not_charged_twice():
    """第二轮轮询时前两笔已上报，新增的那笔只承担增量费用。"""
    trades = [trade("T1", qty=100), trade("T2", qty=100), trade("T3", qty=100)]
    out = tracker().observe(trades, [order(ordered=300, traded=300)],
                            seen_trade_ids=frozenset({"T1", "T2"}),
                            closed_order_ids=frozenset(), now=T)
    assert len(out.fills) == 1
    # 前 2000 元已收 5.02，累计 3000 元应收 5.03，增量 0.01
    assert round(out.fills[0].fee, 2) == 0.01


def test_a_cancelled_order_with_no_fill_becomes_a_reject():
    out = tracker().observe([], [order(ordered=1000, traded=0, status=54)],
                            seen_trade_ids=frozenset(),
                            closed_order_ids=frozenset(), now=T)
    assert len(out.rejects) == 1
    assert out.rejects[0].unfilled_qty == 1000
    assert "已撤" in out.rejects[0].reason
    assert out.closed_order_ids == ("c-1",)


def test_a_partially_filled_then_cancelled_order_rejects_only_the_remainder():
    out = tracker().observe([trade("T1", qty=300)],
                            [order(ordered=1000, traded=300, status=52)],
                            seen_trade_ids=frozenset(),
                            closed_order_ids=frozenset(), now=T)
    assert len(out.fills) == 1
    assert out.fills[0].qty == 300
    assert len(out.rejects) == 1
    assert out.rejects[0].unfilled_qty == 700


def test_a_fully_filled_order_produces_no_reject():
    out = tracker().observe([trade("T1")], [order(ordered=100, traded=100, status=56)],
                            seen_trade_ids=frozenset(),
                            closed_order_ids=frozenset(), now=T)
    assert out.rejects == ()
    assert out.closed_order_ids == ("c-1",)


def test_an_order_still_working_is_not_closed():
    """部成（55）还在挂着，不能当成终态上报未成交。"""
    out = tracker().observe([trade("T1", qty=300)],
                            [order(ordered=1000, traded=300, status=55)],
                            seen_trade_ids=frozenset(),
                            closed_order_ids=frozenset(), now=T)
    assert out.rejects == ()
    assert out.closed_order_ids == ()


def test_an_already_closed_order_is_not_rejected_again():
    out = tracker().observe([], [order(ordered=1000, traded=0, status=54)],
                            seen_trade_ids=frozenset(),
                            closed_order_ids=frozenset({"c-1"}), now=T)
    assert out.rejects == ()
    assert out.closed_order_ids == ()


def test_a_junk_order_is_rejected_with_the_broker_message():
    out = tracker().observe([], [BrokerOrder(
        broker_order_id="B1", client_order_id="c-1", symbol="600036.SH",
        side="buy", ordered_qty=100, traded_qty=0, status=57,
        status_msg="资金不足")],
        seen_trade_ids=frozenset(), closed_order_ids=frozenset(), now=T)
    assert len(out.rejects) == 1
    assert "废单" in out.rejects[0].reason
    assert "资金不足" in out.rejects[0].reason


def test_trades_are_processed_in_chronological_order():
    """费用增量依赖累计额，顺序错了金额就错。"""
    late = trade("T2", qty=100, at=datetime(2026, 9, 11, 14, 0))
    early = trade("T1", qty=100, at=datetime(2026, 9, 11, 9, 31))
    out = tracker().observe([late, early], [order(ordered=200, traded=200)],
                            seen_trade_ids=frozenset(),
                            closed_order_ids=frozenset(), now=T)
    assert [f.broker_trade_id for f in out.fills] == ["T1", "T2"]


def test_a_sell_pays_stamp_duty():
    out = tracker().observe(
        [trade("T1", qty=1000, price=100.0, side="sell")],
        [order(ordered=1000, traded=1000, side="sell")],
        seen_trade_ids=frozenset(), closed_order_ids=frozenset(), now=T)
    # 100,000 元卖出：佣金 30 + 印花税 50 + 过户费 1 = 81
    assert round(out.fills[0].fee, 2) == 81.0


def test_terminal_statuses_match_the_broker_constants():
    assert TERMINAL_STATUSES == frozenset({52, 53, 54, 56, 57})


def test_a_trade_without_a_matching_order_still_becomes_a_fill():
    """委托查询可能比成交查询晚一拍。成交是既成事实，不能因为查不到委托就丢。"""
    out = tracker().observe([trade("T1")], [],
                            seen_trade_ids=frozenset(),
                            closed_order_ids=frozenset(), now=T)
    assert len(out.fills) == 1
    assert out.fills[0].qty == 100
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/agent/test_fill_tracker.py -q
```

Expected: FAIL，`ModuleNotFoundError: No module named 'qsys.agent.fill_tracker'`

- [ ] **Step 3: 实现**

创建 `src/qsys/agent/fill_tracker.py`：

```python
"""从券商快照推导待发布的成交与拒单回报。

**为什么轮询而不是回调**：xtquant 的成交回报是异步回调，但回调在代理
重启期间发生的成交会永久丢失。`query_stock_trades` 每次返回当日全部成交，
配合券商给的 `traded_id` 去重，重启后能自动补齐。去重键由券商提供，
我们不需要自己保证顺序或唯一性。

本模块**不做任何 I/O**：输入是券商快照与已上报集合，输出是待发布消息。
去重集合的持久化在 report_ledger.py，券商字段的翻译在 xtquant_adapter.py。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from qsys.backtest.fees import FeeModel
from qsys.core.trading import Side, to_cents
from qsys.live.messages import FillEvent, RejectEvent

# 委托状态。数值取自 xtconstant，已在 Windows 上实测核对。
STATUS_NAMES: dict[int, str] = {
    48: "未报", 49: "待报", 50: "已报", 51: "已报待撤",
    52: "部成待撤", 53: "部撤", 54: "已撤", 55: "部成",
    56: "已成", 57: "废单",
}

# 终态：不会再有新成交。其余状态仍在挂着，不能据此判定未成交。
TERMINAL_STATUSES: frozenset[int] = frozenset({52, 53, 54, 56, 57})


@dataclass(frozen=True, slots=True)
class BrokerTrade:
    traded_id: str
    client_order_id: str
    broker_order_id: str
    symbol: str
    side: str
    price: float
    qty: int
    at: datetime


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: str
    ordered_qty: int
    traded_qty: int
    status: int
    status_msg: str


@dataclass(frozen=True, slots=True)
class TrackerOutput:
    fills: tuple[FillEvent, ...]
    rejects: tuple[RejectEvent, ...]
    new_trade_ids: tuple[str, ...]
    closed_order_ids: tuple[str, ...]


class FillTracker:
    def __init__(self, fee_model: FeeModel) -> None:
        self._fees = fee_model

    def observe(
        self,
        trades: list[BrokerTrade],
        orders: list[BrokerOrder],
        *,
        seen_trade_ids: frozenset[str],
        closed_order_ids: frozenset[str],
        now: datetime,
    ) -> TrackerOutput:
        fills = self._fills(trades, seen_trade_ids)
        rejects, closed = self._closures(orders, closed_order_ids, now)
        return TrackerOutput(
            fills=tuple(fills),
            rejects=tuple(rejects),
            new_trade_ids=tuple(f.broker_trade_id for f in fills if f.broker_trade_id),
            closed_order_ids=tuple(closed),
        )

    def _fills(
        self, trades: list[BrokerTrade], seen: frozenset[str]
    ) -> list[FillEvent]:
        # 按时间排序：费用是按累计成交额的增量算的，顺序错了金额就错。
        # traded_id 作次级键，保证同一毫秒内的多笔成交顺序稳定。
        ordered = sorted(trades, key=lambda t: (t.at, t.traded_id))

        cumulative: dict[str, float] = {}   # client_order_id -> 累计成交额
        out: list[FillEvent] = []
        for trade in ordered:
            gross = to_cents(trade.price * trade.qty)
            before = cumulative.get(trade.client_order_id, 0.0)
            after = to_cents(before + gross)
            cumulative[trade.client_order_id] = after
            if trade.traded_id in seen:
                # 已上报过，但累计额仍要累进——否则后面那笔会重复承担最低佣金
                continue
            out.append(
                FillEvent(
                    client_order_id=trade.client_order_id,
                    symbol=trade.symbol,
                    side=trade.side,
                    qty=trade.qty,
                    price=trade.price,
                    fee=self._fee_delta(trade.side, before, after),
                    filled_at=trade.at,
                    broker_order_id=trade.broker_order_id,
                    broker_trade_id=trade.traded_id,
                )
            )
        return out

    def _fee_delta(self, side: str, before: float, after: float) -> float:
        """本笔应承担的费用 = 累计应收 − 之前已收。

        最低佣金是**按笔委托**收的：1000 股分三次成交，券商只收一次 5 元。
        逐笔独立调 fee_for 会收三次。
        """
        return to_cents(self._cum_fee(side, after) - self._cum_fee(side, before))

    def _cum_fee(self, side: str, gross: float) -> float:
        # fee_for(0) 会返回最低佣金 5 元，但零成交额不该收费
        if gross <= 0:
            return 0.0
        return self._fees.fee_for(Side(side), gross)

    def _closures(
        self, orders: list[BrokerOrder], closed: frozenset[str], now: datetime
    ) -> tuple[list[RejectEvent], list[str]]:
        rejects: list[RejectEvent] = []
        newly_closed: list[str] = []
        for order in orders:
            if order.status not in TERMINAL_STATUSES:
                continue
            if order.client_order_id in closed:
                continue
            newly_closed.append(order.client_order_id)
            unfilled = order.ordered_qty - order.traded_qty
            if unfilled <= 0:
                continue
            name = STATUS_NAMES.get(order.status, f"状态{order.status}")
            reason = f"{name}: 委托 {order.ordered_qty} 股，成交 {order.traded_qty} 股"
            if order.status_msg:
                reason = f"{reason}（{order.status_msg}）"
            rejects.append(
                RejectEvent(
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    reason=reason,
                    at=now,
                    side=order.side,
                    unfilled_qty=unfilled,
                )
            )
        return rejects, newly_closed
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/agent/test_fill_tracker.py -q
```

Expected: PASS（14 passed）

- [ ] **Step 5: 提交**

```bash
git add src/qsys/agent/fill_tracker.py tests/agent/test_fill_tracker.py
git commit -m "feat(agent): FillTracker——从券商快照推导真实成交回报

轮询而非回调：回调在代理重启期间的成交会永久丢失，
query_stock_trades 每次返回当日全部成交，配 traded_id 去重可自愈。

最低佣金按笔委托而非按笔成交收——按累计成交额算增量费用。"
```

---

## Task 3: ReportLedger —— 跨重启的去重台账

**Files:**
- Create: `src/qsys/agent/report_ledger.py`
- Test: `tests/agent/test_report_ledger.py`

**Interfaces:**
- Consumes: `Transport`（既有，`src/qsys/live/transport.py`）
- Produces: `ReportLedger(transport, prefix="agent:")`，方法
  `seen_trades(day: date) -> frozenset[str]`、`closed_orders(day: date) -> frozenset[str]`、
  `record(day: date, trade_ids: Sequence[str], order_ids: Sequence[str]) -> None`

按交易日分键：券商的 `query_stock_trades` 只返回当日成交，隔日集合自然作废，
不需要清理逻辑。

- [ ] **Step 1: 写失败的测试**

创建 `tests/agent/test_report_ledger.py`：

```python
from datetime import date

from qsys.agent.report_ledger import ReportLedger
from qsys.live.transport import MemoryTransport

DAY = date(2026, 9, 11)


def test_an_empty_ledger_reports_nothing_seen():
    ledger = ReportLedger(MemoryTransport())
    assert ledger.seen_trades(DAY) == frozenset()
    assert ledger.closed_orders(DAY) == frozenset()


def test_recorded_ids_come_back():
    ledger = ReportLedger(MemoryTransport())
    ledger.record(DAY, ["T1", "T2"], ["c-1"])
    assert ledger.seen_trades(DAY) == frozenset({"T1", "T2"})
    assert ledger.closed_orders(DAY) == frozenset({"c-1"})


def test_recording_accumulates_rather_than_replaces():
    ledger = ReportLedger(MemoryTransport())
    ledger.record(DAY, ["T1"], [])
    ledger.record(DAY, ["T2"], [])
    assert ledger.seen_trades(DAY) == frozenset({"T1", "T2"})


def test_a_new_ledger_on_the_same_transport_sees_the_history():
    """代理重启后必须认得昨天已上报的成交，否则会重复发布。"""
    transport = MemoryTransport()
    ReportLedger(transport).record(DAY, ["T1"], ["c-1"])
    assert ReportLedger(transport).seen_trades(DAY) == frozenset({"T1"})


def test_days_are_isolated():
    """券商的当日成交查询隔日归零，台账也按日分键，不需要清理逻辑。"""
    ledger = ReportLedger(MemoryTransport())
    ledger.record(DAY, ["T1"], [])
    assert ledger.seen_trades(date(2026, 9, 14)) == frozenset()


def test_recording_nothing_does_not_create_a_key():
    transport = MemoryTransport()
    ReportLedger(transport).record(DAY, [], [])
    assert transport.get_key("agent:trades:20260911") is None
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/agent/test_report_ledger.py -q
```

Expected: FAIL，`ModuleNotFoundError: No module named 'qsys.agent.report_ledger'`

- [ ] **Step 3: 实现**

创建 `src/qsys/agent/report_ledger.py`：

```python
"""已上报回报的去重台账。

代理重启后必须认得哪些成交已经发布过，否则核心侧会收到重复成交、
持仓推演直接翻倍。台账落在 Transport 的键空间上，与消息流同生命周期。

**按交易日分键**：券商的 `query_stock_trades` 只返回当日成交，
隔日集合自然作废，因此不需要任何清理逻辑。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date

from qsys.live.transport import Transport


class ReportLedger:
    def __init__(self, transport: Transport, *, prefix: str = "agent:") -> None:
        self._t = transport
        self._prefix = prefix

    def _trades_key(self, day: date) -> str:
        return f"{self._prefix}trades:{day:%Y%m%d}"

    def _orders_key(self, day: date) -> str:
        return f"{self._prefix}closed:{day:%Y%m%d}"

    def _load(self, key: str) -> frozenset[str]:
        raw = self._t.get_key(key)
        if not raw:
            return frozenset()
        return frozenset(json.loads(raw))

    def seen_trades(self, day: date) -> frozenset[str]:
        return self._load(self._trades_key(day))

    def closed_orders(self, day: date) -> frozenset[str]:
        return self._load(self._orders_key(day))

    def record(
        self, day: date, trade_ids: Sequence[str], order_ids: Sequence[str]
    ) -> None:
        for key, ids in ((self._trades_key(day), trade_ids),
                         (self._orders_key(day), order_ids)):
            if not ids:
                continue
            merged = self._load(key) | set(ids)
            self._t.set_key(key, json.dumps(sorted(merged)))
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/agent/test_report_ledger.py -q
```

Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add src/qsys/agent/report_ledger.py tests/agent/test_report_ledger.py
git commit -m "feat(agent): 回报去重台账，代理重启不会重复发布成交

按交易日分键——券商当日成交查询隔日归零，集合自然作废，无需清理逻辑。"
```

---

## Task 4: BrokerAdapter 协议扩容与 FakeAdapter

**Files:**
- Modify: `src/qsys/agent/adapter.py`
- Test: `tests/agent/test_execution_agent.py`（既有 `make()` 需要跟着改）

**Interfaces:**
- Consumes: `BrokerTrade` / `BrokerOrder`（Task 2）
- Produces: `BrokerAdapter.query_trades() -> list[BrokerTrade]`、
  `BrokerAdapter.query_orders() -> list[BrokerOrder]`、
  `BrokerAdapter.place_order(..., client_order_id: str) -> str`；
  `FakeAdapter(fills_on_place: bool = False, ...)` 与 `FakeAdapter.settle(...)`

`place_order` 增加 `client_order_id` 形参——它要作为 `order_remark` 传给券商，
成交回报靠它认领委托。

- [ ] **Step 1: 写失败的测试**

在 `tests/agent/test_execution_agent.py` 顶部追加导入与新测试：

```python
from qsys.agent.fill_tracker import BrokerOrder, BrokerTrade


def test_fake_adapter_records_the_client_order_id():
    """client_order_id 要作为 order_remark 传给券商，成交回报靠它认领委托。"""
    from qsys.agent.adapter import FakeAdapter

    adapter = FakeAdapter()
    adapter.place_order("600036.SH", "buy", 100, "market", None, "c-1")
    assert adapter.placed[0].client_order_id == "c-1"


def test_fake_adapter_settles_into_queryable_trades():
    from qsys.agent.adapter import FakeAdapter

    adapter = FakeAdapter()
    broker_id = adapter.place_order("600036.SH", "buy", 100, "limit", 10.0, "c-1")
    adapter.settle("c-1", qty=100, price=10.0)
    trades = adapter.query_trades()
    assert len(trades) == 1
    assert trades[0].client_order_id == "c-1"
    assert trades[0].broker_order_id == broker_id
    assert trades[0].qty == 100
    orders = adapter.query_orders()
    assert orders[0].traded_qty == 100
    assert orders[0].status == 56


def test_fake_adapter_can_settle_partially_then_cancel():
    from qsys.agent.adapter import FakeAdapter

    adapter = FakeAdapter()
    adapter.place_order("600036.SH", "buy", 1000, "limit", 10.0, "c-1")
    adapter.settle("c-1", qty=300, price=10.0)
    assert adapter.query_orders()[0].status == 55      # 部成，还挂着
    adapter.cancel_all()
    assert adapter.query_orders()[0].status == 52      # 部成待撤，终态
    assert adapter.query_orders()[0].traded_qty == 300
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/agent/test_execution_agent.py -q
```

Expected: FAIL，`TypeError: place_order() takes 6 positional arguments but 7 were given`

- [ ] **Step 3: 实现**

把 `src/qsys/agent/adapter.py` 整体替换为：

```python
"""券商适配器协议与测试替身。

真实的 xtquant 适配器在 xtquant_adapter.py（仅 Windows 可导入）。
单元测试一律用 FakeAdapter——不得导入 xtquant。

**协议里没有"下单即成交"这回事**：`place_order` 只返回券商委托号，
成交要靠 `query_trades()` 轮询出来。这是真实券商的行为，
测试替身也必须照此模拟，否则测试会掩盖真实链路的时序问题。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from qsys.agent.fill_tracker import BrokerOrder, BrokerTrade
from qsys.live.messages import PositionSnapshot


class BrokerAdapter(Protocol):
    def place_order(
        self, symbol: str, side: str, qty: int, order_type: str,
        limit_price: float | None, client_order_id: str,
    ) -> str:
        """下单，返回券商委托号。失败时抛异常。

        client_order_id 作为 order_remark 传给券商，会原样出现在
        委托与成交记录上——成交回报靠它认领是哪一笔委托。
        """

    def cancel_all(self) -> None: ...

    def query_trades(self) -> list[BrokerTrade]:
        """当日全部成交。每次返回全量，去重由调用方按 traded_id 做。"""

    def query_orders(self) -> list[BrokerOrder]:
        """当日全部委托，含终态。"""

    def query_account(self) -> tuple[float, dict[str, PositionSnapshot]]:
        """返回 (可用现金, 持仓字典)。"""


@dataclass(frozen=True, slots=True)
class PlacedOrder:
    symbol: str
    side: str
    qty: int
    order_type: str
    limit_price: float | None
    client_order_id: str


class FakeAdapter:
    """测试替身。

    刻意模拟真实券商的两段式：place_order 只登记委托，
    成交要显式调 settle() 才发生。
    """

    def __init__(
        self,
        *,
        cash: float = 100_000.0,
        positions: dict[str, tuple[int, int, float]] | None = None,
        fail_with: str | None = None,
        now: datetime | None = None,
    ) -> None:
        self.cash = cash
        self._positions = positions or {}
        self.fail_with = fail_with
        self.placed: list[PlacedOrder] = []
        self.cancelled = False
        self._now = now or datetime(2026, 9, 11, 9, 30)
        self._seq = 0
        self._trade_seq = 0
        self._orders: dict[str, BrokerOrder] = {}
        self._trades: list[BrokerTrade] = []

    def place_order(
        self, symbol: str, side: str, qty: int, order_type: str,
        limit_price: float | None, client_order_id: str,
    ) -> str:
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        self.placed.append(
            PlacedOrder(symbol, side, qty, order_type, limit_price, client_order_id)
        )
        self._seq += 1
        broker_id = f"B{self._seq}"
        self._orders[client_order_id] = BrokerOrder(
            broker_order_id=broker_id, client_order_id=client_order_id,
            symbol=symbol, side=side, ordered_qty=qty, traded_qty=0,
            status=50, status_msg="",     # 已报
        )
        return broker_id

    def settle(
        self, client_order_id: str, *, qty: int, price: float,
        at: datetime | None = None,
    ) -> BrokerTrade:
        """让某笔委托成交 qty 股。全部成交则状态转已成，否则转部成。"""
        order = self._orders[client_order_id]
        self._trade_seq += 1
        trade = BrokerTrade(
            traded_id=f"T{self._trade_seq}", client_order_id=client_order_id,
            broker_order_id=order.broker_order_id, symbol=order.symbol,
            side=order.side, price=price, qty=qty, at=at or self._now,
        )
        self._trades.append(trade)
        traded = order.traded_qty + qty
        self._orders[client_order_id] = BrokerOrder(
            broker_order_id=order.broker_order_id, client_order_id=client_order_id,
            symbol=order.symbol, side=order.side, ordered_qty=order.ordered_qty,
            traded_qty=traded,
            status=56 if traded >= order.ordered_qty else 55,
            status_msg="",
        )
        return trade

    def junk(self, client_order_id: str, reason: str) -> None:
        """让某笔委托变成废单。"""
        order = self._orders[client_order_id]
        self._orders[client_order_id] = BrokerOrder(
            broker_order_id=order.broker_order_id, client_order_id=client_order_id,
            symbol=order.symbol, side=order.side, ordered_qty=order.ordered_qty,
            traded_qty=order.traded_qty, status=57, status_msg=reason,
        )

    def cancel_all(self) -> None:
        self.cancelled = True
        for oid, order in list(self._orders.items()):
            if order.status in (50, 55):    # 已报 / 部成
                self._orders[oid] = BrokerOrder(
                    broker_order_id=order.broker_order_id, client_order_id=oid,
                    symbol=order.symbol, side=order.side,
                    ordered_qty=order.ordered_qty, traded_qty=order.traded_qty,
                    status=52 if order.traded_qty else 54,
                    status_msg="",
                )

    def query_trades(self) -> list[BrokerTrade]:
        return list(self._trades)

    def query_orders(self) -> list[BrokerOrder]:
        return list(self._orders.values())

    def query_account(self) -> tuple[float, dict[str, PositionSnapshot]]:
        return self.cash, {
            symbol: PositionSnapshot(total_qty=t, available_qty=a, avg_cost=c)
            for symbol, (t, a, c) in self._positions.items()
        }
```

- [ ] **Step 4: 改既有测试对 `adapter.placed` 的断言**

`tests/agent/test_execution_agent.py` 里 `test_agent_places_the_order` 断言的是
元组，现在是 `PlacedOrder`。改为：

```python
def test_agent_places_the_order():
    agent, t, adapter = make()
    t.append(STREAMS.orders, cmd().to_payload())
    assert agent.run_once() == 1
    assert len(adapter.placed) == 1
    placed = adapter.placed[0]
    assert (placed.symbol, placed.side, placed.qty, placed.order_type) == (
        "600036.SH", "buy", 100, "market")
    assert placed.client_order_id == "c-1"
```

`test_agent_publishes_a_fill` 断言的是"下单即成交"，与新行为冲突。
**删掉它** —— Task 5 会用真实回报路径的测试取代。

- [ ] **Step 5: 跑测试确认通过**

```bash
uv run pytest tests/agent -q
```

Expected: FAIL —— `execution_agent.py` 还在用旧的 `place_order` 签名。
这是预期的，Task 5 修。先确认 `test_fake_adapter_*` 三条通过：

```bash
uv run pytest tests/agent/test_execution_agent.py -q -k fake_adapter
```

Expected: PASS（3 passed）

- [ ] **Step 6: 提交**

```bash
git add src/qsys/agent/adapter.py tests/agent/test_execution_agent.py
git commit -m "feat(agent): 适配器协议改成两段式，下单不再等于成交

place_order 只登记委托，成交要 query_trades 轮询出来——这是真实券商的
行为，测试替身也必须照此模拟，否则测试会掩盖真实链路的时序问题。"
```

---

## Task 5: ExecutionAgent 发布真实回报

**Files:**
- Modify: `src/qsys/agent/execution_agent.py`
- Test: `tests/agent/test_execution_agent.py`

**Interfaces:**
- Consumes: `FillTracker` / `BrokerTrade` / `BrokerOrder`（Task 2）、`ReportLedger`（Task 3）、
  `BrokerAdapter.place_order(..., client_order_id)` / `query_trades` / `query_orders`（Task 4）
- Produces: `ExecutionAgent(transport, adapter, streams, *, fee_model=None, ledger=None, clock=None, ...)`、
  `ExecutionAgent.poll_reports() -> int`

**这一步的核心是删除，不是新增**：`_execute` 里那段"下单成功即发布 FillEvent"
必须整段拿掉。留着它，新旧两条路径会同时发布，核心侧持仓直接翻倍。

- [ ] **Step 1: 写失败的测试**

在 `tests/agent/test_execution_agent.py` 追加：

```python
def test_placing_an_order_publishes_no_fill():
    """下单成功不等于成交。留着旧的假回报会让持仓翻倍。"""
    agent, t, _ = make()
    t.append(STREAMS.orders, cmd().to_payload())
    agent.run_once()
    assert drain(t, STREAMS.fills, FillEvent) == []


def test_a_settled_trade_is_published_with_the_real_price():
    agent, t, adapter = make()
    t.append(STREAMS.orders, cmd().to_payload())
    agent.run_once()
    adapter.settle("c-1", qty=100, price=41.37)
    assert agent.poll_reports() == 1
    fills = drain(t, STREAMS.fills, FillEvent)
    assert len(fills) == 1
    assert fills[0].price == 41.37
    assert fills[0].qty == 100
    assert fills[0].broker_trade_id == "T1"


def test_polling_twice_does_not_republish():
    agent, t, adapter = make()
    t.append(STREAMS.orders, cmd().to_payload())
    agent.run_once()
    adapter.settle("c-1", qty=100, price=41.37)
    agent.poll_reports()
    assert agent.poll_reports() == 0
    assert len(drain(t, STREAMS.fills, FillEvent)) == 1


def test_a_partial_fill_publishes_only_what_traded():
    agent, t, adapter = make()
    t.append(STREAMS.orders, cmd(qty=1000).to_payload())
    agent.run_once()
    adapter.settle("c-1", qty=300, price=41.37)
    agent.poll_reports()
    fills = drain(t, STREAMS.fills, FillEvent)
    assert [f.qty for f in fills] == [300]
    # 还挂着，不该有拒单
    assert drain(t, STREAMS.rejects, RejectEvent) == []


def test_cancelling_a_partially_filled_order_rejects_the_remainder():
    agent, t, adapter = make()
    t.append(STREAMS.orders, cmd(qty=1000).to_payload())
    agent.run_once()
    adapter.settle("c-1", qty=300, price=41.37)
    agent.poll_reports()
    adapter.cancel_all()
    agent.poll_reports()
    rejects = drain(t, STREAMS.rejects, RejectEvent)
    assert len(rejects) == 1
    assert rejects[0].unfilled_qty == 700


def test_a_junk_order_is_reported_as_a_reject():
    agent, t, adapter = make()
    t.append(STREAMS.orders, cmd().to_payload())
    agent.run_once()
    adapter.junk("c-1", "资金不足")
    agent.poll_reports()
    rejects = drain(t, STREAMS.rejects, RejectEvent)
    assert len(rejects) == 1
    assert "资金不足" in rejects[0].reason


def test_a_restarted_agent_does_not_republish_settled_trades():
    """台账在 Transport 上，新建 agent 必须认得已上报的成交。"""
    from qsys.agent.execution_agent import ExecutionAgent

    agent, t, adapter = make()
    t.append(STREAMS.orders, cmd().to_payload())
    agent.run_once()
    adapter.settle("c-1", qty=100, price=41.37)
    agent.poll_reports()

    restarted = ExecutionAgent(t, adapter, STREAMS)
    assert restarted.poll_reports() == 0
    assert len(drain(t, STREAMS.fills, FillEvent)) == 1


def test_the_agent_does_not_pull_in_heavy_dependencies():
    """Windows 侧代理只允许 stdlib + pydantic + redis-py + xtquant。

    polars / duckdb 混进来会让 Windows 上的安装与排障成本陡增（spec §11）。
    """
    import subprocess
    import sys

    code = (
        "import qsys.agent.execution_agent, sys; "
        "bad = [m for m in ('polars', 'duckdb', 'pyarrow', 'pandas') if m in sys.modules]; "
        "print(','.join(bad))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.stdout.strip() == "", f"意外拉起了重依赖: {out.stdout.strip()}"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/agent/test_execution_agent.py -q
```

Expected: FAIL，`AttributeError: 'ExecutionAgent' object has no attribute 'poll_reports'`

- [ ] **Step 3: 实现**

`src/qsys/agent/execution_agent.py`：

**保留**既有的全部导入（`AccountSnapshot` / `ControlCommand` / `FillEvent` /
`Heartbeat` / `OrderCommand` / `RejectEvent` / `IdempotencyGuard` / `Transport`
等都还在用），把 `from datetime import datetime` 换成下面第一行，
再补上其余三行：

```python
from datetime import date, datetime          # 原来只导入 datetime

from qsys.agent.fill_tracker import FillTracker
from qsys.agent.report_ledger import ReportLedger
from qsys.backtest.fees import AShareFeeModel, FeeModel
```

`FillEvent` 的导入可以删掉——本文件不再构造它。

`__init__` 签名与尾部新增：

```python
    def __init__(
        self,
        transport: Transport,
        adapter: BrokerAdapter,
        streams: StreamConfig,
        *,
        group: str = "agent",
        consumer: str = "agent-1",
        guard: IdempotencyGuard | None = None,
        fee_model: FeeModel | None = None,
        ledger: ReportLedger | None = None,
    ) -> None:
        self._t = transport
        self._adapter = adapter
        self._s = streams
        self._group = group
        self._consumer = consumer
        self._guard = guard or IdempotencyGuard(transport)
        self._tracker = FillTracker(fee_model or AShareFeeModel())
        self._ledger = ledger or ReportLedger(transport)
        for stream in (streams.orders, streams.control):
            self._t.ensure_group(stream, group)
```

`_execute` 的下单调用带上 `client_order_id`，并**整段删除**结尾的 FillEvent 发布：

```python
    def _execute(self, command: OrderCommand) -> None:
        def place() -> str:
            return self._adapter.place_order(
                command.symbol, command.side, command.qty,
                command.order_type, command.limit_price, command.client_order_id,
            )

        try:
            broker_order_id, replayed = self._guard.process_once(
                command.client_order_id, place
            )
        except Exception as exc:
            # 失败不记入已处理集合，允许重试
            log.error("agent.place_failed", client_order_id=command.client_order_id,
                      error=str(exc))
            self._t.append(
                self._s.rejects,
                RejectEvent(
                    client_order_id=command.client_order_id,
                    symbol=command.symbol,
                    reason=str(exc),
                    at=datetime.now(),
                    side=command.side,
                    unfilled_qty=command.qty,
                ).to_payload(),
            )
            return

        if replayed:
            log.info("agent.duplicate_ignored", client_order_id=command.client_order_id)
            return

        # 这里**不发 FillEvent**。下单成功只是委托被受理，
        # 成交由 poll_reports() 从券商成交查询里读出来。
        log.info("agent.placed", client_order_id=command.client_order_id,
                 broker_order_id=broker_order_id)
```

新增 `poll_reports`（放在 `_execute` 之后、`_drain_control` 之前）：

```python
    def poll_reports(self) -> int:
        """把券商侧的真实成交与终态委托发布出去，返回发布条数。

        每轮都拉当日全量再按 traded_id 去重——代理重启期间发生的成交
        因此能自动补齐，这是轮询相对回调的唯一理由。
        """
        today = date.today()
        try:
            trades = self._adapter.query_trades()
            orders = self._adapter.query_orders()
        except Exception as exc:  # 查询失败不应终止代理，下一轮重试
            log.error("agent.query_reports_failed", error=str(exc))
            return 0

        out = self._tracker.observe(
            trades, orders,
            seen_trade_ids=self._ledger.seen_trades(today),
            closed_order_ids=self._ledger.closed_orders(today),
            now=datetime.now(),
        )
        for fill in out.fills:
            self._t.append(self._s.fills, fill.to_payload())
            log.info("agent.filled", client_order_id=fill.client_order_id,
                     qty=fill.qty, price=fill.price, fee=fill.fee,
                     broker_trade_id=fill.broker_trade_id)
        for reject in out.rejects:
            self._t.append(self._s.rejects, reject.to_payload())
            log.warning("agent.unfilled", client_order_id=reject.client_order_id,
                        unfilled_qty=reject.unfilled_qty, reason=reject.reason)

        # 先发布再记账：崩在中间会重发（核心侧按 broker_trade_id 幂等），
        # 反过来会漏发，而漏一笔成交是不可恢复的
        self._ledger.record(today, out.new_trade_ids, out.closed_order_ids)
        return len(out.fills) + len(out.rejects)
```

`publish_account` 与 `_drain_control` 不动。

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/agent -q
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/qsys/agent/execution_agent.py tests/agent/test_execution_agent.py
git commit -m "fix(agent): 删除假成交回报，改为轮询券商真实成交

下单成功只是委托被受理。旧代码在 place_order 返回后立刻发布 FillEvent，
价格取限价单的 limit_price、市价单为 0，且假定全额成交——部分成交、
废单、撤单一概不体现。第三层对账因此会在编造的价格上通过。

先发布再记账：崩在中间会重发（核心侧幂等），反过来会漏发，
而漏一笔成交是不可恢复的。"
```

---

## Task 6: XtQuantAdapter 实现两个查询

**Files:**
- Modify: `src/qsys/agent/xtquant_adapter.py`
- Test: `tests/agent/test_xtquant_mapping.py`（新建）

**Interfaces:**
- Consumes: `BrokerTrade` / `BrokerOrder`（Task 2）
- Produces: `to_broker_trade(raw, fallback_side: str) -> BrokerTrade`、
  `to_broker_order(raw) -> BrokerOrder`、`side_from_xt(order_type: int) -> str`

Windows 专有代码无法在 CI 上跑，所以把**字段翻译**抽成模块级纯函数，
用鸭子类型的假对象测。适配器类本身只剩下调用 xtquant 的那几行。

- [ ] **Step 1: 写失败的测试**

创建 `tests/agent/test_xtquant_mapping.py`：

```python
"""xtquant 字段翻译。不导入 xtquant——只测纯函数。"""

from datetime import datetime
from types import SimpleNamespace

from qsys.agent.xtquant_mapping import (
    side_from_xt,
    to_broker_order,
    to_broker_trade,
)


def test_buy_and_sell_constants_map_to_our_side_strings():
    assert side_from_xt(23) == "buy"
    assert side_from_xt(24) == "sell"


def test_an_unknown_direction_falls_back_to_buy_and_is_visible():
    """未知方向不能静默当成买——返回 None 让调用方决定。"""
    assert side_from_xt(99) is None


def test_a_trade_is_translated_with_the_client_order_id_from_the_remark():
    raw = SimpleNamespace(
        traded_id="T-9001", order_remark="20260911-a3f2-0007", order_id=12345,
        stock_code="600036.SH", order_type=23, traded_price=41.37,
        traded_volume=300, traded_amount=12411.0,
        traded_time=1789000000,
    )
    trade = to_broker_trade(raw)
    assert trade.traded_id == "T-9001"
    assert trade.client_order_id == "20260911-a3f2-0007"
    assert trade.broker_order_id == "12345"
    assert trade.side == "buy"
    assert trade.price == 41.37
    assert trade.qty == 300
    assert trade.at == datetime.fromtimestamp(1789000000)


def test_a_trade_without_a_remark_falls_back_to_the_broker_order_id():
    """手工在 QMT 里下的单没有 order_remark。不能因此丢掉这笔成交——
    核心侧认不出来会走到第一层对账的不一致告警，那是正确的去处。"""
    raw = SimpleNamespace(
        traded_id="T-1", order_remark="", order_id=777,
        stock_code="600036.SH", order_type=24, traded_price=10.0,
        traded_volume=100, traded_amount=1000.0, traded_time=1789000000,
    )
    assert to_broker_trade(raw).client_order_id == "xt:777"


def test_an_order_is_translated_with_its_status():
    raw = SimpleNamespace(
        order_id=12345, order_remark="c-1", stock_code="600036.SH",
        order_type=23, order_volume=1000, traded_volume=300,
        order_status=52, status_msg="部成待撤",
    )
    order = to_broker_order(raw)
    assert order.broker_order_id == "12345"
    assert order.client_order_id == "c-1"
    assert order.ordered_qty == 1000
    assert order.traded_qty == 300
    assert order.status == 52
    assert order.status_msg == "部成待撤"
    assert order.side == "buy"


def test_a_missing_status_msg_becomes_an_empty_string():
    raw = SimpleNamespace(
        order_id=1, order_remark="c-1", stock_code="600036.SH",
        order_type=23, order_volume=100, traded_volume=100,
        order_status=56, status_msg=None,
    )
    assert to_broker_order(raw).status_msg == ""
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/agent/test_xtquant_mapping.py -q
```

Expected: FAIL，`ModuleNotFoundError: No module named 'qsys.agent.xtquant_mapping'`

- [ ] **Step 3: 建纯函数模块**

创建 `src/qsys/agent/xtquant_mapping.py`：

```python
"""xtquant 数据结构到我们的类型的翻译。

**刻意不导入 xtquant**：Windows 专有代码在 CI 上跑不了，
把字段翻译抽出来才能测。适配器类里只剩下调 xtquant 的那几行。

字段名以 Windows 上实测的自省结果为准（见计划文档的"已核实的 xtquant 事实"）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from qsys.agent.fill_tracker import BrokerOrder, BrokerTrade

# 委托方向常量。23 买 / 24 卖。
_XT_TO_SIDE = {23: "buy", 24: "sell"}


def side_from_xt(order_type: int) -> str | None:
    """未知方向返回 None——静默当成买会造成反向持仓推演。"""
    return _XT_TO_SIDE.get(int(order_type))


def _client_id(raw: Any, broker_order_id: str) -> str:
    """order_remark 里带的是我们的 client_order_id。

    手工在 QMT 界面下的单没有 remark，退化成 `xt:<委托号>`——
    核心侧认不出这个 id，会走到第一层对账的不一致告警。那正是它该去的地方：
    人工干预必须被看见，而不是被吞掉。
    """
    remark = (getattr(raw, "order_remark", "") or "").strip()
    return remark or f"xt:{broker_order_id}"


def to_broker_trade(raw: Any) -> BrokerTrade:
    broker_order_id = str(raw.order_id)
    side = side_from_xt(raw.order_type)
    return BrokerTrade(
        traded_id=str(raw.traded_id),
        client_order_id=_client_id(raw, broker_order_id),
        broker_order_id=broker_order_id,
        symbol=str(raw.stock_code),
        side=side or "buy",
        price=float(raw.traded_price),
        qty=int(raw.traded_volume),
        at=datetime.fromtimestamp(int(raw.traded_time)),
    )


def to_broker_order(raw: Any) -> BrokerOrder:
    broker_order_id = str(raw.order_id)
    side = side_from_xt(raw.order_type)
    return BrokerOrder(
        broker_order_id=broker_order_id,
        client_order_id=_client_id(raw, broker_order_id),
        symbol=str(raw.stock_code),
        side=side or "buy",
        ordered_qty=int(raw.order_volume),
        traded_qty=int(raw.traded_volume),
        status=int(raw.order_status),
        status_msg=str(getattr(raw, "status_msg", "") or ""),
    )
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/agent/test_xtquant_mapping.py -q
```

Expected: PASS（6 passed）

- [ ] **Step 5: 接进适配器**

`src/qsys/agent/xtquant_adapter.py`：删掉文件头那段"已知待办"（它现在被解决了），
改成：

```python
"""真实 xtquant 适配器。仅 Windows 可导入。

成交回报是**异步**的：order_stock 只返回委托号，真实成交价与数量由
券商推送。这里不订阅回调，而是每轮拉 query_stock_trades / query_stock_orders
的当日全量，由 FillTracker 按 traded_id 去重——回调在代理重启期间的成交
会永久丢失，轮询能自愈。

字段翻译在 xtquant_mapping.py（纯函数，可在 CI 上测）。
本文件只剩调用 xtquant 的那几行。
"""

from __future__ import annotations

from qsys.agent.fill_tracker import BrokerOrder, BrokerTrade
from qsys.agent.xtquant_mapping import to_broker_order, to_broker_trade
from qsys.live.messages import PositionSnapshot

_SIDE_TO_XT = {"buy": 23, "sell": 24}  # xtquant 委托方向常量
_REMARK_LIMIT = 24  # 券商对 order_remark 的常见长度上限


class XtQuantAdapter:
    def __init__(self, account_id: str, mini_qmt_path: str) -> None:
        from xtquant.xttrader import XtQuantTrader
        from xtquant.xttype import StockAccount

        self._trader = XtQuantTrader(mini_qmt_path, session_id=int(account_id[-6:]))
        self._account = StockAccount(account_id)
        self._trader.start()
        if self._trader.connect() != 0:
            raise RuntimeError("miniQMT 连接失败")
        self._trader.subscribe(self._account)

    def place_order(
        self, symbol: str, side: str, qty: int, order_type: str,
        limit_price: float | None, client_order_id: str,
    ) -> str:
        from xtquant import xtconstant

        if len(client_order_id) > _REMARK_LIMIT:
            raise ValueError(
                f"client_order_id 超过 {_REMARK_LIMIT} 字节，券商会截断，"
                f"成交回报将认不出委托: {client_order_id!r}"
            )
        price_type = (
            xtconstant.FIX_PRICE if order_type == "limit" else xtconstant.LATEST_PRICE
        )
        order_id = self._trader.order_stock(
            self._account, symbol, _SIDE_TO_XT[side], qty,
            price_type, limit_price or 0.0,
            "qsys", client_order_id,
        )
        if order_id < 0:
            raise RuntimeError(f"下单被拒: order_id={order_id}")
        return str(order_id)

    def cancel_all(self) -> None:
        for order in self._trader.query_stock_orders(self._account, cancelable_only=True):
            self._trader.cancel_order_stock(self._account, order.order_id)

    def query_trades(self) -> list[BrokerTrade]:
        return [to_broker_trade(t)
                for t in self._trader.query_stock_trades(self._account) or []]

    def query_orders(self) -> list[BrokerOrder]:
        return [to_broker_order(o)
                for o in self._trader.query_stock_orders(self._account) or []]

    def query_account(self) -> tuple[float, dict[str, PositionSnapshot]]:
        asset = self._trader.query_stock_asset(self._account)
        positions = {
            p.stock_code: PositionSnapshot(
                total_qty=p.volume, available_qty=p.can_use_volume,
                avg_cost=p.open_price,
            )
            for p in self._trader.query_stock_positions(self._account)
        }
        return float(asset.cash), positions
```

- [ ] **Step 6: 跑全套**

```bash
uv run pytest -q -p no:warnings
```

Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/qsys/agent/xtquant_adapter.py src/qsys/agent/xtquant_mapping.py \
        tests/agent/test_xtquant_mapping.py
git commit -m "feat(agent): xtquant 实现成交/委托查询，client_order_id 走 order_remark

字段翻译抽成纯函数模块——Windows 专有代码在 CI 上跑不了，抽出来才能测。
order_remark 有长度上限，超了券商会截断、回报就认不出委托，故显式校验。"
```

---

## Task 7: LiveBroker 支持部分成交

**Files:**
- Modify: `src/qsys/live/live_broker.py:56-57,108-145`
- Test: `tests/live/test_live_broker.py`

**Interfaces:**
- Consumes: `FillEvent.broker_trade_id`、`RejectEvent.unfilled_qty` / `.side`（Task 1）
- Produces: 行为变化，无新公开接口

现状 `poll_fills` 收到第一笔成交就把委托从 `_pending` 弹出。一笔委托分三次成交时，
后两笔的拒单会认不出 side 与 qty。

- [ ] **Step 1: 写失败的测试**

在 `tests/live/test_live_broker.py` 追加。该文件既有的助手是
`make(tmp_path, **kw) -> (broker, transport, kill_switch)` 与
`order(side=Side.BUY, qty=100, oid="o1") -> Order`，沿用它们：

```python
def publish_fill(transport, oid, *, qty, price):
    transport.append(STREAMS.fills, FillEvent(
        client_order_id=oid, symbol="600036.SH", side="buy", qty=qty,
        price=price, fee=0.0, filled_at=T,
    ).to_payload())


def publish_reject(transport, oid, *, unfilled_qty, side):
    transport.append(STREAMS.rejects, RejectEvent(
        client_order_id=oid, symbol="600036.SH", reason="已撤",
        at=T, side=side, unfilled_qty=unfilled_qty,
    ).to_payload())


def test_a_partially_filled_order_stays_pending(tmp_path):
    """一笔委托分多次成交，累计到位之前不能出队。"""
    b, t, _ = make(tmp_path)
    b.submit(order(qty=1000))
    publish_fill(t, "o1", qty=300, price=10.0)
    assert len(b.poll_fills()) == 1
    assert "o1" in b.pending_order_ids()


def test_an_order_leaves_pending_once_fully_filled(tmp_path):
    b, t, _ = make(tmp_path)
    b.submit(order(qty=1000))
    publish_fill(t, "o1", qty=300, price=10.0)
    publish_fill(t, "o1", qty=700, price=10.0)
    b.poll_fills()
    assert "o1" not in b.pending_order_ids()


def test_a_reject_after_a_partial_fill_carries_the_remainder(tmp_path):
    b, t, _ = make(tmp_path)
    b.submit(order(qty=1000))
    publish_fill(t, "o1", qty=300, price=10.0)
    b.poll_fills()
    publish_reject(t, "o1", unfilled_qty=700, side="buy")
    rejects = b.poll_rejects()
    assert len(rejects) == 1
    assert rejects[0].qty == 700
    assert "o1" not in b.pending_order_ids()


def test_a_reject_without_an_unfilled_qty_falls_back_to_the_order_qty(tmp_path):
    """下单失败的拒单没有 unfilled_qty——退回委托全量。"""
    b, t, _ = make(tmp_path)
    b.submit(order(qty=1000))
    publish_reject(t, "o1", unfilled_qty=None, side=None)
    assert b.poll_rejects()[0].qty == 1000
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/live/test_live_broker.py -q
```

Expected: FAIL，`AttributeError: 'LiveBroker' object has no attribute 'pending_order_ids'`

- [ ] **Step 3: 实现**

`src/qsys/live/live_broker.py`，`__init__` 里 `self._pending` 之后加一行：

```python
        self._pending: dict[str, Order] = {}
        # client_order_id -> 累计已成交股数。部分成交时委托要留在 _pending 里，
        # 否则后续成交与拒单认不出方向与数量。
        self._filled: dict[str, int] = {}
```

`submit` 里 `self._pending[order.order_id] = order` 之后加：

```python
        self._filled[order.order_id] = 0
```

`poll_fills` 改为：

```python
    def poll_fills(self) -> tuple[Fill, ...]:
        out: list[Fill] = []
        for msg in self._t.read_group(self._s.fills, self._group, self._consumer, count=100):
            event = FillEvent.from_payload(msg.payload)
            self._t.ack(self._s.fills, self._group, msg.id)
            order = self._pending.get(event.client_order_id)
            done = self._filled.get(event.client_order_id, 0) + event.qty
            self._filled[event.client_order_id] = done
            # 累计到位才出队；部分成交时委托仍在挂着
            if order is not None and done >= order.qty:
                self._pending.pop(event.client_order_id, None)
                self._filled.pop(event.client_order_id, None)
            out.append(
                Fill(
                    order_id=event.client_order_id,
                    symbol=Symbol.parse(event.symbol),
                    side=Side(event.side),
                    qty=event.qty,
                    price=event.price,
                    fee=event.fee,
                    filled_at=event.filled_at,
                )
            )
        return tuple(out)
```

`poll_rejects` 里构造 `Reject` 的那段改为：

```python
            order = self._pending.pop(event.client_order_id, None)
            self._filled.pop(event.client_order_id, None)
            qty = event.unfilled_qty
            if qty is None:
                qty = order.qty if order else 0
            side = Side(event.side) if event.side else (order.side if order else Side.BUY)
            out.append(
                Reject(
                    order_id=event.client_order_id,
                    symbol=Symbol.parse(event.symbol),
                    side=side,
                    qty=qty,
                    reason=event.reason,
```

在类尾部新增自省方法：

```python
    def pending_order_ids(self) -> frozenset[str]:
        """尚未终结的委托号。供对账与测试断言使用。"""
        return frozenset(self._pending)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/live -q
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/qsys/live/live_broker.py tests/live/test_live_broker.py
git commit -m "fix(live): 委托累计成交到位才出队，部分成交不再丢失上下文

旧代码收到第一笔成交就把委托从 _pending 弹出。一笔委托分三次成交时，
后两笔的拒单认不出 side 与 qty，冻结额度归还就是错的。"
```

---

## Task 8: 跨重启唯一的委托号

**Files:**
- Create: `src/qsys/core/order_ids.py`
- Modify: `src/qsys/backtest/engine.py:202,212-216`
- Test: `tests/core/test_order_ids.py`

**Interfaces:**
- Produces: `OrderIdFactory`（Protocol，`__call__() -> str`）、
  `SequentialOrderIds(prefix: str = "o")`、`LiveOrderIds(day: date | None = None)`
- Modifies: `Engine.__init__(..., order_ids: OrderIdFactory | None = None)`

**为什么必须做**：现在委托号是 `f"o{order_seq}"`，每次进程启动都从 `o1` 开始。
`IdempotencyGuard` 以 client_order_id 为键，同日重启后第一笔单会被判成重放而
**静默丢弃** —— 一笔真实委托凭空消失，且日志里只写 "duplicate_ignored"。
回测里这没问题（单进程单次），实盘里这是丢单。

长度必须 ≤ 24 字节（Task 6 的 `_REMARK_LIMIT`）。
`20260911-a3f2-0007` = 18 字节。

- [ ] **Step 1: 写失败的测试**

创建 `tests/core/test_order_ids.py`：

```python
from datetime import date

from qsys.core.order_ids import LiveOrderIds, SequentialOrderIds

REMARK_LIMIT = 24


def test_sequential_ids_are_stable_and_readable():
    """回测要可复现，序号必须确定。"""
    ids = SequentialOrderIds()
    assert [ids(), ids(), ids()] == ["o1", "o2", "o3"]


def test_two_sequential_factories_produce_the_same_ids():
    assert SequentialOrderIds()() == SequentialOrderIds()()


def test_live_ids_carry_the_date():
    ids = LiveOrderIds(day=date(2026, 9, 11))
    assert ids().startswith("20260911-")


def test_live_ids_fit_the_broker_remark_limit():
    ids = LiveOrderIds(day=date(2026, 9, 11))
    for _ in range(10_000):
        value = ids()
    assert len(value) <= REMARK_LIMIT


def test_two_live_factories_on_the_same_day_do_not_collide():
    """同日重启不能复用旧号——否则幂等守卫会把新单当成重放而丢弃。"""
    a = LiveOrderIds(day=date(2026, 9, 11))
    b = LiveOrderIds(day=date(2026, 9, 11))
    assert {a() for _ in range(50)}.isdisjoint({b() for _ in range(50)})


def test_live_ids_are_unique_within_a_factory():
    ids = LiveOrderIds(day=date(2026, 9, 11))
    assert len({ids() for _ in range(1000)}) == 1000
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/core/test_order_ids.py -q
```

Expected: FAIL，`ModuleNotFoundError: No module named 'qsys.core.order_ids'`

- [ ] **Step 3: 实现**

创建 `src/qsys/core/order_ids.py`：

```python
"""委托号工厂。

回测与实盘对委托号的要求相反：回测要**可复现**（同样输入产生同样的号，
否则落盘文件无法逐字节比对），实盘要**跨进程唯一**（同日重启复用旧号，
IdempotencyGuard 会把新单当成重放而静默丢弃——一笔真实委托凭空消失）。

券商对 order_remark 有长度上限（常见 24 字节），实盘号为此设计得很短。
"""

from __future__ import annotations

import secrets
from datetime import date
from itertools import count
from typing import Protocol


class OrderIdFactory(Protocol):
    def __call__(self) -> str: ...


class SequentialOrderIds:
    """回测用：o1、o2、o3……可读且可复现。"""

    def __init__(self, prefix: str = "o") -> None:
        self._prefix = prefix
        self._seq = count(1)

    def __call__(self) -> str:
        return f"{self._prefix}{next(self._seq)}"


class LiveOrderIds:
    """实盘用：`<YYYYMMDD>-<run>-<seq>`，如 `20260911-a3f2-0007`。

    `run` 是进程启动时生成的 4 位随机后缀。同日重启会得到不同的 run，
    因此不可能与上一进程的号相撞。
    """

    def __init__(self, day: date | None = None, *, run: str | None = None) -> None:
        self._day = day or date.today()
        self._run = run or secrets.token_hex(2)
        self._seq = count(1)

    def __call__(self) -> str:
        return f"{self._day:%Y%m%d}-{self._run}-{next(self._seq):04d}"
```

- [ ] **Step 4: 接进引擎**

`src/qsys/backtest/engine.py`：`Engine.__init__` 增加形参并存下来
（放在既有形参之后，带默认值，既有调用方一字不动）：

```python
        order_ids: OrderIdFactory | None = None,
```

```python
        self._order_ids = order_ids or SequentialOrderIds()
```

顶部加导入：

```python
from qsys.core.order_ids import OrderIdFactory, SequentialOrderIds
```

`run` 里删掉 `order_seq = 0`（第 202 行），`flush` 改为：

```python
        def flush(api, at: datetime) -> None:
            for intent in api.drain():
                broker.submit(Order(self._order_ids(), intent.symbol, intent.side,
                                    intent.qty, intent.order_type, intent.limit_price, at))
```

删掉 `flush` 里的 `nonlocal order_seq` 与 `order_seq += 1`。

- [ ] **Step 5: 跑全套确认回测未变**

```bash
uv run pytest -q -p no:warnings
```

Expected: PASS —— 委托号仍是 `o1, o2, ...`，既有断言不受影响。

- [ ] **Step 6: 提交**

```bash
git add src/qsys/core/order_ids.py tests/core/test_order_ids.py \
        src/qsys/backtest/engine.py
git commit -m "feat(core): 委托号工厂，实盘号跨进程重启唯一

旧的 f'o{seq}' 每次启动都从 o1 开始。同日重启后第一笔单会被
IdempotencyGuard 判成重放而静默丢弃——一笔真实委托凭空消失，
日志里只写 duplicate_ignored。回测继续用可复现的序号。"
```

---

## Task 9: 费率进部署配置并接线 run_agent

**Files:**
- Modify: `src/qsys/live/config.py`
- Modify: `config/deploy.example.toml`
- Modify: `scripts/run_agent.py`
- Test: `tests/live/test_config.py`

**Interfaces:**
- Consumes: `AShareFeeModel`（既有）、`ExecutionAgent(..., fee_model=...)`（Task 5）
- Produces: `FeeConfig(commission_rate, min_commission, stamp_duty_rate, transfer_fee_rate)`、
  `DeployConfig.fees: FeeConfig`

费率因人而异（券商佣金谈到多少是私人信息），必须可配。默认值与
`AShareFeeModel` 的默认值一致，不填就是常见档位。

- [ ] **Step 1: 写失败的测试**

在 `tests/live/test_config.py` 追加：

```python
def test_fees_default_to_the_common_rates(tmp_path):
    path = tmp_path / "deploy.toml"
    path.write_text('[redis]\nhost = "127.0.0.1"\n')
    cfg = load_deploy_config(path)
    assert cfg.fees.commission_rate == 0.0003
    assert cfg.fees.min_commission == 5.0
    assert cfg.fees.stamp_duty_rate == 0.0005
    assert cfg.fees.transfer_fee_rate == 0.00001


def test_fees_can_be_overridden(tmp_path):
    """佣金谈到多少是私人信息，必须可配。"""
    path = tmp_path / "deploy.toml"
    path.write_text(
        '[redis]\nhost = "127.0.0.1"\n\n'
        "[fees]\ncommission_rate = 0.000854\nmin_commission = 0.1\n"
    )
    cfg = load_deploy_config(path)
    assert cfg.fees.commission_rate == 0.000854
    assert cfg.fees.min_commission == 0.1
    # 没写的仍取默认
    assert cfg.fees.stamp_duty_rate == 0.0005
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/live/test_config.py -q
```

Expected: FAIL，`AttributeError: 'DeployConfig' object has no attribute 'fees'`

- [ ] **Step 3: 实现**

`src/qsys/live/config.py`，在 `StreamConfig` 之后新增：

```python
@dataclass(frozen=True, slots=True)
class FeeConfig:
    """券商费率。默认值为 A 股常见档位，佣金因人而异必须可配。

    xtquant 的成交记录里没有手续费字段，实盘费用由这组费率算出来——
    费率是确定的，算出来的就是券商会扣的。
    """

    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_duty_rate: float = 0.0005  # 仅卖出
    transfer_fee_rate: float = 0.00001  # 双向
```

`DeployConfig` 加一个字段：

```python
    fees: FeeConfig = field(default_factory=FeeConfig)
```

`load_deploy_config` 的返回值加一行：

```python
        fees=FeeConfig(**raw.get("fees", {})),
```

- [ ] **Step 4: 补配置示例**

`config/deploy.example.toml` 末尾追加：

```toml
# 券商费率。xtquant 的成交记录不带手续费，实盘费用由这里算出来。
# 默认为 A 股常见档位；佣金按你与券商谈定的填。
[fees]
commission_rate = 0.0003
min_commission = 5.0
stamp_duty_rate = 0.0005      # 仅卖出
transfer_fee_rate = 0.00001   # 双向
```

- [ ] **Step 5: 接线 run_agent**

`scripts/run_agent.py`：导入与构造改为

```python
from qsys.agent.execution_agent import ExecutionAgent
from qsys.backtest.fees import AShareFeeModel
from qsys.live.config import load_deploy_config
from qsys.live.redis_transport import RedisTransport
```

```python
    cfg = load_deploy_config(args.config)
    transport = RedisTransport.from_config(cfg)
    adapter = XtQuantAdapter(args.account, args.qmt)
    fees = AShareFeeModel(
        commission_rate=cfg.fees.commission_rate,
        min_commission=cfg.fees.min_commission,
        stamp_duty_rate=cfg.fees.stamp_duty_rate,
        transfer_fee_rate=cfg.fees.transfer_fee_rate,
    )
    agent = ExecutionAgent(transport, adapter, cfg.streams,
                           consumer=cfg.consumer_name, fee_model=fees)
```

主循环加一行 `poll_reports()`：

```python
    while True:
        try:
            agent.run_once()
            agent.poll_reports()
            agent.beat()
            if ticks % ACCOUNT_PUBLISH_EVERY == 0:
                agent.publish_account()
        except Exception as exc:  # 单次循环失败不应终止代理
            log.error("agent.loop_error", error=str(exc))
        ticks += 1
        time.sleep(POLL_SECONDS)
```

- [ ] **Step 6: 跑全套**

```bash
uv run pytest -q -p no:warnings
```

Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/qsys/live/config.py config/deploy.example.toml scripts/run_agent.py \
        tests/live/test_config.py
git commit -m "feat(live): 费率进部署配置，代理循环里拉真实成交

xtquant 的成交记录不带手续费字段，实盘费用由配置的费率算出来。
佣金因人而异，不能写死。"
```

---

## Task 10: Windows 端到端冒烟（不下真单）

**Files:**
- Create: `scripts/win_smoke_reports.py`
- Modify: `docs/deploy/`（若已有部署说明文档则追加一节；没有则新建 `docs/deploy/live-agent.md`）

**Interfaces:**
- Consumes: `XtQuantAdapter`（Task 6）、`FillTracker`（Task 2）

在真实 Windows + miniQMT 上确认三件事，**全程只读，不下任何委托**：
`query_stock_trades` / `query_stock_orders` 能调通、字段翻译不抛异常、
`FillTracker` 能把当日真实成交算出合理费用。

用户当天有真实成交时最有价值；没有成交也能验证接口连通与空集处理。

- [ ] **Step 1: 写脚本**

创建 `scripts/win_smoke_reports.py`：

```python
"""Windows 端到端冒烟：真实账户的成交/委托查询与回报推导。

**只读。不下任何委托。** 用于在接实盘前确认三件事：
  1. query_stock_trades / query_stock_orders 能调通
  2. xtquant_mapping 的字段翻译对真实数据不抛异常
  3. FillTracker 能把当日真实成交算出合理的费用

用法（Windows PowerShell）：
  C:\\qsys\\.venv\\Scripts\\python.exe -u C:\\qsys\\win_smoke_reports.py `
      --account <资金账号> --qmt <miniQMT userdata 路径>

控制台是 GBK，输出一律用 ASCII 标点。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

from qsys.agent.fill_tracker import STATUS_NAMES, FillTracker
from qsys.agent.xtquant_adapter import XtQuantAdapter
from qsys.backtest.fees import AShareFeeModel


def main() -> int:
    parser = argparse.ArgumentParser(description="实盘回报链路冒烟（只读）")
    parser.add_argument("--account", required=True)
    parser.add_argument("--qmt", required=True)
    args = parser.parse_args()

    adapter = XtQuantAdapter(args.account, args.qmt)

    trades = adapter.query_trades()
    orders = adapter.query_orders()
    print(f"[1/3] 查询成功: 当日成交 {len(trades)} 笔 / 委托 {len(orders)} 笔")

    print("\n[2/3] 委托明细")
    print(f"{'client_order_id':<24}{'symbol':<12}{'side':<6}"
          f"{'ordered':>8}{'traded':>8}  status")
    for o in orders:
        name = STATUS_NAMES.get(o.status, str(o.status))
        print(f"{o.client_order_id:<24}{o.symbol:<12}{o.side:<6}"
              f"{o.ordered_qty:>8}{o.traded_qty:>8}  {name} {o.status_msg}")

    tracker = FillTracker(AShareFeeModel())
    out = tracker.observe(trades, orders, seen_trade_ids=frozenset(),
                          closed_order_ids=frozenset(), now=datetime.now())
    print(f"\n[3/3] 推导出 {len(out.fills)} 条成交 / {len(out.rejects)} 条未成交")
    print(f"{'client_order_id':<24}{'symbol':<12}{'qty':>7}"
          f"{'price':>10}{'fee':>9}  trade_id")
    for f in out.fills:
        print(f"{f.client_order_id:<24}{f.symbol:<12}{f.qty:>7}"
              f"{f.price:>10.3f}{f.fee:>9.2f}  {f.broker_trade_id}")
    for r in out.rejects:
        print(f"  [未成交] {r.client_order_id} {r.symbol} "
              f"剩余 {r.unfilled_qty} 股 - {r.reason}")

    if trades:
        gross = sum(f.price * f.qty for f in out.fills)
        fee = sum(f.fee for f in out.fills)
        print(f"\n成交金额合计 {gross:,.2f}  费用合计 {fee:,.2f}  "
              f"占比 {fee / gross * 100:.4f}%")
        print("请与券商当日交割单核对费用。对不上说明 [fees] 配置的费率不对。")
    else:
        print("\n当日无成交。接口连通性与空集处理已验证。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 在 Windows 上跑**

把脚本与 `src/qsys` 同步到 Windows 的 `C:\qsys`，然后：

```powershell
C:\qsys\.venv\Scripts\python.exe -u C:\qsys\win_smoke_reports.py `
    --account <资金账号> --qmt <miniQMT userdata 路径>
```

**注意：** `C:\qmt-bridge`、`C:\qmt-bridge-private`、`C:\qmt-bridge-simulation`
是用户自己的影子交叉验证，**不得修改**。本脚本只在 `C:\qsys` 下运行。

- [ ] **Step 3: 核对费用**

若当日有成交，把脚本算出的费用与券商交割单逐笔核对。
对不上就调 `config/deploy.local.toml` 的 `[fees]`，直到一致。
**这一步不能跳过**：费用算错会让第三层对账的偏差里混进一个固定的系统性误差，
把真正的滑点掩盖掉。

- [ ] **Step 4: 记录结果**

新建或追加 `docs/deploy/live-agent.md`，记下：
账户当日成交笔数、脚本算出的费用、交割单上的费用、两者差额、
最终采用的 `[fees]` 费率。

- [ ] **Step 5: 提交**

```bash
git add scripts/win_smoke_reports.py docs/deploy/live-agent.md
git commit -m "feat(agent): Windows 只读冒烟脚本，接实盘前核对回报与费用

费用算错会让第三层对账混进固定的系统性误差，把真正的滑点掩盖掉，
所以必须先与交割单核对到一致。"
```

---

## 完成后

全套测试应为 **1150 + 约 35 条新测试**全绿：

```bash
uv run pytest -q -p no:warnings
```

然后用 `superpowers:finishing-a-development-branch` 收尾。

**下一段是 1-6b（实盘运行器与实时行情通道）** —— 本计划做完，回报是真的了，
但核心侧仍然没有任何东西在跑。1-6b 要解决：

- `scripts/run_live.py`：真实时钟 + 交易日历 + 策略装载 + ShadowBroker/LiveBroker 接线
- Windows → Linux 的实时报价通道（09:30 竞价价、11:25/14:25/14:50 检查点价）
- 第一层对账接进循环：持仓快照 vs 本地推演，不一致立即停止下单

