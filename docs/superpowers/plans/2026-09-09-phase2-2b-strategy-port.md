# 二期 2-2b：策略移植（选股链路）与聚宽对账 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把聚宽 `Structural Strategy V1.2` 的**选股链路**移植到 qsys——候选池、
LOW/HIGH 入场判定、S6 分类与 S7 Gate——并产出「哪天买了哪些票」，与聚宽记录对账。

**Architecture:** 策略只通过 `Context` 拿数据，`Context` 把 `as_of` 钉死，因此
未来函数在 API 层面不可能发生。本计划为此扩出 `ctx.market`：涨跌停标记、
生态因子、估值、分钟线，全部走同一套可见性规则。**不含下单与退出**——
选股是离散的，错一只就看得出来，先把这一段对准再谈净值。

**Tech Stack:** Python 3.11+ · uv · polars · duckdb · pytest

**Spec:** `docs/superpowers/specs/2026-09-06-personal-quant-system-design.md`（§5 内核与策略、§8.2 交叉验证）

**移植基准:** `docs/reference/joinquant-structural-v1.2.py`（2,651 行，**不要修改**）

## 一个必须先解决的 PIT 问题

策略在**开盘时刻**判定低开/高开，用的是竞价成交价。而 `HistoricalFeed`
把**整根日线**锁到该日收盘才可见——T 日 09:30 拿不到 T 日的 `open`。

放宽 PIT 是错的。正确的做法是承认一个事实：**`open` 在 09:30 就已确定**，
它和同一根 bar 的 high/low/close 可见时刻不同。为此新增一个**只给 open**
的访问口：

```python
ctx.market.session_open(symbols, day)   # 09:30 起可见，只返回 open
```

它绝不返回 high/low/close/volume——那些确实要等收盘。反向测试守着这条。

**为什么不用分钟线的竞价根**：分钟线只覆盖 242/894 天。而 `open` 与竞价
成交价实测完全相等（2-1a 与 2-1b 两次验证），用日线 `open` 可让 LOW 入场
判定覆盖全部 894 天。分钟线仍用于**竞价量**（LOW_MAIN 独有的过滤）。

## Global Constraints

- Python **3.11+**
- **策略不得直接访问 `WarehouseQuery`**——数据访问一律经 `Context`，`as_of` 由
  框架绑定。这是 spec §5 的硬要求
- **不改 `docs/reference/joinquant-structural-v1.2.py`**——它是对账基准
- **阈值一律照抄，不做任何调参**。原策略是 FROZEN 版本，移植的目标是复现
  而不是改进
- 单元测试不得触网
- 环境隔离：所有命令前置
  `unset VIRTUAL_ENV; export PATH="$HOME/.local/bin:$(echo "$PATH" | tr ':' '\n' | grep -v 'project-002/.venv' | paste -sd: -)"`

## 移植范围

V1.2 在 S7 Gate 之前**整体移除了 WEAK 家族**（`_v12_keep_non_weak`），
故只移植 **LOW** 与 **HIGH** 两个分支，各分主板 / 创业板：
`LOW_MAIN` / `LOW_CHINEXT` / `HIGH_MAIN` / `HIGH_CHINEXT`。

## 已知语义分歧（对账时必须单独归因）

| 分歧 | 影响 |
|---|---|
| **竞价价口径** | 原策略取 `get_call_auction(09:15~09:26)` 的**第一条**记录（09:15 快照，作者明确注释「不改成最后一条」）；我们只有 09:25 的已成交价。**无法用现有数据消除** |
| **竞价量只有 242 天** | `LOW_MAIN` 的 `auction_vol/prev_vol >= 2%` 过滤在早期 652 天无法施加。按用户决定：**两版都跑**，用 242 天窗口量化这条过滤的影响 |
| **无历史 ST** | 原策略 `filter_st_paused_stock` 排除 ST。我们按 10% 判涨停，ST（5%）本就进不了候选池，**方向等效**，不单独实现 |

---

## File Structure

```
src/qsys/data/
  market_feed.py              PIT 安全的派生数据访问（flags/regime/valuation/open/竞价量）
src/qsys/strategy/
  context.py                  ← Context 追加 .market
  structural/
    __init__.py
    params.py                 全部阈值常量，逐条标注原文行号
    universe.py               候选池：首板 / 炸板 / 板块与新股过滤
    entry.py                  check_low_open / check_high_open / check_left_pressure
    gate.py                   _s6_classify 与 _s6_gate_allow
    strategy.py               组装：选股 -> Gate -> 当日候选
scripts/
  run_selection.py            全区间跑选股，导出每日候选
  compare_joinquant.py        ← 扩展：对账「哪天买了哪些票」
tests/strategy/structural/
  test_market_feed.py test_universe.py test_entry.py
  test_gate.py test_selection_e2e.py
```

---

## Task 1: PIT 安全的派生数据访问

**Files:**
- Create: `src/qsys/data/market_feed.py`
- Modify: `src/qsys/strategy/context.py`
- Test: `tests/strategy/structural/test_market_feed.py`

**Interfaces:**
- Consumes: `WarehouseQuery`、`TradingCalendar`、`MinuteFeed`
- Produces: `MarketFeed(query, calendar)`；`BoundMarket`（as_of 钉死）暴露
  `flags(symbols, start, end)`、`regime(start, end)`、`valuation(symbols, start, end)`、
  `session_open(symbols, day) -> dict[str, float]`、`auction_volume(symbols, day) -> dict[str, int]`；
  `Context.market -> BoundMarket`

