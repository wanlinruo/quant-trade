# 二期 2-2c：完整回测（下单、分配、日内退出） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 2-2b 的选股接上 S8 权重分配、当日开盘买入、以及 11:25 / 14:25 / 14:50 三个日内退出检查点，跑出净值曲线。

**Architecture:** 本策略是**隔夜策略**而非日内策略——原文
`get_sellable_lots` 写明「A股T+1：只允许卖出入场日早于当前交易日的 lot」。
T 日开盘买入，T+1 才可卖，三个检查点卖的是**前一日买入的仓位**。

因此现有引擎的 T+1 冻结/解冻与 `SettlementConstraint` **本来就是对的**，
不需要改。唯一缺的是**日内检查点**这个节奏：退出发生在 11:25 / 14:25 /
14:50，而引擎目前只有 09:15 / 15:00 / 15:30 三个时刻。做法是扩展现有引擎，
而不是另起一个日内引擎——两个引擎会各自演化、各自出错，那正是 spec §1
列的第四个痛点（多系统拼接脆弱）。

**Tech Stack:** Python 3.11+ · uv · polars · pytest

**Spec:** `docs/superpowers/specs/2026-09-06-personal-quant-system-design.md`（§6 回测层、§8 测试）

**移植基准:** `docs/reference/joinquant-structural-v1.2.py`（**不要修改**）

## 为什么必须扩引擎，而不是绕开

| 做法 | 问题 |
|---|---|
| 塞进日频引擎（收盘成交近似） | 11:25 止盈与 14:25 无条件卖会退化成同一个动作，退出逻辑失真 |
| 另起一个日内引擎 | 两个引擎各自演化、各自出错，正是要避免的多系统拼接 |
| **扩展现有引擎** | 日频策略行为完全不变（不配检查点就是原样），本策略得到它需要的检查点 |

引擎当前的每日顺序（`engine.py` 文件头）是正确性的核心，扩展**不得改动它**，
只在第 4 步「撮合 @ 今日开盘」之后插入检查点。

## 覆盖区间的硬约束

三个退出检查点都需要**日内价格与当时的涨停状态**，只有分钟线能给。

| 版本 | 区间 | 说明 |
|---|---|---|
| **精确版** | **2025-09-10 ~ 2026-09-09（242 天）** | 分钟线齐全，退出逻辑完整 |
| 日线近似版 | 2023-06-06 ~ 2026-09-09 | **跳过 11:25 检查点**，14:25/14:50 用收盘价与收盘涨停状态代替 |

近似版的偏离是明确的、单向的，对账时可归因；它存在的意义是让长区间有个
参照，不是用来代替精确版。

## Global Constraints

- Python **3.11+**
- **不改 `engine.py` 既有的每日顺序**——只在开盘撮合之后插入检查点
- **不配检查点时引擎行为与现在逐字节相同**——有测试守着（既有回测的
  `run_id` 必须不变）
- 阈值一律照抄 `params.py`，不做任何调参
- 单元测试不得触网
- 环境隔离：所有命令前置
  `unset VIRTUAL_ENV; export PATH="$HOME/.local/bin:$(echo "$PATH" | tr ':' '\n' | grep -v 'project-002/.venv' | paste -sd: -)"`

---

## File Structure

```
src/qsys/core/clock.py            ← Phase 追加 INTRADAY
src/qsys/backtest/engine.py       ← 日内检查点（不改既有顺序）
src/qsys/data/market_feed.py      ← 追加 intraday_prices / intraday_limit_up
src/qsys/strategy/base.py         ← 追加 on_checkpoint 钩子
src/qsys/strategy/structural/
  exits.py                        三个退出决策（纯函数）
  allocation.py                   S8 权重分配
  strategy.py                     组装成 Strategy
scripts/run_structural_backtest.py
tests/backtest/test_intraday_checkpoints.py
tests/strategy/structural/
  test_structural_exits.py test_structural_allocation.py
  test_structural_backtest.py
```

---

## Task 1: 引擎支持日内检查点

**Files:**
- Modify: `src/qsys/core/clock.py`、`src/qsys/backtest/engine.py`、`src/qsys/strategy/base.py`
- Test: `tests/backtest/test_intraday_checkpoints.py`

**Interfaces:**
- Produces: `Strategy.on_checkpoint(ctx, label)`；
  `BacktestEngine(..., checkpoints: Sequence[tuple[str, time]] = ())`；
  检查点提交的订单**当场按该检查点价格成交**，不等次日

- [ ] **Step 1: 写失败测试**

Create `tests/backtest/test_intraday_checkpoints.py`，至少覆盖：

```python
def test_without_checkpoints_the_engine_is_unchanged():
    """不配检查点时，run_id 与净值必须与既有回测逐字节相同。

    这是扩展的安全网：日频策略的行为一点都不能变。
    """


def test_checkpoint_hook_fires_in_order():
    """11:25 -> 14:25 -> 14:50，且都在当日开盘撮合之后。"""


def test_an_order_at_a_checkpoint_fills_at_that_checkpoint_price():
    """检查点卖出当场成交，不排到次日开盘。"""


def test_shares_bought_today_cannot_be_sold_at_a_checkpoint():
    """A 股 T+1：当日买入当日不可卖。既有的 SettlementConstraint 已经
    在做这件事，检查点不得绕过它。"""


def test_yesterdays_position_is_sellable_at_a_checkpoint():
    """检查点真正要卖的就是这个——前一日买入、持有过夜的仓位。"""


def test_a_checkpoint_without_intraday_prices_is_skipped():
    """没有分钟线的日子跳过检查点，不能拿收盘价冒充。"""


def test_ctx_now_at_a_checkpoint_is_that_checkpoint_time():
    """as_of 必须推进到检查点时刻，否则策略读不到当时的分钟线。"""
```

### T+1 语义（已用原文确认）

原文 `get_sellable_lots`（L606）写得很直白：

```python
"""A股T+1：只允许卖出入场日早于当前交易日的lot。"""
if lot.get('entry_date') is not None and lot['entry_date'] < today
```

配合 `_submit_checkpoint_exits` 里的 `position.closeable_amount` 判定
（L780-786），结论是：**本策略是隔夜策略**。T 日开盘买入，T+1 才可卖，
三个检查点卖的是前一日买入的仓位。涨停则继续持有，直到某个检查点判定卖出。

引擎既有的 `unfreeze()` 与 `SettlementConstraint` 已经正确实现了这条规则，
**不需要改动**。

- [ ] **Step 2-5: 失败 -> 实现 -> 通过 -> 提交**

---

## Task 2: 日内价格与涨停状态

**Files:**
- Modify: `src/qsys/data/market_feed.py`
- Test: `tests/strategy/structural/test_structural_market_feed.py`（追加）

**Interfaces:**
- Produces: `BoundMarket.intraday(symbols, day, at: time) -> dict[str, float]`
  （该时刻的最新价，取自不晚于 `at` 的最后一根分钟线）；
  `BoundMarket.limit_up_price(symbols, day) -> dict[str, float]`（取自 `daily_flags`）

**要点**：
- 「是否涨停」= 该时刻最新价 >= 当日涨停价。涨停价来自 `daily_flags.limit_up`，
  而它由**昨收**算出，昨日收盘即已确定，不构成未来函数
- 取不到分钟线时返回空，**不得回退到收盘价**——那会让 11:25 的判定用上
  收盘信息，是彻头彻尾的未来函数

- [ ] **Step 1-5: 测试 -> 实现 -> 通过 -> 提交**

---

## Task 3: 退出决策（纯函数）

**Files:**
- Create: `src/qsys/strategy/structural/exits.py`
- Test: `tests/strategy/structural/test_structural_exits.py`

**Interfaces:**
- Produces: `legacy_exit(last_price, limit_up, entry_price, ma5) -> ExitDecision`；
  `low_1125(last_price, limit_up, entry_price) -> ExitDecision`；
  `low_1425(last_price, limit_up, entry_price) -> ExitDecision`；
  `dynamic_ma5(recent_closes, last_price) -> float | None`

逐条对照原文：

| 函数 | 原文 | 规则 |
|---|---|---|
| `dynamic_ma5` | L615 | `(最近 4 日收盘均值 × 4 + 现价) / 5` |
| `legacy_exit` | L624 | 非涨停且现价 > 入场价 → `TAKE_PROFIT`；否则现价 < MA5 → `MA5_STOP`；否则持有 |
| `low_1125` | L707 | 涨停 → 持有；非涨停且盈利 → 卖；非涨停且不盈利 → 等 14:25 |
| `low_1425` | L742 | 涨停 → 持有过夜；非涨停 → 无条件卖 |