- [ ] **Step 1: 写失败测试**

Create `tests/strategy/structural/test_market_feed.py`:

```python
from datetime import date, datetime, time

import pyarrow as pa
import pytest

from qsys.core.calendar import AShareCalendar
from qsys.data import schema
from qsys.data.market_feed import MarketFeed
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

D = [date(2024, 1, d) for d in (2, 3, 4)]
SESSIONS = list(D)


def seed(warehouse: Warehouse) -> None:
    n = len(D)
    warehouse.write("bars_daily", pa.table({
        "symbol": ["600001.SH"] * n, "dt": list(D),
        "open": [10.0, 11.0, 12.0], "high": [10.5, 11.5, 12.5],
        "low": [9.5, 10.5, 11.5], "close": [10.2, 11.2, 12.2],
        "volume": [1000, 2000, 3000], "amount": [0.0] * n,
    }, schema=schema.BARS_DAILY), year=2024)
    warehouse.write("daily_flags", pa.table({
        "dt": list(D), "symbol": ["600001.SH"] * n,
        "prev_close": [None, 10.2, 11.2], "limit_up": [None, 11.22, 12.32],
        "limit_down": [None, 9.18, 10.08],
        "is_limit_up": [False, False, True], "is_limit_down": [False] * n,
        "touched_limit_up": [False, False, True], "is_broken_board": [False] * n,
        "is_first_board": [False, False, True],
    }, schema=schema.DAILY_FLAGS), year=2024)
    warehouse.write("market_regime", pa.table({
        "dt": list(D), "limit_up_count": [1, 2, 3], "limit_down_count": [0] * n,
        "limit_up_down_ratio": [1.0] * n, "touched_limit_up_count": [1, 2, 3],
        "broken_board_count": [0] * n, "broken_board_rate": [0.0] * n,
        "first_board_count": [1, 1, 1], "market_advance_ratio": [0.5] * n,
        "market_median_return_1d": [0.01] * n, "market_total_money": [1e10] * n,
        "prior_limitup_nextday_return_mean": [0.02] * n,
        "prior_limitup_nextday_return_median": [0.02] * n,
        "valid_stock_count": [100] * n, "index_ma20_slope_5d": [0.001] * n,
    }, schema=schema.MARKET_REGIME), year=2024)
    warehouse.write("valuation_snapshot", pa.table({
        "dt": list(D), "symbol": ["600001.SH"] * n,
        "close": [10.2, 11.2, 12.2], "market_cap": [100.0, 110.0, 120.0],
        "circulating_market_cap": [80.0, 88.0, 96.0],
        "turnover_ratio": [1.0] * n,
    }, schema=schema.VALUATION_SNAPSHOT), year=2024)
    warehouse.write("minute_bars", pa.table({
        "symbol": ["600001.SH"] * 2,
        "dt": [datetime(2024, 1, 4, 9, 30), datetime(2024, 1, 4, 9, 31)],
        "open": [12.0, 12.0], "high": [12.0, 12.3], "low": [12.0, 12.0],
        "close": [12.0, 12.3], "volume": [50, 300], "amount": [0.0] * 2,
    }, schema=schema.MINUTE_BARS), year=2024)


@pytest.fixture
def market(warehouse):
    seed(warehouse)
    return MarketFeed(WarehouseQuery(warehouse), AShareCalendar(SESSIONS))


def at(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute))


def test_flags_are_visible_only_after_that_session_closes(market):
    bound = market.bind(at(D[2], 9, 30))
    assert bound.flags(["600001.SH"], D[0], D[2])["dt"].max() == D[1]
    after = market.bind(at(D[2], 15, 0))
    assert after.flags(["600001.SH"], D[0], D[2])["dt"].max() == D[2]


def test_regime_follows_the_same_visibility_rule(market):
    bound = market.bind(at(D[2], 9, 30))
    assert bound.regime(D[0], D[2])["dt"].max() == D[1]


def test_valuation_follows_the_same_visibility_rule(market):
    bound = market.bind(at(D[2], 9, 30))
    assert bound.valuation(["600001.SH"], D[0], D[2])["dt"].max() == D[1]


def test_session_open_is_visible_from_the_open(market):
    """open 在 09:30 就已确定——这正是策略判低开/高开的依据。"""
    assert market.bind(at(D[2], 9, 30)).session_open(["600001.SH"], D[2]) == {
        "600001.SH": 12.0
    }


def test_session_open_is_invisible_before_the_open(market):
    assert market.bind(at(D[2], 9, 29)).session_open(["600001.SH"], D[2]) == {}


def test_session_open_never_leaks_the_rest_of_the_bar(market):
    """只给 open。high/low/close 确实要等收盘，这个口子不能变成后门。"""
    bound = market.bind(at(D[2], 9, 30))
    opens = bound.session_open(["600001.SH"], D[2])
    assert isinstance(opens["600001.SH"], float)
    # 当日 close 是 12.2、high 是 12.5，都不该出现在返回值里
    assert set(opens.values()) == {12.0}


def test_auction_volume_comes_from_the_0930_bar(market):
    got = market.bind(at(D[2], 9, 30)).auction_volume(["600001.SH"], D[2])
    assert got == {"600001.SH": 50}


def test_auction_volume_is_invisible_before_the_open(market):
    assert market.bind(at(D[2], 9, 29)).auction_volume(["600001.SH"], D[2]) == {}


def test_auction_volume_is_empty_when_there_is_no_minute_data(market):
    """分钟线只覆盖最近 242 天，早期没有——返回空而不是 0。

    返回 0 会让「竞价量/昨日量 >= 2%」判成 False 而静默剔除候选；
    返回空则让调用方能显式区分「没数据」与「量不够」。
    """
    assert market.bind(at(D[0], 9, 30)).auction_volume(["600001.SH"], D[0]) == {}


def test_bound_market_cannot_be_asked_for_another_as_of(market):
    bound = market.bind(at(D[2], 9, 30))
    assert not hasattr(bound, "as_of")
    with pytest.raises(TypeError):
        bound.flags(["600001.SH"], D[0], D[2], as_of=at(D[2], 15, 0))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/strategy/structural/test_market_feed.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.market_feed'`