**注意 `legacy_exit` 的顺序**：先判止盈再判 MA5。若现价既高于入场价又低于
MA5，原版走 `TAKE_PROFIT` 而非 `MA5_STOP`——照抄，不要"优化"。

- [ ] **Step 1-5: 测试 -> 实现 -> 通过 -> 提交**

---

## Task 4: S8 权重分配与买入

**Files:**
- Create: `src/qsys/strategy/structural/allocation.py`
- Test: `tests/strategy/structural/test_structural_allocation.py`

**Interfaces:**
- Produces: `allocate(candidates: Sequence[tuple[str, str]], cash: float) -> list[Budget]`

对照原文 L976-L1010：

- 权重取自 `BRANCH_WEIGHTS`，未知标签取 1.0
- **在当日通过 Gate 的候选内部归一化**——只有一只候选时它可拿到全部预算
- `budget = 可用现金 × weight / total_weight`
- `budget / open_price >= 100` 才下单（不足一手就跳过，记 `BELOW_BOARD_LOT`）
- 可用现金 <= 0 时全部跳过，记 `NO_AVAILABLE_CASH`

- [ ] **Step 1-5: 测试 -> 实现 -> 通过 -> 提交**

---

## Task 5: 策略类组装

**Files:**
- Create: `src/qsys/strategy/structural/strategy.py`
- Test: `tests/strategy/structural/test_structural_backtest.py`

**Interfaces:**
- Produces: `StructuralStrategy(Strategy)`，实现 `on_session_start`（选股+下单）
  与 `on_checkpoint`（退出）

**买入时刻**：原策略 `run_daily(buy, '09:26')`，用当日开盘价下市价单。
在我们的引擎里对应 `on_session_start` 之后、开盘撮合之前提交，
成交价即当日开盘价。

**持仓与入场价**：退出决策要按**每笔买入的入场价**判盈亏（原策略的 lot 账本）。
一期的 `Portfolio` 是按标的聚合的，没有 lot。**先用持仓均价代替**，
并在文档中标注这是与原版的一处差异——同一标的跨分支重复买入时会有偏差。
lot 级账本归入后续。

- [ ] **Step 1-5: 测试 -> 实现 -> 通过 -> 提交**

---

## Task 6: 跑回测并产出净值

**Files:**
- Create: `scripts/run_structural_backtest.py`

- [ ] **Step 1: 精确版回测**

区间 2025-09-10 ~ 2026-09-09（242 天），产出净值、夏普、最大回撤、成交明细。

- [ ] **Step 2: 近似版回测**

区间 2023-06-06 ~ 2026-09-09，跳过 11:25 检查点。**结果单独标注**，
不与精确版混谈。

- [ ] **Step 3: 结果接入数据台**

净值曲线与成交明细加到 Web 上，可视化验证。

- [ ] **Step 4: 提交**

---

## 完成标准

1. `uv run pytest` 全绿，既有 937 项无回归
2. **不配检查点时引擎行为逐字节不变**——既有回测 `run_id` 不变
3. **T+1 语义正确**——当日买入当日不可卖，有测试守着
4. **没有分钟线的日子跳过检查点**，绝不用收盘价冒充
5. 精确版回测跑通，产出净值曲线
6. 阈值与原文逐条对应，无内联魔数

## 执行实测（2026-09-09）

Task 1-6 完成，1,019 项测试全绿。首次跑通完整回测。

### 途中查出的两个问题

**1. 买入不能放在 09:15。** 引擎的 `SESSION_START` 是 09:15「当日 bar 尚不
可见」，而本策略要用**已结算的竞价价**定量下单（原策略 `run_daily(buy,
'09:26')`）。放在 09:15 的后果是 `allocate` 拿不到开盘价，每一单都以
`INVALID_OPEN_PRICE` 跳过——**首次跑真实回测 0 笔成交就是这个原因**。

修法是新增 `Phase.AUCTION` 与 `Strategy.on_auction`：竞价已结算、当日
open 已确定，其余仍不可见；在此下的单按当日开盘价撮合。不配 `auction_at`
时引擎行为与原来完全一致。

**2. 分钟线只覆盖买入日，不覆盖退出日。** 这是 2-1b 设计清单时的疏漏：
`candidate_stock_days(window=1)` 只给首板次日（买入日），而**退出发生在
再次日**。实测退出日的分钟线覆盖率 **0.0%**（15,198 / 15,200 缺失）——
仓位在检查点拿不到报价，永远退不出去。