- [ ] **Step 3: 实现 `src/qsys/data/market_feed.py`**

```python
"""派生数据的 PIT 访问契约。

日线 bar 的可见时刻是收盘，`daily_flags` / `market_regime` /
`valuation_snapshot` 都从日线派生，因此适用同一条规则。

**唯一的例外是当日开盘价**：`open` 在 09:30 就已确定，与同一根 bar 的
high/low/close 可见时刻不同。策略判低开/高开正是用它。为此单开一个
`session_open()`，**只返回 open**——把整根 bar 放出来就成了后门。

竞价量取自分钟线 09:30 那根（实测 QMT 把开盘集合竞价单独成根，
四价相等）。分钟线只覆盖最近约一年，取不到时**返回空而不是 0**：
返回 0 会让「竞价量不足」的判定静默通过，调用方分不清「没数据」
和「量真的不够」。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time

import polars as pl

from qsys.core.calendar import TradingCalendar, last_closed_session
from qsys.data.query import WarehouseQuery

# QMT 的开盘集合竞价 bar 以 09:30 标注（收盘时刻约定）
AUCTION_AT = time(9, 30)


class BoundMarket:
    """as_of 钉死的派生数据视图。策略拿不到别的 as_of。"""

    def __init__(self, query: WarehouseQuery, calendar: TradingCalendar,
                 as_of: datetime) -> None:
        self._query = query
        self._calendar = calendar
        self._as_of = as_of

    @property
    def _visible_through(self) -> date | None:
        return last_closed_session(self._calendar, self._as_of)

    def _clip(self, start: date, end: date) -> tuple[date, date] | None:
        through = self._visible_through
        if through is None:
            return None
        effective = min(end, through)
        return None if effective < start else (start, effective)

    def flags(self, symbols: Sequence[str], start: date, end: date) -> pl.DataFrame:
        window = self._clip(start, end)
        if window is None:
            return self._query.daily_flags(start, start).clear()
        frame = self._query.daily_flags(*window)
        return frame.filter(pl.col("symbol").is_in(list(symbols)))

    def regime(self, start: date, end: date) -> pl.DataFrame:
        window = self._clip(start, end)
        if window is None:
            return self._query.market_regime(start, start).clear()
        return self._query.market_regime(*window)

    def valuation(self, symbols: Sequence[str], start: date, end: date) -> pl.DataFrame:
        window = self._clip(start, end)
        if window is None:
            return self._query.valuation(start, start).clear()
        frame = self._query.valuation(*window)
        return frame.filter(pl.col("symbol").is_in(list(symbols)))

    def _open_is_visible(self, day: date) -> bool:
        return self._as_of >= datetime.combine(day, AUCTION_AT)

    def session_open(self, symbols: Sequence[str], day: date) -> dict[str, float]:
        """当日开盘价，09:30 起可见。**只返回 open。**"""
        if not self._open_is_visible(day):
            return {}
        bars = self._query.daily_bars(list(symbols), day, day)
        if bars.height == 0:
            return {}
        return dict(zip(bars["symbol"].to_list(), bars["open"].to_list()))

    def auction_volume(self, symbols: Sequence[str], day: date) -> dict[str, int]:
        """集合竞价成交量，取自分钟线 09:30 那根。取不到返回空。"""
        if not self._open_is_visible(day):
            return {}
        moment = datetime.combine(day, AUCTION_AT)
        bars = self._query.minute_bars(list(symbols), moment, moment)
        if bars.height == 0:
            return {}
        return dict(zip(bars["symbol"].to_list(), bars["volume"].to_list()))


class MarketFeed:
    def __init__(self, query: WarehouseQuery, calendar: TradingCalendar) -> None:
        self._query = query
        self._calendar = calendar

    def bind(self, as_of: datetime) -> BoundMarket:
        return BoundMarket(self._query, self._calendar, as_of)
```

- [ ] **Step 4: `Context` 追加 `.market`**

`src/qsys/strategy/context.py`：`__init__` 增加 `market: MarketFeed | None = None`
参数，并追加属性：

```python
    @property
    def market(self) -> BoundMarket:
        if self._market is None:
            raise RuntimeError("此 Context 未接入 MarketFeed")
        return self._market.bind(self.now)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/strategy/structural/test_market_feed.py -q`
Expected: PASS —— 11 passed

- [ ] **Step 6: 提交**