首次回测的表现正是这个症状：19 笔买入只有 11 笔卖出，8 个仓位卡死，
现金被占光后大量候选以 `BELOW_BOARD_LOT` 跳过。

修法是把清单改成 `window=3`（44,766 组，需补 29,566 组），覆盖买入日与
其后两个交易日——JQ 的 `branch_summary` 显示平均持仓 1.04~1.77 个交易日。

### 首次回测（数据补齐前，结果不可用）

区间 2025-09-10 ~ 2026-09-08，`run_id b37e0c7e0928d9c5`，2.4 分钟。
30 笔成交、+58.63%、夏普 1.313——**但 8 个仓位从未退出，这不是策略的
真实表现**，补齐退出日分钟线后需重跑。

### 补齐数据后的回测与逐笔对账（2026-09-09）

分钟线补到 `window=3`（1,080 万行 / 3,592 只），退出日覆盖率 **0.0% -> 99.3%**。
重跑后成交 155 笔（买 78 / 卖 77），退出逻辑正常工作。

**逐笔对账：交易本身几乎完美复现**

| 项 | 结果 |
|---|---|
| 入场价差 >0.2% | **0 / 69** |
| 出场日 | 逐笔一致 |
| 退出原因 | 逐笔一致（TAKE_PROFIT / MA5_STOP / LW_1125 / LW_1425） |

**但组合收益差距巨大**：聚宽 +109.93%，qsys -9.06%（同窗口）。

原因不在交易执行，在**多选**：

| | qsys | 聚宽 |
|---|---|---|
| 笔数 | 77 | 86 |
| 单笔均值 | **+1.82%** | **+3.65%** |
| 亏损 >5% | 13 笔 | 8 笔 |

qsys 独有的 10 笔均值 **-5.58%**（最差 -14.25%），把均值从 +3.65% 拖到 +1.82%。

**为什么 4% 的选股差异能翻转结果**：策略满仓单押（48 个买入日里 27 天只持
一只），38% 波动率下，单笔均值差 1.8 个点经复利就是 +110% 变 -9%。
**波动损耗把选股差异放大了几十倍**——这个策略对选股精度极度敏感，
一笔多选的代价远大于一笔漏选。

残留多选的来源（对账已定位）：近期窗口 14 条，其中 5 条是 Gate 判定分歧
（我们放行了聚宽拒的），其余是竞价价口径。**后者用现有数据消不掉**。

### 补全市场除权除息（2026-09-09）

回测入口此前**从没给引擎传过 `corporate_actions`**——引擎一直有
`apply_corporate_actions` 的能力，缺的是数据（一期 bootstrap 只有 3 只）。

数据源换成 QMT 的 `get_divid_factors`：全市场 **5,558 只有数据、0 失败、
18 秒**。它**含配股**（`allotNum`/`allotPrice`），akshare 的
`stock_fhps_detail_em` 没有——spec §12 挂着的那条风险由此关闭。

| 表 | 之前 | 现在 |
|---|---|---|
| `corporate_actions` | 63 行 / 3 只 | **60,424 行 / 5,742 只** |
| `adj_factors` | 351 行 / 3 只 | **4,504,869 行 / 5,217 只** |
| 含配股 | 0 | **1,127 条** |

回测区间内命中 **4,913 条除权除息事件 / 220 天**。接入后收益从 -9.06%
变成 **-8.73%**（+0.33 个百分点）——影响确实存在但很小，与 1~2 天持仓的
预期一致。**现在这个数字是干净的**：不再有「除权被当成亏损」的系统性低估。

配股暂不处理：`CorporateAction` 只有现金与送转两项，配股还涉及是否认购的
决策。占比 1.9%，记为缺口。

## 已知缺口（转入后续）

- **lot 级账本** —— 退出按持仓均价而非每笔入场价，同一标的跨分支重复买入
  时有偏差
- **精确版只覆盖 242 天** —— 受分钟线限制
- **竞价价口径分歧** —— 2-2b 遗留，无法用现有数据消除
- **`adj_factors` 只覆盖 3 只** —— 全市场复权因子未采集，净值未做复权校正

## 下一步

与聚宽对账**净值 + 成交明细**，然后是 **Web 回测平台**。