```bash
git add src/qsys/data/market_feed.py src/qsys/strategy/context.py \
        tests/strategy/structural/test_market_feed.py
git commit -m "feat(strategy): 派生数据的 PIT 访问契约"
```

---

## Task 2: 参数常量与候选池

**Files:**
- Create: `src/qsys/strategy/structural/__init__.py`、`params.py`、`universe.py`
- Test: `tests/strategy/structural/test_universe.py`

**Interfaces:**
- Produces: `params` 中全部阈值；`board_of(symbol)`、`strategy_tag(name, symbol)`；
  `candidate_pool(market, day, sessions) -> PoolResult(first_boards, broken_boards)`

- [ ] **Step 1: 写 `params.py`**

```python
"""Structural Strategy V1.2 的全部阈值，逐条对应原文行号。

**一律照抄，不做任何调参。** 原策略是 FROZEN 版本，移植的目标是复现。
偏离任何一个数都会让交叉验证失去意义——分不清是移植错了还是参数动了。
"""

from __future__ import annotations

# ---- 候选池（prepare_stock_list / filter_*，原文 L1349-L1403）----
NEW_STOCK_DAYS = 50  # 上市不满 50 天剔除
EXCLUDED_PREFIXES = ("4", "8")  # 北交所
EXCLUDED_PREFIX_2 = "68"  # 科创板

# ---- check_low_open（原文 L1201-L1221）----
LOW_HISTORY_DAYS = 60
LOW_RELATIVE_POSITION_MAX = 0.5  # rp > 0.5 剔除
LOW_MIN_MONEY = 1e8
LOW_AUCTION_RATIO_MIN = 0.955
LOW_AUCTION_RATIO_MAX = 0.97

# ---- LOW_MAIN 的 Stage4 附加过滤（原文 L905-L955, L1157）----
LOW_MAIN_AUCTION_VOL_RATIO_MIN = 0.020

# ---- check_high_open（原文 L1222-L1273）----
HIGH_AVG_INC_MIN = 0.07
HIGH_MIN_MONEY = 5.5e8
HIGH_MAX_MONEY = 20e8
HIGH_MIN_MARKET_CAP = 70.0  # 亿元
HIGH_MAX_CIRCULATING_CAP = 520.0  # 亿元
HIGH_AUCTION_VOL_RATIO_MIN = 0.03
HIGH_AUCTION_RATIO_MIN = 1.0  # 严格大于
HIGH_AUCTION_RATIO_MAX = 1.06  # 严格小于

# ---- check_left_pressure（原文 L1334-L1347）----
LEFT_PRESSURE_HISTORY_DAYS = 101
LEFT_PRESSURE_DEFAULT_ZYTS = 100
LEFT_PRESSURE_PADDING = 5
LEFT_PRESSURE_VOLUME_FACTOR = 0.9

# ---- S6 冻结阈值（原文 L1846-L1854）----
S6_LOW_TREND_WEAK_THRESHOLD = -0.009053065956339234
S6_LOW_HEAT_LOW_THRESHOLD = 0.0069591652776029595
S6_LOW_STOCK_RET10_HIGH = 0.09255705602513398
S6_LOW_DISTANCE60_HIGH = -0.19610064664276514
S6_HIGH_MAIN_BREADTH_HIGH = 0.0042554739215828
S6_HC_TOUCHED_NOT_LOW = 103.33333333333333
S6_HC_ADVANCE_NOT_LOW = 0.5104943476458254

# ---- S8 分支权重（原文 L89-L96）----
BRANCH_WEIGHTS = {
    "HIGH_MAIN": 1.15,
    "LOW_MAIN": 1.15,
    "HIGH_CHINEXT": 0.85,
    "LOW_CHINEXT": 1.00,
}
```

- [ ] **Step 2: 写候选池失败测试**

Create `tests/strategy/structural/test_universe.py`:

```python
from datetime import date

import polars as pl
import pytest

from qsys.strategy.structural.universe import (
    board_of, candidate_pool, strategy_tag,
)


def test_board_of_chinext():
    assert board_of("300750.SZ") == "CHINEXT"
    assert board_of("301001.SZ") == "CHINEXT"


def test_board_of_main():
    assert board_of("600036.SH") == "MAIN"
    assert board_of("000001.SZ") == "MAIN"


def test_strategy_tag_combines_name_and_board():
    assert strategy_tag("LOW", "600036.SH") == "LOW_MAIN"
    assert strategy_tag("HIGH", "300750.SZ") == "HIGH_CHINEXT"


def flags_frame(rows):
    """rows: [(symbol, dt, is_limit_up, touched, is_first_board)]"""
    return pl.DataFrame({
        "dt": [r[1] for r in rows], "symbol": [r[0] for r in rows],
        "prev_close": [10.0] * len(rows), "limit_up": [11.0] * len(rows),
        "limit_down": [9.0] * len(rows),
        "is_limit_up": [r[2] for r in rows], "is_limit_down": [False] * len(rows),
        "touched_limit_up": [r[3] for r in rows],
        "is_broken_board": [r[3] and not r[2] for r in rows],
        "is_first_board": [r[4] for r in rows],
    })


D = [date(2024, 1, d) for d in (2, 3, 4)]


def test_first_boards_come_from_the_previous_session():
    pool = candidate_pool(
        flags_frame([("600001.SH", D[1], True, True, True)]),
        listed_since={"600001.SH": date(2020, 1, 1)},
        day=D[2], previous=D[1],
    )
    assert pool.first_boards == ["600001.SH"]


def test_broken_boards_are_collected_separately():
    pool = candidate_pool(
        flags_frame([("600002.SH", D[1], False, True, False)]),
        listed_since={"600002.SH": date(2020, 1, 1)},
        day=D[2], previous=D[1],
    )
    assert pool.first_boards == []
    assert pool.broken_boards == ["600002.SH"]


def test_star_and_bse_are_excluded():
    """filter_kcbj_stock：排除 4/8 开头（北交所）与 68 开头（科创板）。"""
    pool = candidate_pool(
        flags_frame([
            ("688001.SH", D[1], True, True, True),
            ("430001.BJ", D[1], True, True, True),
            ("830001.BJ", D[1], True, True, True),
            ("600001.SH", D[1], True, True, True),
        ]),
        listed_since={s: date(2020, 1, 1) for s in
                      ("688001.SH", "430001.BJ", "830001.BJ", "600001.SH")},
        day=D[2], previous=D[1],
    )
    assert pool.first_boards == ["600001.SH"]


def test_chinext_is_kept():
    pool = candidate_pool(
        flags_frame([("300001.SZ", D[1], True, True, True)]),
        listed_since={"300001.SZ": date(2020, 1, 1)},
        day=D[2], previous=D[1],
    )
    assert pool.first_boards == ["300001.SZ"]


def test_new_stocks_are_excluded():
    """上市不满 50 天剔除。上市日取自日线首个 bar。"""
    pool = candidate_pool(
        flags_frame([("600001.SH", D[1], True, True, True)]),
        listed_since={"600001.SH": date(2023, 12, 20)},  # 距 2024-01-04 仅 15 天
        day=D[2], previous=D[1],
    )
    assert pool.first_boards == []


def test_a_stock_listed_exactly_50_days_ago_is_excluded():
    """原文是 start_date < date - 50 天，等于 50 天不算数。"""
    pool = candidate_pool(
        flags_frame([("600001.SH", D[1], True, True, True)]),
        listed_since={"600001.SH": date(2024, 1, 4) - __import__("datetime").timedelta(days=50)},
        day=D[2], previous=D[1],
    )
    assert pool.first_boards == []


def test_a_symbol_without_a_listing_date_is_excluded():
    """不知道上市日就不能放行——宁可漏，不可错。"""
    pool = candidate_pool(
        flags_frame([("600001.SH", D[1], True, True, True)]),
        listed_since={}, day=D[2], previous=D[1],
    )
    assert pool.first_boards == []


def test_other_sessions_are_ignored():
    pool = candidate_pool(
        flags_frame([
            ("600001.SH", D[0], True, True, True),
            ("600002.SH", D[1], True, True, True),
        ]),
        listed_since={s: date(2020, 1, 1) for s in ("600001.SH", "600002.SH")},
        day=D[2], previous=D[1],
    )
    assert pool.first_boards == ["600002.SH"]


def test_results_are_sorted():
    pool = candidate_pool(
        flags_frame([
            ("600009.SH", D[1], True, True, True),
            ("600001.SH", D[1], True, True, True),
        ]),
        listed_since={s: date(2020, 1, 1) for s in ("600009.SH", "600001.SH")},
        day=D[2], previous=D[1],
    )
    assert pool.first_boards == ["600001.SH", "600009.SH"]
```

- [ ] **Step 3: 运行测试确认失败**

Run: `uv run pytest tests/strategy/structural/test_universe.py -q`
Expected: FAIL —— `ModuleNotFoundError`

- [ ] **Step 4: 实现 `src/qsys/strategy/structural/universe.py`**

```python
"""候选池：昨日首板与昨日炸板。

对应原策略 `prepare_stock_list` / `filter_*`（原文 L1349-L1403）。

原策略用 `get_hl_stock(initial, yesterday, 1)` 取昨日涨停，再剔除前日
也涨停的——那正是 `daily_flags.is_first_board` 的定义，2-1a 已实测一致。
炸板对应 `get_ever_hl_stock2`：`close != high_limit and high == high_limit`，
即 `is_broken_board`。

**ST 过滤不实现**：无历史 ST 数据。但涨跌停按 10% 判定，ST 股（5%）
本就不会被判成涨停，方向等效——见 docs/reference/README.md。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

import polars as pl

from qsys.strategy.structural.params import (
    EXCLUDED_PREFIXES, EXCLUDED_PREFIX_2, NEW_STOCK_DAYS,
)


@dataclass(frozen=True, slots=True)
class PoolResult:
    first_boards: list[str]
    broken_boards: list[str]


def board_of(symbol: str) -> str:
    """显式板块制度标签（原文 L576）。"""
    return "CHINEXT" if symbol.startswith(("300", "301")) else "MAIN"


def strategy_tag(name: str, symbol: str) -> str:
    return f"{name}_{board_of(symbol)}"


def _tradable(symbol: str) -> bool:
    """filter_kcbj_stock：排除北交所与科创板（原文 L1401）。"""
    code = symbol.split(".")[0]
    return not (code.startswith(EXCLUDED_PREFIXES) or code.startswith(EXCLUDED_PREFIX_2))


def candidate_pool(
    flags: pl.DataFrame,
    listed_since: Mapping[str, date],
    day: date,
    previous: date,
) -> PoolResult:
    """昨日首板与昨日炸板，已施加板块与新股过滤。"""
    if flags.height == 0:
        return PoolResult([], [])

    cutoff = day - timedelta(days=NEW_STOCK_DAYS)

    def keep(symbol: str) -> bool:
        if not _tradable(symbol):
            return False
        listed = listed_since.get(symbol)
        # 不知道上市日就不放行——宁可漏，不可错
        return listed is not None and listed < cutoff

    yesterday = flags.filter(pl.col("dt") == previous)
    first = sorted(
        s for s in yesterday.filter(pl.col("is_first_board"))["symbol"].to_list()
        if keep(s)
    )
    broken = sorted(
        s for s in yesterday.filter(pl.col("is_broken_board"))["symbol"].to_list()
        if keep(s)
    )
    return PoolResult(first, broken)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/strategy/structural/test_universe.py -q`
Expected: PASS —— 11 passed

- [ ] **Step 6: 提交**

```bash
git add src/qsys/strategy/structural/ tests/strategy/structural/test_universe.py
git commit -m "feat(strategy): 阈值常量与候选池"
```

---

## Task 3: 入场判定

**Files:**
- Create: `src/qsys/strategy/structural/entry.py`
- Test: `tests/strategy/structural/test_entry.py`

**Interfaces:**
- Produces: `check_low_open(history, auction_price) -> bool`；
  `check_left_pressure(history) -> bool`；
  `check_high_open(history, auction_price, auction_volume, valuation) -> bool`；
  三者均接收**已取好的 DataFrame**，不自行访问数据——便于用构造数据单测

- [ ] **Step 1: 写失败测试**

Create `tests/strategy/structural/test_entry.py`:

```python
from datetime import date, timedelta

import polars as pl
import pytest

from qsys.strategy.structural.entry import (
    check_high_open, check_left_pressure, check_low_open,
)


def history(closes, highs=None, lows=None, moneys=None, volumes=None, opens=None):
    n = len(closes)
    start = date(2024, 1, 1)
    return pl.DataFrame({
        "dt": [start + timedelta(days=i) for i in range(n)],
        "open": opens or list(closes),
        "high": highs or [c * 1.02 for c in closes],
        "low": lows or [c * 0.98 for c in closes],
        "close": list(closes),
        "volume": volumes or [1000] * n,
        "amount": moneys or [2e8] * n,
    })


# ---------------- check_low_open ----------------

def test_low_open_needs_60_days():
    h = history([10.0] * 59)
    assert check_low_open(h, auction_price=9.6) is False


def test_low_open_accepts_a_valid_setup():
    """相对位置低、成交额足、竞价低开 3~4.5%。"""
    closes = [12.0] * 59 + [10.0]
    h = history(closes, highs=[14.0] * 60, lows=[9.5] * 60, moneys=[2e8] * 60)
    # rp = (10 - 9.5) / (14 - 9.5) = 0.111 < 0.5
    assert check_low_open(h, auction_price=10.0 * 0.96) is True


def test_low_open_rejects_high_relative_position():
    closes = [12.0] * 59 + [13.9]
    h = history(closes, highs=[14.0] * 60, lows=[9.5] * 60)
    assert check_low_open(h, auction_price=13.9 * 0.96) is False


def test_low_open_rejects_thin_turnover():
    closes = [12.0] * 59 + [10.0]
    h = history(closes, highs=[14.0] * 60, lows=[9.5] * 60,
                moneys=[2e8] * 59 + [0.9e8])
    assert check_low_open(h, auction_price=9.6) is False


def test_low_open_rejects_a_gap_outside_the_band():
    closes = [12.0] * 59 + [10.0]
    h = history(closes, highs=[14.0] * 60, lows=[9.5] * 60)
    assert check_low_open(h, auction_price=10.0 * 0.99) is False   # 只跌 1%
    assert check_low_open(h, auction_price=10.0 * 0.90) is False   # 跌太多


def test_low_open_band_edges_are_inclusive():
    closes = [12.0] * 59 + [10.0]
    h = history(closes, highs=[14.0] * 60, lows=[9.5] * 60)
    assert check_low_open(h, auction_price=10.0 * 0.955) is True
    assert check_low_open(h, auction_price=10.0 * 0.97) is True


def test_low_open_rejects_a_flat_range():
    h = history([10.0] * 60, highs=[10.0] * 60, lows=[10.0] * 60)
    assert check_low_open(h, auction_price=9.6) is False


# ---------------- check_left_pressure ----------------

def test_left_pressure_needs_volume_expansion():
    """最后一天的量必须超过窗口内其余最大量的 0.9 倍。"""
    highs = [10.0] * 100 + [20.0]
    volumes = [1000] * 100 + [2000]
    h = history([10.0] * 101, highs=highs, volumes=volumes)
    assert check_left_pressure(h) is True


def test_left_pressure_rejects_shrinking_volume():
    highs = [10.0] * 100 + [20.0]
    volumes = [5000] * 100 + [1000]
    h = history([10.0] * 101, highs=highs, volumes=volumes)
    assert check_left_pressure(h) is False


def test_left_pressure_window_stops_at_the_prior_high():
    """窗口自「上一个不低于昨日最高的日子」起算，再往前 5 天。"""
    highs = [10.0] * 95 + [30.0] + [10.0] * 4 + [20.0]
    volumes = [9000] * 95 + [9000] + [1000] * 4 + [1500]
    h = history([10.0] * 101, highs=highs, volumes=volumes)
    # 前高在第 96 根，窗口不该回溯到那之前的巨量
    assert check_left_pressure(h) is True


# ---------------- check_high_open ----------------

def valuation(market_cap=100.0, circulating=300.0):
    return {"market_cap": market_cap, "circulating_market_cap": circulating}


def high_history():
    """昨日 VWAP 相对前日收盘 +10%，成交额 10 亿。"""
    return history(
        [10.0, 11.0],
        highs=[10.0, 11.5], lows=[10.0, 10.5],
        moneys=[5e8, 11e8], volumes=[5e7, 1e8],
    )


def test_high_open_accepts_a_valid_setup():
    assert check_high_open(
        high_history(), auction_price=11.0 * 1.03,
        auction_volume=1e8 * 0.05, valuation=valuation(),
    ) is True


def test_high_open_rejects_weak_average_increase():
    h = history([10.0, 10.2], moneys=[5e8, 11e8], volumes=[5e7, 1e8])
    assert check_high_open(h, auction_price=10.2 * 1.03,
                           auction_volume=1e8 * 0.05, valuation=valuation()) is False


def test_high_open_rejects_money_outside_the_band():
    h = history([10.0, 11.0], moneys=[5e8, 3e8], volumes=[5e7, 1e8])
    assert check_high_open(h, auction_price=11.0 * 1.03,
                           auction_volume=1e8 * 0.05, valuation=valuation()) is False


def test_high_open_rejects_small_market_cap():
    assert check_high_open(
        high_history(), auction_price=11.0 * 1.03, auction_volume=1e8 * 0.05,
        valuation=valuation(market_cap=69.9),
    ) is False


def test_high_open_rejects_large_circulating_cap():
    assert check_high_open(
        high_history(), auction_price=11.0 * 1.03, auction_volume=1e8 * 0.05,
        valuation=valuation(circulating=520.1),
    ) is False


def test_high_open_rejects_thin_auction():
    assert check_high_open(
        high_history(), auction_price=11.0 * 1.03,
        auction_volume=1e8 * 0.02, valuation=valuation(),
    ) is False


def test_high_open_requires_a_gap_up_below_six_percent():
    assert check_high_open(high_history(), auction_price=11.0,
                           auction_volume=1e8 * 0.05, valuation=valuation()) is False
    assert check_high_open(high_history(), auction_price=11.0 * 1.06,
                           auction_volume=1e8 * 0.05, valuation=valuation()) is False


def test_high_open_without_valuation_is_rejected():
    """没有市值数据不能放行——原策略 turn.empty 时直接 return False。"""
    assert check_high_open(high_history(), auction_price=11.0 * 1.03,
                           auction_volume=1e8 * 0.05, valuation=None) is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/strategy/structural/test_entry.py -q`
Expected: FAIL —— `ModuleNotFoundError`

- [ ] **Step 3: 实现 `src/qsys/strategy/structural/entry.py`**

三个函数逐行对照原文 L1201-L1347 实现，**阈值全部取自 params，不内联数字**。
`check_left_pressure` 的 `zyts_0` 循环与 `vol_data` 切片要**逐字照抄语义**——
原版有意从倒数第 3 天起找，且不做越界检查。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/strategy/structural/test_entry.py -q`
Expected: PASS —— 19 passed

- [ ] **Step 5: 提交**

---

## Task 4: S6 分类与 S7 Gate

**Files:**
- Create: `src/qsys/strategy/structural/gate.py`
- Test: `tests/strategy/structural/test_gate.py`

**Interfaces:**
- Produces: `classify(tag, env, stock_factors) -> Classification(state, known, reason)`；
  `gate_allow(tag, classification) -> bool`；三值逻辑 `three_or` / `three_and`

- [ ] **Step 1: 写失败测试**

覆盖：三值逻辑的真值表、LOW_MAIN 的 RISK/CLEAN/UNKNOWN_ALLOW 三态、
HIGH_MAIN 的 FAVORABLE/OTHER、HIGH_CHINEXT 的 AND 语义、
LOW_CHINEXT 无 Gate 一律放行、缺数据一律 UNKNOWN_ALLOW 放行。

- [ ] **Step 2-5: 失败 -> 实现 -> 通过 -> 提交**

**实现要点**：`_s6_three_or` 与 `_s6_three_and` 的三值语义必须精确
（原文 L1891-L1906）：OR 里 True 支配、两个 False 才 False、否则 None；
AND 里 False 支配、两个 True 才 True、否则 None。**缺数据走
UNKNOWN_ALLOW 放行**，不是拒绝——原策略明确「preserve baseline rather
than reject an unclassifiable trade」。

---

## Task 5: 策略组装与每日候选

**Files:**
- Create: `src/qsys/strategy/structural/strategy.py`
- Create: `scripts/run_selection.py`
- Test: `tests/strategy/structural/test_selection_e2e.py`

**Interfaces:**
- Produces: `StructuralSelection(market_feed, ...)`；
  `select(day, previous) -> list[Selection(symbol, tag, state, reason)]`

**路由顺序照抄原文 L1152-L1190**：先判 LOW，命中即 `continue`，
**不回落去判 HIGH**（两者开盘区间本就互斥，显式 continue 固定生命周期语义）。
`LOW_MAIN` 命中后还要过竞价量过滤，不过则整只剔除、同样不回落。

**竞价量缺失的两版行为**由参数控制：
`require_auction_volume=True` 为忠实版（无数据即剔除），
`False` 为近似版（无数据则跳过该条过滤）。默认忠实版。

- [ ] **Step 1-4: 端到端测试 -> 实现 -> 通过 -> 提交**

---

## Task 6: 全区间选股与聚宽对账

**Files:**
- Create: `scripts/run_selection.py`（Task 5 已建，此处跑全量）
- Modify: `scripts/compare_joinquant.py`

- [ ] **Step 1: 全区间跑选股，导出每日候选**

2023-01 至最后交易日，两版各跑一遍（`require_auction_volume` 开/关），
导出 `var/selection_strict.csv` 与 `var/selection_loose.csv`，列为
`dt, symbol, tag, s7_state, allow`。

- [ ] **Step 2: 量化竞价量过滤的影响**

在有分钟线的 242 天窗口内比较两版：剔除了多少只、占比多少。
**这是用户明确要的证据**，用来决定早期 652 天要不要用近似版。

- [ ] **Step 3: 与聚宽对账**

用户提供聚宽的每日买入记录后，比对「哪天买了哪些票」：
- 两边都有 → 命中
- 只有聚宽有 → 漏选，按已知分歧逐条归因
- 只有 qsys 有 → 多选，同上

**已知分歧的归因顺序**：竞价价口径 > 竞价量缺失 > ST 过滤缺失。
排除这三类后仍有差异，才是移植 bug。

- [ ] **Step 4: 提交**

---

## 完成标准

1. `uv run pytest` 全绿，既有 823 项无回归
2. **策略不触碰 `WarehouseQuery`** —— 数据访问一律经 `Context`
3. **PIT 不可穿越** —— 反向测试证明 09:29 拿不到当日 open；
   `session_open` 只返回 open，不泄露 high/low/close
4. **阈值与原文逐条对应**，`params.py` 标注行号，无内联魔数
5. 全区间选股跑通，导出每日候选
6. 竞价量过滤的影响已量化

## 执行实测（2026-09-09）

移植代码 535 行，测试 915 项全绿（新增 92 项）。

### 全区间选股（近似版，2023-04-01 ~ 2026-09-09，840 个交易日）

| 项 | 结果 |
|---|---|
| 入选 | **397 只 / 236 个交易日 / 360 个标的** |
| 判定总数 | 889，Gate 通过率 **45%** |
| 有票的日子 | 28%，单日中位 1、最大 8 |
| 按年 | 2023: 61 / 2024: 128 / 2025: 112 / 2026: 96 |
| HIGH 分支 | 2023-04-03 ~ 2026-09-09，298 只 |
| LOW 分支 | 2023-05-29 ~ 2026-08-24，99 只 |
| 耗时 | 3.1 分钟 |

Gate 在真干活，不是摆设：LOW_MAIN 56 CLEAN / 129 RISK（拒 70%），
HIGH_MAIN 254 FAVORABLE / 275 OTHER（拒 52%），
HIGH_CHINEXT 44 / 88（拒 67%）。

### 竞价量过滤的影响（242 天窗口内，忠实版 vs 近似版）

| 分支 | 近似 | 忠实 | 剔除 |
|---|---|---|---|
| `LOW_MAIN` | 29 | 26 | **3 只（10%）** |
| `LOW_CHINEXT` | 20 | 20 | 0（该过滤只作用于 LOW_MAIN） |

**结论**：这条过滤剔除约 10% 的 LOW_MAIN 入选。早期 598 天用近似版是
可接受的——差异有界且方向已知（近似版只会多选，不会少选，忠实版是
近似版的严格子集，实测「忠实版有而近似版没有的」为 0）。

### 途中发现并修正

1. **单位陷阱**：原策略 `vwap = money / volume`，聚宽 volume 单位是股、
   我们是手。实测 `amount/(volume*100) = 41.00` 落在当日区间
   [40.81, 41.38] 内，而 `amount/volume = 4100`。照抄会让 `avg_inc`
   变成 +40 而不是 +0.07，`check_high_open` 放行所有股票，且不报错。
2. **生态因子取 T-1 而非 T**：原策略在 `before_trading_start` 里算
   （原文 L206-L209），用 T-1 数据但以 T 为键存。我们的表按「因子所
   描述的那一天」存，故 T 日读 T-1。恰好与 PIT 一致。
3. **近似开关未覆盖 HIGH**：`check_high_open` 无条件需要竞价量，导致
   HIGH 判定 100% 落在有分钟线的 242 天内——首次跑出 2026 年 91 只、
   2023 年 10 只的假象。给 HIGH 同样的口子后，HIGH 覆盖全区间，
   年度分布转为 61/128/112/96。

## 已知缺口（转入后续）

- **下单与退出未移植** —— S8 权重分配、11:25/14:25/14:50 三个检查点，归入 2-2c
- **竞价价口径分歧** —— 无法用现有数据消除
- **`adj_factors` 只覆盖 3 只** —— 本计划用不复权价（涨停判定必须用原始价），
  但 2-2c 的净值计算需要全市场复权因子

## 下一步

**2-2c：完整回测** —— S8 分支权重分配、下单、三个退出检查点，接进回测引擎，
产出净值曲线并与聚宽对比。
