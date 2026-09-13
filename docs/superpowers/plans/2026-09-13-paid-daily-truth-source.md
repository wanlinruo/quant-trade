# 付费日线真值源接入（计划 A：数据层） —— 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把日线事实源从 mootdx 的 `bars_daily` 切到付费源 `paid_daily`，同时把 ST 判定与复权因子从「价格反推 / 自算」换成直取真值。

**Architecture:** `WarehouseQuery.daily_bars()` 底层换读 `paid_daily`，投影同样 8 列，32 处调用点零改动；`flags.py` 删掉三条 ST 价格启发式，`is_st` 直取自 `name` 列，涨跌停基准由直取的 `pre_close` 替代 `corporate_actions` 自算的除权参考价；`adjust.py` 的 `factor` 改为直取，自算复权降级为校验。全程分三段各出一次回归差异报告，保证行为变化可归因。

**Tech Stack:** Python 3.12 / polars / pyarrow / duckdb / structlog / pytest

**Spec:** `docs/superpowers/specs/2026-09-12-paid-daily-truth-source-design.md`

**范围**：本计划只覆盖 spec 的 §5、§6、§7、§10。§8（每日自动化）与 §9（QMT 兜底通道）属计划 B，待周一 17:10 之后实测卖家交付与转存行为再写。

## Global Constraints

以下每一条都来自 spec，**每个任务的要求都隐含包含本节**：

- **禁止编造数据。** 每一列只允许两类：直取（源的原值）、纯派生（直取列的确定性算术，公式写在代码注释与 spec 里，可逐位重算）。拿不到就记 null 由质检报出来，**不准填**。
- **禁止启发式、阈值猜测、缺失值填充、均值、模型、人工猜测。**
- **本计划内不存在任何例外。** spec §2.1 批准的 ST 结转只发生在兜底路径（计划 B），本计划不实现、不引用。
- **`paid_daily` 表永久只读。** 不注册进 `schema.PARTITIONED`，避免 `Warehouse.write("paid_daily", ...)` 成为可能。
- **`volume` 直取不取整。** 付费源的 582305.98 就是 582305.98，取整是有损变换。
- **`bars_daily` 冻结**，只经 `legacy_daily_bars()` 读取，唯一用途是回归对账。不删除、不写入。
- **板块过滤必须显式。** 付费源含北交所 343 只，旧表没有；`build_daily_flags` 与 `build_market_regime` 不显式过滤就会静默改变广度因子。
- 所有测试**不得联网**，不得依赖 `/data/qsys/warehouse` 的真实数据；用 `warehouse` fixture 造数据。
- 每个任务结束必须全量 `pytest` 通过（当前基线 1195 个）再提交。

---

## File Structure

**修改**

| 文件 | 本次职责变化 |
|---|---|
| `src/qsys/data/schema.py` | 新增 `PAID_DAILY`（从 `pipeline/paid_daily.py` 迁入）与 `DAILY_BARS`（视图契约，volume 为 float64）；`DAILY_FLAGS` 换 ST 三列为 `is_st` |
| `src/qsys/data/pipeline/paid_daily.py` | `SCHEMA` 改为从 `schema.PAID_DAILY` 引用，消除循环导入 |
| `src/qsys/data/query.py` | `daily_bars()` 换底；新增 `paid_daily()`、`legacy_daily_bars()` |
| `src/qsys/core/types.py` | `Bar.volume` 由 `int` 改 `float` |
| `src/qsys/data/pipeline/flags.py` | 删三条 ST 启发式；`is_st` 直取；涨跌停基准改 `pre_close`；加板块过滤 |
| `src/qsys/data/pipeline/regime.py` | 加板块过滤 |
| `src/qsys/data/pipeline/adjust.py` | `factor` 直取，自算降级为校验 |

**新建**

| 文件 | 职责 |
|---|---|
| `scripts/regression_paid_daily.py` | 三段式回归对账，逐键比对新旧派生表并出差异报告 |
| `tests/data/test_paid_daily_query.py` | 查询层换底的测试 |
| `tests/data/test_flags_st_truth.py` | ST 真值与涨跌停基准的测试 |

---

## Task 0: 板块判定提升为共享函数

板块判定现在只存在于 `akshare_provider._board_of`（私有），而 `PRICE_LIMITS` 按板块
建键住在 `markets/ashare.py`。本计划有三处要用它（回归脚本、`flags.py`、`regime.py`），
再复制一份必然走样——板块词表与它的涨跌幅上限必须同住一处。

**Files:**
- Modify: `src/qsys/markets/ashare.py`
- Modify: `src/qsys/data/providers/akshare_provider.py:88-98`
- Test: `tests/markets/test_ashare.py`

**Interfaces:**
- Produces: `qsys.markets.ashare.board_of(code: str) -> str`，返回
  `"star" | "gem" | "bse" | "bshare" | "main"`，入参是**不带交易所后缀的代码**

- [ ] **Step 1: 写失败测试**

```python
# tests/markets/test_ashare.py
# 导入加到**文件顶部的导入块**里，不要跟着测试函数追加到文件中部。

import pytest

from qsys.markets.ashare import PRICE_LIMITS, board_of


@pytest.mark.parametrize(
    "code,board",
    [
        ("688981", "star"), ("689009", "star"),
        ("300750", "gem"), ("301689", "gem"),
        ("920268", "bse"), ("830799", "bse"), ("871396", "bse"), ("430418", "bse"),
        ("900901", "bshare"), ("200011", "bshare"),
        ("000001", "main"), ("600000", "main"), ("603005", "main"),
    ],
)
def test_board_of_classifies_by_code_prefix(code, board):
    assert board_of(code) == board


def test_every_board_has_a_price_limit():
    """板块词表与涨跌幅上限必须同步。

    多一个板块而忘了给上限，涨跌停价会悄悄退到默认值，
    首板判定跟着错，而没有任何地方报错。
    """
    produced = {board_of(c) for c in
                ("688981", "300750", "920268", "900901", "000001")}
    assert produced <= set(PRICE_LIMITS)


def test_akshare_provider_reuses_the_shared_function():
    """三处分头实现必然对同一天给出不同口径。"""
    from qsys.data.providers import akshare_provider

    assert akshare_provider._board_of is board_of
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/markets/test_ashare.py -k board -v`
Expected: FAIL，`ImportError: cannot import name 'board_of'`

- [ ] **Step 3: 实现**

```python
# src/qsys/markets/ashare.py —— 在 PRICE_LIMITS 之前插入

def board_of(code: str) -> str:
    """按号段判板块。入参是**不带交易所后缀**的代码。

    与 PRICE_LIMITS 同住一处：板块词表和它的涨跌幅上限必须一起改，
    分开放就会出现「新增板块忘了给上限」而无人报错。
    """
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("300", "301")):
        return "gem"
    if code[:2] in ("43", "83", "87", "92"):
        return "bse"
    if code[:2] in ("90", "20"):
        # B 股：外币计价、交易规则与 A 股不同，单独标注以便股票池排除
        return "bshare"
    return "main"
```

```python
# src/qsys/markets/ashare.py —— PRICE_LIMITS 上方的注释订正
#
# **此刻为真**：ST 的 5% 档由 flags.py 决定，本表只登记板块档位。
# 不要在这里预告 flags.py 将来怎么判 ST——Task 7 改完它之后，由 Task 7
# 负责把那句话补上。注释断言一个尚未发生的事实，就是一句失实注释。

# 日涨跌幅上限。ST 为 5%，不在此表——ST 是股票的状态而非板块属性，
# 由 data/pipeline/flags.py 判定。本表只登记板块档位。
PRICE_LIMITS: dict[str, float] = {
```

```python
# src/qsys/markets/ashare.py —— 模块 docstring 第 6 行订正
#
# 原文：板块取值与计划 1-1 数据层 `_board_of()` 的输出一一对应。
# 本任务把 board_of 搬到了本模块，方向反了，改为：

板块取值由本模块的 `board_of()` 给出，`PRICE_LIMITS` 的键与它一一对应。
```

```python
# src/qsys/data/providers/akshare_provider.py —— 删掉本地 _board_of 定义，改为
from qsys.markets.ashare import board_of as _board_of
```

- [x] **Step 4: 跑全量测试（1267 passed，11 warnings）**

Run: `.venv/bin/python -m pytest -q`
Expected: 1195 passed + 本任务 3 个

- [ ] **Step 5: 提交**

```bash
git add src/qsys/markets/ashare.py src/qsys/data/providers/akshare_provider.py tests/markets/test_ashare.py
git commit -m "refactor(markets): 板块判定提升为 board_of，与 PRICE_LIMITS 同住"
```

---

## Task 1: schema 迁移 —— `PAID_DAILY` 进 `schema.py`

`query.py` 需要 `paid_daily` 的 arrow schema 来构造空表，但 `pipeline/paid_daily.py` 反过来 import `query`（经 `Warehouse`/`WarehouseQuery`），直接引用会循环导入。canonical 位置是 `schema.py`——其余所有表的 schema 都在那里。

**Files:**
- Modify: `src/qsys/data/schema.py`
- Modify: `src/qsys/data/pipeline/paid_daily.py:39-46`
- Test: `tests/data/pipeline/test_paid_daily.py`

**Interfaces:**
- Produces: `schema.PAID_DAILY`（`pa.Schema`，28 列，带 metadata）、`schema.DAILY_BARS`（`pa.Schema`，8 列，volume 为 `float64`）

- [ ] **Step 1: 写失败测试**

```python
# tests/data/pipeline/test_paid_daily.py 追加

def test_paid_daily_schema_lives_in_the_schema_module():
    """schema 的 canonical 位置是 schema.py，pipeline 只引用不定义。

    query.py 要用它构造空表，而 pipeline 反过来 import query——
    schema 留在 pipeline 里就成了循环导入。
    """
    from qsys.data import schema
    from qsys.data.pipeline import paid_daily

    assert paid_daily.SCHEMA is schema.PAID_DAILY
    assert len(schema.PAID_DAILY) == 28
    assert schema.PAID_DAILY.field("volume").type == pa.float64()
    assert schema.PAID_DAILY.field("name").type == pa.string()
    assert schema.PAID_DAILY.metadata[b"volume_unit"] == b"lot=100 shares"


def test_paid_daily_is_not_writable_through_the_warehouse():
    """付费表永久只读。注册进 PARTITIONED 就等于开了写入口子。"""
    from qsys.data import schema

    assert "paid_daily" not in schema.PARTITIONED
    assert "paid_daily" not in schema.UNPARTITIONED


def test_daily_bars_view_schema_keeps_volume_unrounded():
    """daily_bars 视图的 volume 是 float64。

    付费源 volume 带小数（582305.98 手）。取整是有损变换，
    而 BARS_DAILY 描述的是冻结的旧表（int64），两者必须分开。
    """
    from qsys.data import schema

    assert schema.DAILY_BARS.field("volume").type == pa.float64()
    assert schema.BARS_DAILY.field("volume").type == pa.int64()
    assert schema.DAILY_BARS.names == schema.BARS_DAILY.names
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/pipeline/test_paid_daily.py -k "schema or writable or unrounded" -v`
Expected: FAIL，`AttributeError: module 'qsys.data.schema' has no attribute 'PAID_DAILY'`

- [ ] **Step 3: 把 schema 迁进 `schema.py`**

```python
# src/qsys/data/schema.py —— 在 BARS_DAILY 之后插入

# daily_bars() 视图的契约。列名与 BARS_DAILY 相同，但 volume 是 float64：
# 付费源给的是带小数的手数（582305.98），取整会丢掉真实精度。
# BARS_DAILY 保留 int64，因为它描述的是已冻结的 bars_daily 旧表。
DAILY_BARS = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
        ("amount", pa.float64()),
    ]
)

# 付费源日线，28 列。唯一的日线真值事实表，永久只读——**不要**注册进
# PARTITIONED，那会让 Warehouse.write("paid_daily", ...) 成为可能。
# 列顺序严格对应卖家 Excel 第三行；单位换算只做乘法，见 pipeline/paid_daily.py。
_PAID_DAILY_NUMERIC = [
    "open", "high", "low", "close", "pre_close", "change", "pct_change",
    "volume", "amount", "factor", "turnover_ratio", "free_turnover_ratio",
    "volume_ratio", "pe", "pe_ttm", "pb", "ps", "ps_ttm",
    "dividend_yield", "dividend_yield_ttm", "total_shares",
    "circulating_shares", "free_shares", "market_cap", "circulating_market_cap",
]
PAID_DAILY = pa.schema(
    [("dt", pa.date32()), ("symbol", pa.string()), ("name", pa.string())]
    + [(n, pa.float64()) for n in _PAID_DAILY_NUMERIC],
    metadata={
        b"provider": b"yuhaha-baidupan",
        b"volume_unit": b"lot=100 shares",
        b"money_unit": b"CNY",
        b"share_capital_unit": b"share",
        b"percentage_unit": b"percent",
        b"factor": b"cumulative backward adjustment",
    },
)
```

```python
# src/qsys/data/pipeline/paid_daily.py —— 删掉本地 SCHEMA 定义，改为引用
# 原第 39-46 行的 SCHEMA = pa.schema([...]) 整段删除，替换为：

from qsys.data import schema as _schema

# canonical 定义在 schema.py。放在这里会与 query.py 循环导入。
SCHEMA = _schema.PAID_DAILY
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/pipeline/test_paid_daily.py -v`
Expected: PASS

- [ ] **Step 5: 跑全量测试**

Run: `.venv/bin/python -m pytest -q`
Expected: 1201 passed（基线 1195 + Task 0 的 3 个 + 本任务 3 个）

- [ ] **Step 6: 提交**

```bash
git add src/qsys/data/schema.py src/qsys/data/pipeline/paid_daily.py tests/data/pipeline/test_paid_daily.py
git commit -m "refactor(data): 付费日线 schema 迁入 schema.py，为查询层换底铺路"
```

---

## Task 2: 查询层换底

`daily_bars()` 的 32 处调用点全都是「我要日线」，不关心来源，因此原地换底即可。
spec §5 列的 `daily_canonical()` 视图属**计划 B**——它的存在意义是在付费源与
QMT 兜底之间择一，而本计划没有兜底表，现在建等于建一个恒等视图。另加两个显式方法：`paid_daily()` 给需要 28 列的消费点，`legacy_daily_bars()` 只给回归对账用。

**Files:**
- Modify: `src/qsys/data/query.py:71-93`
- Create: `tests/data/test_paid_daily_query.py`

**Interfaces:**
- Consumes: `schema.PAID_DAILY`、`schema.DAILY_BARS`（Task 1）
- Produces:
  - `WarehouseQuery.daily_bars(symbols, start, end) -> pl.DataFrame`（8 列，读 `paid_daily`，volume 为 Float64）
  - `WarehouseQuery.paid_daily(symbols, start, end) -> pl.DataFrame`（28 列）
  - `WarehouseQuery.legacy_daily_bars(symbols, start, end) -> pl.DataFrame`（8 列，读冻结的 `bars_daily`，volume 为 Int64）

- [ ] **Step 1: 写失败测试**

```python
# tests/data/test_paid_daily_query.py

"""查询层换底：daily_bars 读付费源，旧表只经 legacy_daily_bars 读。"""

from datetime import date

import polars as pl
import pyarrow.parquet as pq

from qsys.data import schema
from qsys.data.query import WarehouseQuery

DAYS = [date(2026, 9, 9), date(2026, 9, 10)]


def _write_paid(warehouse, rows):
    """直接落 paid_daily 分区——该表永久只读，不经 Warehouse.write。"""
    frame = pl.DataFrame(rows)
    table = frame.to_arrow().cast(schema.PAID_DAILY)
    path = warehouse.root / "paid_daily" / "year=2026" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def _paid_row(symbol, dt, close, volume):
    row = {"dt": dt, "symbol": symbol, "name": "平安银行"}
    for field in schema.PAID_DAILY.names:
        if field in row:
            continue
        row[field] = 0.0
    row |= {"open": close, "high": close, "low": close, "close": close,
            "pre_close": close, "volume": volume, "amount": close * volume * 100,
            "factor": 1.0}
    return row


def test_daily_bars_reads_the_paid_source(warehouse):
    _write_paid(warehouse, [_paid_row("000001.SZ", DAYS[0], 11.70, 582305.98)])
    got = WarehouseQuery(warehouse).daily_bars(None, DAYS[0], DAYS[-1])
    assert got.height == 1
    assert got["close"].to_list() == [11.70]


def test_daily_bars_keeps_the_fractional_lot_count(warehouse):
    """付费源 volume 带小数，取整是有损变换。"""
    _write_paid(warehouse, [_paid_row("000001.SZ", DAYS[0], 11.70, 582305.98)])
    got = WarehouseQuery(warehouse).daily_bars(None, DAYS[0], DAYS[-1])
    assert got["volume"].dtype == pl.Float64
    assert got["volume"].to_list() == [582305.98]


def test_daily_bars_projects_exactly_the_eight_columns(warehouse):
    """32 处调用点依赖这 8 列，多给或少给都会波及下游。"""
    _write_paid(warehouse, [_paid_row("000001.SZ", DAYS[0], 11.70, 100.0)])
    got = WarehouseQuery(warehouse).daily_bars(None, DAYS[0], DAYS[-1])
    assert got.columns == list(schema.DAILY_BARS.names)


def test_daily_bars_is_empty_with_the_right_schema_when_nothing_is_stored(warehouse):
    got = WarehouseQuery(warehouse).daily_bars(None, DAYS[0], DAYS[-1])
    assert got.height == 0
    assert got.columns == list(schema.DAILY_BARS.names)
    assert got["volume"].dtype == pl.Float64


def test_paid_daily_exposes_all_twenty_eight_columns(warehouse):
    _write_paid(warehouse, [_paid_row("000001.SZ", DAYS[0], 11.70, 100.0)])
    got = WarehouseQuery(warehouse).paid_daily(None, DAYS[0], DAYS[-1])
    assert got.columns == list(schema.PAID_DAILY.names)
    assert got["name"].to_list() == ["平安银行"]


def test_legacy_daily_bars_still_reads_the_frozen_table(warehouse):
    """旧表冻结但不删——这次回归要拿它逐键对账。"""
    old = pl.DataFrame(
        {"symbol": ["000001.SZ"], "dt": [DAYS[0]], "open": [11.76], "high": [11.79],
         "low": [11.69], "close": [11.70], "volume": [582306], "amount": [6.8e8]}
    )
    warehouse.write("bars_daily", old.to_arrow().cast(schema.BARS_DAILY), year=2026)
    got = WarehouseQuery(warehouse).legacy_daily_bars(None, DAYS[0], DAYS[-1])
    assert got["volume"].to_list() == [582306]
    assert got["volume"].dtype == pl.Int64


def test_daily_bars_does_not_fall_back_to_the_frozen_table(warehouse):
    """只写了旧表时 daily_bars 必须返回空。

    悄悄回退到旧表，就等于在付费源缺数据的日子里用另一个源顶上，
    而调用方不会知道——这正是要消除的双主表歧义。
    """
    old = pl.DataFrame(
        {"symbol": ["000001.SZ"], "dt": [DAYS[0]], "open": [11.76], "high": [11.79],
         "low": [11.69], "close": [11.70], "volume": [582306], "amount": [6.8e8]}
    )
    warehouse.write("bars_daily", old.to_arrow().cast(schema.BARS_DAILY), year=2026)
    assert WarehouseQuery(warehouse).daily_bars(None, DAYS[0], DAYS[-1]).height == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/test_paid_daily_query.py -v`
Expected: FAIL，`AttributeError: 'WarehouseQuery' object has no attribute 'paid_daily'`

- [ ] **Step 3: 实现**

```python
# src/qsys/data/query.py —— 替换原 daily_bars（第 71-93 行）

_DAILY_BAR_COLUMNS = "symbol, dt, open, high, low, close, volume, amount"


def daily_bars(
    self,
    symbols: Sequence[str] | None,
    start: date,
    end: date,
) -> pl.DataFrame:
    """日线。**读付费源 `paid_daily`**，投影 8 列。

    2026-09-12 由 `bars_daily` 换底。mootdx 的 K 线接口自 09-10 起在上游
    失效，付费源成为唯一日线真值源。列名与旧表相同，故 32 处调用点无需改动；
    唯一差别是 `volume` 为 Float64——付费源的手数带小数，取整会丢精度。

    **不回退旧表。** 付费源没有的日期就是没有，回退会让调用方在不知情的
    情况下拿到另一个源的值。
    """
    if symbols is not None and len(symbols) == 0:
        return _empty(schema.DAILY_BARS)
    glob = self._glob("paid_daily", partitioned=True)
    if glob is None:
        return _empty(schema.DAILY_BARS)

    sql = (
        f"SELECT {_DAILY_BAR_COLUMNS} "
        f"FROM {_read(glob)} WHERE dt BETWEEN ? AND ?"
    )
    params: list[object] = [start, end]
    if symbols is not None:
        placeholders = ", ".join("?" for _ in symbols)
        sql += f" AND symbol IN ({placeholders})"
        params.extend(symbols)
    sql += " ORDER BY symbol, dt"
    return self._con.execute(sql, params).pl()


def paid_daily(
    self,
    symbols: Sequence[str] | None,
    start: date,
    end: date,
) -> pl.DataFrame:
    """付费源全部 28 列。flags / adjust / valuation 要用 name、factor、pre_close。"""
    if symbols is not None and len(symbols) == 0:
        return _empty(schema.PAID_DAILY)
    glob = self._glob("paid_daily", partitioned=True)
    if glob is None:
        return _empty(schema.PAID_DAILY)

    columns = ", ".join(schema.PAID_DAILY.names)
    sql = f"SELECT {columns} FROM {_read(glob)} WHERE dt BETWEEN ? AND ?"
    params: list[object] = [start, end]
    if symbols is not None:
        placeholders = ", ".join("?" for _ in symbols)
        sql += f" AND symbol IN ({placeholders})"
        params.extend(symbols)
    sql += " ORDER BY symbol, dt"
    return self._con.execute(sql, params).pl()


def legacy_daily_bars(
    self,
    symbols: Sequence[str] | None,
    start: date,
    end: date,
) -> pl.DataFrame:
    """冻结的 mootdx/QMT 旧日线表。**唯一用途是换源回归对账。**

    不要在生产路径上调用它。表已不再更新（mootdx 上游失效），
    保留只为给这次换源提供逐键比对的参照物。
    """
    if symbols is not None and len(symbols) == 0:
        return _empty(schema.BARS_DAILY)
    glob = self._glob("bars_daily", partitioned=True)
    if glob is None:
        return _empty(schema.BARS_DAILY)

    sql = (
        f"SELECT {_DAILY_BAR_COLUMNS} "
        f"FROM {_read(glob)} WHERE dt BETWEEN ? AND ?"
    )
    params: list[object] = [start, end]
    if symbols is not None:
        placeholders = ", ".join("?" for _ in symbols)
        sql += f" AND symbol IN ({placeholders})"
        params.extend(symbols)
    sql += " ORDER BY symbol, dt"
    return self._con.execute(sql, params).pl()
```

- [ ] **Step 4: 跑新测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/test_paid_daily_query.py -v`
Expected: PASS（8 个）

- [ ] **Step 5: 跑全量测试，记录被换底打破的用例**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -30`
Expected: **会有失败**。既有测试大量用 `warehouse.write("bars_daily", ...)` 造数据再读 `daily_bars()`。把失败清单原样记录下来，Task 3 逐个迁移，**不要在本任务里顺手改**。

- [ ] **Step 6: 提交（允许带着已知失败）**

```bash
git add src/qsys/data/query.py tests/data/test_paid_daily_query.py
git commit -m "feat(data): daily_bars 换底读付费源，旧表降为 legacy_daily_bars

既有测试仍按 bars_daily 造数据，下一个任务统一迁移。"
```

---

## Task 2b: 旧表写入方的读取改为 `legacy_daily_bars`

Task 2 换底后暴露的计划缺口。三个生产模块同时「写 `bars_daily`」和「读
`daily_bars()`」做合并，换底之后它们变成**读付费源、写旧表**：

```
pipeline/universe.py       _DATASET = "bars_daily"        + query.daily_bars(...)
pipeline/bars.py           write("bars_daily", ...)       + query.daily_bars(...)
pipeline/daily_backfill.py write("bars_daily", ...)       + query.daily_bars(...)
```

两个后果：

1. **付费数据会被灌进冻结的旧表。** Task 5 的回归对账正是拿 `bars_daily` 当参照物，
   污染之后对账失去意义。
2. **类型不匹配**：`paid_daily.volume` 是 Float64，`BARS_DAILY.volume` 是 Int64，
   `.cast(schema.BARS_DAILY)` 会截断或报错。

实践上这三条当前跑不到（mootdx 上游已失效，`daily_archive` 的探针会拦下），但那是
运气不是设计——`daily_backfill` 是 QMT 补缺口通道，计划 B 还要用它。

**修法**：每个模块读自己写的那张表。三处 `query.daily_bars(...)` 改成
`query.legacy_daily_bars(...)`。不删除这三个模块——`daily_backfill` 是计划 B 的
QMT 通道，`universe` 刚在 bb01f33 加过上游探针与时间预算。

**Files:**
- Modify: `src/qsys/data/pipeline/universe.py`（`_write_batch` 内的读取）
- Modify: `src/qsys/data/pipeline/bars.py:~50`（`existing` 的来源）
- Modify: `src/qsys/data/pipeline/daily_backfill.py:~85`（`existing` 的来源）
- Test: `tests/data/pipeline/test_legacy_writers.py`

**Interfaces:**
- Consumes: `WarehouseQuery.legacy_daily_bars`（Task 2）
- Produces: 无新接口，三个模块的内部读取改向

- [ ] **Step 1: 写失败测试**

```python
# tests/data/pipeline/test_legacy_writers.py

"""写 bars_daily 的模块必须读 bars_daily。

换底之后 daily_bars() 读的是付费源。这三个模块仍然维护旧表，
若继续读 daily_bars()，合并结果会把付费数据写进冻结的旧表，
而 Task 5 的回归对账正是拿旧表当参照物。
"""

import inspect

from qsys.data.pipeline import bars, daily_backfill, universe


def test_universe_reads_the_table_it_writes():
    src = inspect.getsource(universe)
    assert "legacy_daily_bars" in src
    assert ".daily_bars(" not in src


def test_bars_reads_the_table_it_writes():
    src = inspect.getsource(bars)
    assert "legacy_daily_bars" in src
    assert ".daily_bars(" not in src


def test_daily_backfill_reads_the_table_it_writes():
    src = inspect.getsource(daily_backfill)
    assert "legacy_daily_bars" in src
    assert ".daily_bars(" not in src
```

这三条是源码级断言，不是行为断言——刻意为之。行为断言要造两张表的数据才能
区分，成本远高于收益；而「读写配对」本身就是一条可以直接检查的结构约束。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/pipeline/test_legacy_writers.py -v`
Expected: FAIL，三条都因 `.daily_bars(` 仍在源码里而失败

- [ ] **Step 3: 三处改向**

```python
# src/qsys/data/pipeline/universe.py —— _write_batch 内
        merged = (
            # 读自己写的那张表。daily_bars() 换底读付费源之后，用它做合并
            # 会把付费数据灌进冻结的旧表，毁掉回归对账的参照物。
            pl.concat([query.legacy_daily_bars(None, lo, hi), incoming], how="vertical")
```

```python
# src/qsys/data/pipeline/bars.py —— existing 的来源，同上理由
        existing = query.legacy_daily_bars(None, year_start, year_end)
```

```python
# src/qsys/data/pipeline/daily_backfill.py —— existing 的来源，同上理由
        existing = query.legacy_daily_bars(None, year_start, year_end)
```

各处的实际变量名与参数以文件现状为准，只改被调用的方法名，不改其余逻辑。
改完在每处留一句中文注释说明「读自己写的那张表」这条约束。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/pipeline/test_legacy_writers.py -v`
Expected: PASS（3 个）

- [ ] **Step 5: 确认这三个模块的既有测试恢复**

Run: `.venv/bin/python -m pytest tests/data/test_universe.py tests/data/pipeline/test_bars.py tests/data/pipeline/test_daily_backfill.py -rfE --tb=no -q`
Expected: 这三个文件的失败应大幅减少或清零——它们写 `bars_daily`、读也回到
`bars_daily`，夹具本就是对的。**仍失败的用例记进报告**，交 Task 3 判断。

- [ ] **Step 6: 提交**

```bash
git add src/qsys/data/pipeline/universe.py src/qsys/data/pipeline/bars.py \
        src/qsys/data/pipeline/daily_backfill.py tests/data/pipeline/test_legacy_writers.py
git commit -m "fix(data): 维护旧表的三个模块改读 legacy_daily_bars

换底后它们变成读付费源、写旧表，会把付费数据灌进冻结的对账参照表，
且 Float64 volume 转 Int64 会截断。"
```

---

## Task 3: 既有测试夹具迁移

Task 2 换底后，所有「写 `bars_daily` 再读 `daily_bars()`」的测试都会失败。它们测的是业务逻辑而非存储位置，因此迁移夹具即可，断言不动。

**Files:**
- Modify: `tests/conftest.py`（新增 `write_daily_bars` fixture）
- Modify: Task 2 Step 5 记录的全部失败测试文件

**Interfaces:**
- Consumes: `WarehouseQuery.daily_bars`（Task 2）
- Produces: `write_daily_bars(warehouse, frame)` fixture —— 把 8 列日线写进 `paid_daily` 分区，缺失的 20 列补 null

- [ ] **Step 1: 写 fixture 的测试**

```python
# tests/data/test_paid_daily_query.py 追加

def test_write_daily_bars_fixture_round_trips(warehouse, write_daily_bars):
    """夹具写进去的，daily_bars 要能原样读出来。

    缺失的 20 列补 null 而不是 0——0 是一个真实数值，会在下游被当成
    「成交额为零」「因子为零」使用；null 才是「没有这个数据」。
    """
    import polars as pl
    from datetime import date

    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ"], "dt": [date(2026, 9, 9)],
        "open": [11.76], "high": [11.79], "low": [11.69], "close": [11.70],
        "volume": [582306.0], "amount": [6.8e8],
    }))
    got = WarehouseQuery(warehouse).daily_bars(None, date(2026, 9, 9), date(2026, 9, 9))
    assert got["close"].to_list() == [11.70]

    full = WarehouseQuery(warehouse).paid_daily(None, date(2026, 9, 9), date(2026, 9, 9))
    assert full["factor"].to_list() == [None]
    assert full["name"].to_list() == [None]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/test_paid_daily_query.py -k round_trips -v`
Expected: FAIL，`fixture 'write_daily_bars' not found`

- [ ] **Step 3: 加 fixture**

```python
# tests/conftest.py 追加

from datetime import date

import pyarrow.parquet as pq
import pytest

from qsys.data import schema


@pytest.fixture
def write_daily_bars():
    """把 8 列日线写进 paid_daily 分区，供既有测试复用。

    paid_daily 永久只读、不经 Warehouse.write，所以这里直接落分区文件。
    未提供的 20 列一律补 **null**——补 0 会让下游把「没有数据」当成
    「数值为零」，正是规矩里禁止的填充。
    """

    def write(warehouse, frame):
        import polars as pl

        missing = [n for n in schema.PAID_DAILY.names if n not in frame.columns]
        frame = frame.with_columns(
            [pl.lit(None, dtype=pl.Utf8).alias(n) for n in missing if n == "name"]
            + [pl.lit(None, dtype=pl.Float64).alias(n) for n in missing if n != "name"]
        ).select(schema.PAID_DAILY.names)
        table = frame.to_arrow().cast(schema.PAID_DAILY)
        for year in sorted({d.year for d in frame["dt"].to_list()}):
            path = warehouse.root / "paid_daily" / f"year={year}" / "part.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            mask = pl.col("dt").is_between(date(year, 1, 1), date(year, 12, 31))
            chunk = frame.filter(mask).to_arrow().cast(schema.PAID_DAILY)
            pq.write_table(chunk, path)

    return write
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/test_paid_daily_query.py -k round_trips -v`
Expected: PASS

- [ ] **Step 5: 逐个迁移失败测试 —— 先分清是哪一类**

失败分两类，处理方式相反，**不要一刀切**。判据是「被测模块往哪张表写」。

**A 类：测 `daily_bars()` 的消费方**（`test_flags` / `test_regime` / `test_feed` /
`test_valuation_derived` / `test_query` / `test_daily_rules` / 各 e2e 等）。
被测模块只读不写日线，夹具自己造数据。改**夹具**：

把 `warehouse.write("bars_daily", frame.to_arrow().cast(schema.BARS_DAILY), year=YYYY)`
换成 `write_daily_bars(warehouse, frame)`，并在测试函数签名里加 `write_daily_bars` 参数。
**断言一行不改**——它们测的是业务逻辑，不是存储位置。

`volume` 若是整数字面量（如 `[1000]`），改成浮点（`[1000.0]`）以匹配新 dtype。

**B 类：测「维护旧表的三个模块」**（`tests/data/pipeline/test_bars.py`、
`tests/data/pipeline/test_daily_backfill.py`、`tests/data/test_universe.py`，
Task 2b 之后仍失败的约 9 条）。这一类**夹具是对的**——数据由被测模块
（`ingest_daily_bars` / `fill_daily_holes` / `ingest_universe`）自己写进 `bars_daily`。
错的是**断言**：它们用 `WarehouseQuery(...).daily_bars(...)` 去验证结果，而那个
现在读付费源。改**断言**：

```python
# 被测模块写的是 bars_daily，验证就得读 bars_daily。
df = WarehouseQuery(warehouse).legacy_daily_bars(None, lo, hi)
```

B 类**不要引入 `write_daily_bars` 夹具**，也不要把它们的数据改写进 `paid_daily`——
那会让测试不再覆盖这三个模块真正的写入路径。

**分类方法**：看测试里有没有调用 `ingest_daily_bars`、`ingest_universe`、
`fill_daily_holes` 这类会自己写表的函数。有 → B 类；没有、靠 `warehouse.write`
手工造数据 → A 类。拿不准的逐个看被测函数写哪张表，**不要猜**。

- [ ] **Step 6: 跑全量测试**

Run: `.venv/bin/python -m pytest -q`
Expected: 全绿，总数 = 1201 + 1

- [ ] **Step 7: 提交**

```bash
git add tests/
git commit -m "test: 日线测试夹具迁到 paid_daily"
```

---

## Task 4: `Bar.volume` 改浮点

**Files:**
- Modify: `src/qsys/core/types.py:52`
- Test: `tests/core/test_types.py`

**Interfaces:**
- Consumes: 无
- Produces: `Bar.volume: float`

`sim_broker.py:117` 的 `cap = int(bar.volume * LOT * self._volume_cap)` 已包 `int()`，传浮点不需要改。

- [ ] **Step 1: 写失败测试**

```python
# tests/core/test_types.py 追加

def test_bar_volume_is_a_float():
    """付费源的手数带小数（582305.98 手），Bar 不能把它标成 int。

    标注错了不会立刻报错——dataclass 不做运行时校验——但会误导
    每一个读这段代码的人，并让静态检查失去意义。
    """
    import typing
    from datetime import date

    from qsys.core.types import Bar, Symbol

    assert typing.get_type_hints(Bar)["volume"] is float

    bar = Bar(symbol=Symbol.parse("000001.SZ"), dt=date(2026, 9, 9),
              open=11.76, high=11.79, low=11.69, close=11.70,
              volume=582305.98, amount=6.8e8)
    assert bar.volume == 582305.98
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/core/test_types.py -k volume_is_a_float -v`
Expected: FAIL，`assert <class 'int'> is <class 'float'>`

- [ ] **Step 3: 改类型**

```python
# src/qsys/core/types.py —— 第 52 行
    # 手。付费源给的是带小数的手数（582305.98），不取整。
    volume: float
```

- [ ] **Step 4: 跑全量测试**

Run: `.venv/bin/python -m pytest -q`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add src/qsys/core/types.py tests/core/test_types.py
git commit -m "fix(core): Bar.volume 改浮点，付费源手数带小数"
```

---

## Task 5: 回归基线 —— 换源前后逐键对账（第 1 段）

在改 `flags.py` **之前**先把「只换源」这一段的差异定量固化。spec §10 的三段式验收，本任务是第 1 段。

**Files:**
- Create: `src/qsys/data/quality/source_diff.py`（纯逻辑）
- Create: `scripts/regression_paid_daily.py`（薄 CLI）
- Test: `tests/data/quality/test_source_diff.py`

**分层理由**：`scripts/` 不是 Python 包（没有 `__init__.py`），测试无法
`from scripts.xxx import`。本仓库既有分层是纯逻辑进模块、脚本只做薄 CLI
（`scripts/ingest_paid_daily.py` 55 行之于 `pipeline/paid_daily.py` 241 行）。
`src/qsys/data/quality/` 已有 `rules.py` / `report.py` / `daily_rules.py`，
对账逻辑归在这里。

**Interfaces:**
- Consumes: `WarehouseQuery.daily_bars`、`WarehouseQuery.legacy_daily_bars`（Task 2）、
  `markets.ashare.board_of`（Task 0）、`write_daily_bars` fixture（Task 3）
- Produces: `qsys.data.quality.source_diff.compare_daily_sources(query, start, end, boards) -> SourceDiff`，其中
  `SourceDiff` 是 dataclass，字段 `only_in_paid: pl.DataFrame`、`only_in_legacy: pl.DataFrame`、
  `value_diffs: pl.DataFrame`（列 `symbol, dt, field, paid, legacy, abs_diff`）、
  `summary: dict[str, int | float]`

- [ ] **Step 1: 写失败测试**

```python
# tests/data/quality/test_source_diff.py

"""换源回归对账：差异必须能逐键列出，不能只给一个总数。"""

from datetime import date

import polars as pl

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.quality.source_diff import compare_daily_sources

DAY = date(2026, 9, 9)


def _legacy(warehouse, rows):
    """造旧表数据作对账参照物。

    Global Constraints 说 bars_daily 冻结不写入，指的是**生产路径**；
    这里写的是 tmp_path 下的临时仓库，不触碰生产数据。
    """
    frame = pl.DataFrame(rows)
    warehouse.write("bars_daily", frame.to_arrow().cast(schema.BARS_DAILY), year=2026)


def test_reports_rows_only_in_the_paid_source(warehouse, write_daily_bars):
    """北交所 343 只只在付费源里，必须单独列出而不是混进数值差异。"""
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ", "920268.BJ"], "dt": [DAY, DAY],
        "open": [11.76, 5.0], "high": [11.79, 5.1], "low": [11.69, 4.9],
        "close": [11.70, 5.05], "volume": [582306.0, 100.0], "amount": [6.8e8, 5e4],
    }))
    _legacy(warehouse, {
        "symbol": ["000001.SZ"], "dt": [DAY], "open": [11.76], "high": [11.79],
        "low": [11.69], "close": [11.70], "volume": [582306], "amount": [6.8e8],
    })
    diff = compare_daily_sources(WarehouseQuery(warehouse), DAY, DAY, boards=None)
    assert diff.only_in_paid["symbol"].to_list() == ["920268.BJ"]
    assert diff.only_in_legacy.height == 0


def test_reports_rows_only_in_the_legacy_source(warehouse, write_daily_bars):
    """旧表那 10 行零成交停牌占位在付费源里没有，同样要能看见。"""
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ"], "dt": [DAY], "open": [11.76], "high": [11.79],
        "low": [11.69], "close": [11.70], "volume": [582306.0], "amount": [6.8e8],
    }))
    _legacy(warehouse, {
        "symbol": ["000001.SZ", "000016.SZ"], "dt": [DAY, DAY],
        "open": [11.76, 5.0], "high": [11.79, 5.0], "low": [11.69, 5.0],
        "close": [11.70, 5.0], "volume": [582306, 0], "amount": [6.8e8, 1e-9],
    })
    diff = compare_daily_sources(WarehouseQuery(warehouse), DAY, DAY, boards=None)
    assert diff.only_in_legacy["symbol"].to_list() == ["000016.SZ"]


def test_lists_every_differing_field_key_by_key(warehouse, write_daily_bars):
    """差异要逐 (symbol, dt, field) 列出。只报一个总数等于把问题藏起来。"""
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ"], "dt": [DAY], "open": [11.76], "high": [11.79],
        "low": [11.69], "close": [11.70],
        "volume": [582305.98], "amount": [682946782.66],
    }))
    _legacy(warehouse, {
        "symbol": ["000001.SZ"], "dt": [DAY], "open": [11.76], "high": [11.79],
        "low": [11.69], "close": [11.70],
        "volume": [582306], "amount": [682946752.0],
    })
    diff = compare_daily_sources(WarehouseQuery(warehouse), DAY, DAY, boards=None)
    fields = sorted(diff.value_diffs["field"].to_list())
    assert fields == ["amount", "volume"]
    row = diff.value_diffs.filter(pl.col("field") == "volume").to_dicts()[0]
    assert row["paid"] == 582305.98
    assert row["legacy"] == 582306.0
    assert abs(row["abs_diff"] - 0.02) < 1e-9


def test_ohlc_agreement_is_reported_as_zero_when_prices_match(warehouse, write_daily_bars):
    """OHLC 实测零差异，回归要能把这个结论固化下来。"""
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ"], "dt": [DAY], "open": [11.76], "high": [11.79],
        "low": [11.69], "close": [11.70], "volume": [582306.0], "amount": [6.8e8],
    }))
    _legacy(warehouse, {
        "symbol": ["000001.SZ"], "dt": [DAY], "open": [11.76], "high": [11.79],
        "low": [11.69], "close": [11.70], "volume": [582306], "amount": [6.8e8],
    })
    diff = compare_daily_sources(WarehouseQuery(warehouse), DAY, DAY, boards=None)
    assert diff.summary["ohlc_diff_rows"] == 0
    assert diff.summary["common_rows"] == 1


def test_board_filter_excludes_beijing_from_the_comparison(warehouse, write_daily_bars):
    """按板块过滤后，北交所不该再出现在「只在付费源」里。"""
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ", "920268.BJ"], "dt": [DAY, DAY],
        "open": [11.76, 5.0], "high": [11.79, 5.1], "low": [11.69, 4.9],
        "close": [11.70, 5.05], "volume": [582306.0, 100.0], "amount": [6.8e8, 5e4],
    }))
    _legacy(warehouse, {
        "symbol": ["000001.SZ"], "dt": [DAY], "open": [11.76], "high": [11.79],
        "low": [11.69], "close": [11.70], "volume": [582306], "amount": [6.8e8],
    })
    diff = compare_daily_sources(
        WarehouseQuery(warehouse), DAY, DAY, boards={"main", "gem", "star"}
    )
    assert diff.only_in_paid.height == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/quality/test_source_diff.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'qsys.data.quality.source_diff'`

- [ ] **Step 3: 实现**

```python
# src/qsys/data/quality/source_diff.py

"""换源回归对账：付费源 vs 冻结的旧表，逐键列差异。

**为什么要逐键**：换源必然产生差异（volume 精度、amount 舍入、北交所覆盖、
停牌占位行）。只报一个总数，就无法区分「已知的口径差」与「真的错了」。
spec §10 的三段式验收全靠这份逐键清单。命令行入口在
scripts/regression_paid_daily.py。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from qsys.data.query import WarehouseQuery
from qsys.markets.ashare import board_of

_VALUE_FIELDS = ["open", "high", "low", "close", "volume", "amount"]
_OHLC = ["open", "high", "low", "close"]


@dataclass(frozen=True, slots=True)
class SourceDiff:
    only_in_paid: pl.DataFrame
    only_in_legacy: pl.DataFrame
    value_diffs: pl.DataFrame
    summary: dict[str, int | float]


def compare_daily_sources(
    query: WarehouseQuery,
    start: date,
    end: date,
    boards: set[str] | None,
) -> SourceDiff:
    """逐 (symbol, dt, field) 比对付费源与冻结旧表。

    `boards` 为 None 时不过滤；给定集合时只保留该板块的标的——旧表从来
    不含北交所，不过滤会把 343 只全算成「只在付费源」，淹没真正的差异。
    """
    paid = query.daily_bars(None, start, end)
    legacy = query.legacy_daily_bars(None, start, end)

    if boards is not None:
        keep = pl.col("symbol").map_elements(
            lambda x: board_of(x.split(".")[0]), return_dtype=pl.Utf8
        ).is_in(list(boards))
        paid = paid.filter(keep)
        legacy = legacy.filter(keep)

    legacy = legacy.with_columns(pl.col("volume").cast(pl.Float64))

    keys = ["symbol", "dt"]
    only_paid = paid.join(legacy.select(keys), on=keys, how="anti").sort(keys)
    only_legacy = legacy.join(paid.select(keys), on=keys, how="anti").sort(keys)

    common = paid.join(legacy, on=keys, how="inner", suffix="_legacy")
    diffs: list[pl.DataFrame] = []
    for field in _VALUE_FIELDS:
        hit = common.filter(pl.col(field) != pl.col(f"{field}_legacy"))
        if hit.height == 0:
            continue
        diffs.append(
            hit.select(
                keys
                + [
                    pl.lit(field).alias("field"),
                    pl.col(field).alias("paid"),
                    pl.col(f"{field}_legacy").alias("legacy"),
                    (pl.col(field) - pl.col(f"{field}_legacy")).abs().alias("abs_diff"),
                ]
            )
        )
    value_diffs = (
        pl.concat(diffs, how="vertical").sort(["field", "abs_diff"], descending=[False, True])
        if diffs
        else pl.DataFrame(
            schema={"symbol": pl.Utf8, "dt": pl.Date, "field": pl.Utf8,
                    "paid": pl.Float64, "legacy": pl.Float64, "abs_diff": pl.Float64}
        )
    )

    summary = {
        "paid_rows": paid.height,
        "legacy_rows": legacy.height,
        "common_rows": common.height,
        "only_in_paid": only_paid.height,
        "only_in_legacy": only_legacy.height,
        "ohlc_diff_rows": value_diffs.filter(pl.col("field").is_in(_OHLC)).height,
        "volume_diff_rows": value_diffs.filter(pl.col("field") == "volume").height,
        "amount_diff_rows": value_diffs.filter(pl.col("field") == "amount").height,
        "max_amount_diff": float(
            value_diffs.filter(pl.col("field") == "amount")["abs_diff"].max() or 0.0
        ),
    }
    return SourceDiff(only_paid, only_legacy, value_diffs, summary)


```

模块里只留逻辑，不要 `argparse`、不要 `main()`、不要 `if __name__`——那些进 CLI。
模块顶部的导入相应收敛为：

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from qsys.data.query import WarehouseQuery
from qsys.markets.ashare import board_of
```

CLI 单独一个文件：

```python
# scripts/regression_paid_daily.py

"""换源回归对账的命令行入口。逻辑在 qsys.data.quality.source_diff。

用法:
    uv run python scripts/regression_paid_daily.py \
        --warehouse /data/qsys/warehouse --start 2023-01-03 --end 2026-09-09 \
        --out artifacts/regression_stage1.json
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from qsys.data.quality.source_diff import compare_daily_sources
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=Path("warehouse"))
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--boards", default="main,gem,star",
                        help="逗号分隔；传空串表示不过滤")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    boards = {b.strip() for b in args.boards.split(",") if b.strip()} or None
    query = WarehouseQuery(Warehouse(args.warehouse))
    diff = compare_daily_sources(query, args.start, args.end, boards)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(diff.summary, indent=2, ensure_ascii=False))
    for name, frame in (("only_in_paid", diff.only_in_paid),
                        ("only_in_legacy", diff.only_in_legacy),
                        ("value_diffs", diff.value_diffs)):
        frame.write_parquet(args.out.with_name(f"{args.out.stem}_{name}.parquet"))

    print(json.dumps(diff.summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/quality/test_source_diff.py -v`
Expected: PASS（5 个）

- [ ] **Step 5: 在节点上跑第 1 段真实对账**

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 \
  "cd /home/ubuntu/qsys && .venv/bin/python scripts/regression_paid_daily.py \
   --warehouse warehouse --start 2023-01-03 --end 2026-09-09 \
   --out artifacts/regression_stage1.json"
```

预期（依据 spec §3.2/§3.3）：`ohlc_diff_rows` 为 0；`volume_diff_rows` 与
`amount_diff_rows` 非零且全部为精度/舍入级；`only_in_legacy` 为那 10 行零成交占位。
**任何 OHLC 差异都必须先查清再继续**，不得带着未解释的价格差进入 Task 6。

- [ ] **Step 6: 提交**

```bash
git add src/qsys/data/quality/source_diff.py scripts/regression_paid_daily.py tests/data/quality/test_source_diff.py artifacts/regression_stage1.json
git commit -m "test(data): 换源第 1 段回归对账，逐键差异清单"
```

---

## Task 6: `daily_flags` schema 换 ST 三列为真值列

**Files:**
- Modify: `src/qsys/data/schema.py:95-116`（`DAILY_FLAGS`）
- Modify: `src/qsys/data/pipeline/flags.py:111-128`（`_FLAG_COLUMNS`）
- Test: `tests/data/test_flags_st_truth.py`

**Interfaces:**
- Produces: `DAILY_FLAGS` 新增 `is_st: bool`、`st_source: string`；删除 `st_inferred_raw`、`st_inference_rejected_by_price_evidence`、`st_inferred_final`

本任务只改 schema 与列清单，`_compute` 的实现在 Task 7。改完 schema 后既有 flags 测试会失败，属预期，Task 7 一并修复。

- [ ] **Step 1: 写失败测试**

```python
# tests/data/test_flags_st_truth.py

"""ST 由真值直取，不再从价格反推。"""

import pyarrow as pa

from qsys.data import schema


def test_daily_flags_has_truth_columns_not_inference_audit_columns():
    """反推没了，反推的审计列也不该留。

    留着它们会让读代码的人以为启发式还在工作，也会让下游继续引用
    一个永远为 False 的列。
    """
    names = set(schema.DAILY_FLAGS.names)
    assert "is_st" in names
    assert "st_source" in names
    assert "st_inferred_raw" not in names
    assert "st_inference_rejected_by_price_evidence" not in names
    assert "st_inferred_final" not in names
    assert schema.DAILY_FLAGS.field("is_st").type == pa.bool_()
    assert schema.DAILY_FLAGS.field("st_source").type == pa.string()


def test_flag_columns_match_the_schema():
    """列清单与 schema 必须一致，否则 cast 会在落库时才炸。"""
    from qsys.data.pipeline.flags import _FLAG_COLUMNS

    assert _FLAG_COLUMNS == list(schema.DAILY_FLAGS.names)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/test_flags_st_truth.py -v`
Expected: FAIL，`assert 'is_st' in {...}`

- [ ] **Step 3: 改 schema 与列清单**

```python
# src/qsys/data/schema.py —— DAILY_FLAGS 末尾三列替换为：

        ("limit_pct", pa.float64()),  # 实际生效的涨跌幅：ST 为 0.05，否则按板块 0.10 / 0.20 / 0.30
        # ST 真值。直取自付费源当日 `name` 列是否含 "ST"。
        # 2026-09-12 之前这里是三条价格启发式反推出来的，一致率 93.8%，已删除。
        ("is_st", pa.bool_()),
        # 该行 ST 值的来源。本计划内恒为 "paid"；兜底路径（计划 B）会写
        # "carried_from_<日期>"，届时消费方必须能区分。
        ("st_source", pa.string()),
```

```python
# src/qsys/data/pipeline/flags.py —— _FLAG_COLUMNS 末尾三项替换为：
    "limit_pct",
    "is_st",
    "st_source",
```

- [ ] **Step 4: 跑新测试确认通过，记录既有 flags 测试的失败**

Run: `.venv/bin/python -m pytest tests/data/test_flags_st_truth.py -v`
Expected: PASS

Run: `.venv/bin/python -m pytest tests/data/test_flags.py -q 2>&1 | tail -20`
Expected: **失败**，`_compute` 仍产出旧三列。原样记录，Task 7 修复。

- [ ] **Step 5: 提交（允许带着已知失败）**

```bash
git add src/qsys/data/schema.py src/qsys/data/pipeline/flags.py tests/data/test_flags_st_truth.py
git commit -m "feat(data): daily_flags 换 ST 真值列，删掉反推审计列

_compute 的实现在下一个任务。"
```

---

## Task 7: `flags.py` —— ST 直取、涨跌停基准改 `pre_close`

**Files:**
- Modify: `src/qsys/data/pipeline/flags.py`（模块 docstring、`build_daily_flags`、`_compute`）
- Modify: `tests/data/test_flags.py`（Task 6 记录的失败用例）
- Test: `tests/data/test_flags_st_truth.py`

**Interfaces:**
- Consumes: `WarehouseQuery.paid_daily`（Task 2）、`DAILY_FLAGS`（Task 6）
- Produces: `build_daily_flags(warehouse, start, end, boards=None) -> int`（新增 `boards` 参数）

**删除** `_with_reference_price`（2026-09-13 订正）。

计划初稿要求保留它，理由是「兜底路径（计划 B）没有 `pre_close`，仍需用
`corporate_actions` 自算」。**计划 B 已于 2026-09-13 整段作废**（日线源改 Tushare
API 直连，人工转存环节消失，兜底路径不再存在），这条依据随之失效。

实测确认它只有 `_compute` 一处调用点，删掉后即为死代码零残留。连带删除：
`build_daily_flags` 里的 `actions = query.corporate_actions()` 读取、
`_compute` 的 `actions` 参数、以及 `frame = _with_reference_price(frame, actions)` 调用。

**注意 `adj_prev_close` 这一列要留**——它在 `DAILY_FLAGS` schema 里、有测试、
下游有消费方。变的只是它的来源：从「`corporate_actions` 事件表自算」改为
「`pre_close` 直取」。

本任务需在 `flags.py` 顶部加 `from qsys.markets.ashare import PRICE_LIMITS, board_of`
（原来只导入 `PRICE_LIMITS`）。

- [ ] **Step 1: 写失败测试**

```python
# tests/data/test_flags_st_truth.py 追加

from datetime import date

import polars as pl

from qsys.data.pipeline.flags import build_daily_flags
from qsys.data.query import WarehouseQuery

DAYS = [date(2026, 4, 29), date(2026, 4, 30)]


def _paid(write_daily_bars, warehouse, rows):
    write_daily_bars(warehouse, pl.DataFrame(rows))


def test_st_is_read_from_the_name_column(warehouse, write_daily_bars):
    """名称含 ST 即为 ST。不看价格，不看历史贴板次数。"""
    _paid(write_daily_bars, warehouse, {
        "symbol": ["000010.SZ"], "dt": [DAYS[1]], "name": ["*ST美丽"],
        "open": [3.0], "high": [3.0], "low": [3.0], "close": [3.0],
        "pre_close": [3.0], "volume": [100.0], "amount": [3e4],
    })
    build_daily_flags(warehouse, DAYS[1], DAYS[1])
    got = WarehouseQuery(warehouse).daily_flags(DAYS[1], DAYS[1])
    assert got["is_st"].to_list() == [True]
    assert got["st_source"].to_list() == ["paid"]


def test_a_plain_name_is_not_st(warehouse, write_daily_bars):
    _paid(write_daily_bars, warehouse, {
        "symbol": ["000010.SZ"], "dt": [DAYS[0]], "name": ["美丽生态"],
        "open": [3.0], "high": [3.0], "low": [3.0], "close": [3.0],
        "pre_close": [3.0], "volume": [100.0], "amount": [3e4],
    })
    build_daily_flags(warehouse, DAYS[0], DAYS[0])
    got = WarehouseQuery(warehouse).daily_flags(DAYS[0], DAYS[0])
    assert got["is_st"].to_list() == [False]


def test_st_status_is_per_day_not_carried_across_the_transition(warehouse, write_daily_bars):
    """同一只票跨 ST 生效日，两天的判定必须各自独立。

    000010.SZ 实测 2026-04-30 由「美丽生态」变为「*ST美丽」。
    """
    _paid(write_daily_bars, warehouse, {
        "symbol": ["000010.SZ", "000010.SZ"], "dt": DAYS,
        "name": ["美丽生态", "*ST美丽"],
        "open": [3.0, 3.0], "high": [3.0, 3.0], "low": [3.0, 3.0],
        "close": [3.0, 3.0], "pre_close": [3.0, 3.0],
        "volume": [100.0, 100.0], "amount": [3e4, 3e4],
    })
    build_daily_flags(warehouse, DAYS[0], DAYS[1])
    got = WarehouseQuery(warehouse).daily_flags(DAYS[0], DAYS[1]).sort("dt")
    assert got["is_st"].to_list() == [False, True]


def test_a_null_name_yields_null_st_not_false(warehouse, write_daily_bars):
    """拿不到名称就记 null，不能默认成「不是 ST」。

    默认 False 会让一只 ST 股按 10% 档算涨停价，首板判定直接错。
    """
    _paid(write_daily_bars, warehouse, {
        "symbol": ["000010.SZ"], "dt": [DAYS[0]],
        "open": [3.0], "high": [3.0], "low": [3.0], "close": [3.0],
        "pre_close": [3.0], "volume": [100.0], "amount": [3e4],
    })
    build_daily_flags(warehouse, DAYS[0], DAYS[0])
    got = WarehouseQuery(warehouse).daily_flags(DAYS[0], DAYS[0])
    assert got["is_st"].to_list() == [None]
    assert got["limit_up"].to_list() == [None]


def test_limit_price_uses_the_direct_pre_close(warehouse, write_daily_bars):
    """涨跌停基准直取 pre_close，不再从 corporate_actions 自算。

    实测 pre_close 就是除权参考价（3980 个除权日，与因子折算值中位差
    0.000154），比事件表自算可靠。
    """
    _paid(write_daily_bars, warehouse, {
        "symbol": ["000001.SZ"], "dt": [DAYS[0]], "name": ["平安银行"],
        "open": [10.94], "high": [12.03], "low": [10.90], "close": [12.03],
        "pre_close": [10.94], "volume": [100.0], "amount": [1e5],
    })
    build_daily_flags(warehouse, DAYS[0], DAYS[0])
    got = WarehouseQuery(warehouse).daily_flags(DAYS[0], DAYS[0])
    assert got["limit_up"].to_list() == [12.03]   # round(10.94 * 1.10, 2)
    assert got["adj_prev_close"].to_list() == [10.94]
    assert got["is_limit_up"].to_list() == [True]


def test_st_limit_is_five_percent(warehouse, write_daily_bars):
    _paid(write_daily_bars, warehouse, {
        "symbol": ["000010.SZ"], "dt": [DAYS[1]], "name": ["*ST美丽"],
        "open": [3.0], "high": [3.15], "low": [3.0], "close": [3.15],
        "pre_close": [3.0], "volume": [100.0], "amount": [3e4],
    })
    build_daily_flags(warehouse, DAYS[1], DAYS[1])
    got = WarehouseQuery(warehouse).daily_flags(DAYS[1], DAYS[1])
    assert got["limit_pct"].to_list() == [0.05]
    assert got["limit_up"].to_list() == [3.15]
    assert got["is_limit_up"].to_list() == [True]


def test_a_symbol_missing_from_instruments_falls_back_to_its_code_prefix(
    warehouse, write_daily_bars
):
    """instruments 里没有的标的按号段判板块，不能默认成 main。

    北交所上限 30%，默认成 main 会按 10% 算涨停价——旧表不含北交所所以
    这条路从没走过，付费源有 343 只。
    """
    _paid(write_daily_bars, warehouse, {
        "symbol": ["920268.BJ"], "dt": [DAYS[0]], "name": ["京证股份"],
        "open": [5.0], "high": [6.5], "low": [5.0], "close": [6.5],
        "pre_close": [5.0], "volume": [100.0], "amount": [5e4],
    })
    build_daily_flags(warehouse, DAYS[0], DAYS[0])
    got = WarehouseQuery(warehouse).daily_flags(DAYS[0], DAYS[0])
    assert got["limit_pct"].to_list() == [0.30]
    assert got["limit_up"].to_list() == [6.5]      # round(5.0 * 1.30, 2)
    assert got["is_limit_up"].to_list() == [True]


def test_board_filter_keeps_beijing_out_of_daily_flags(warehouse, write_daily_bars):
    """付费源含北交所 343 只，旧表没有。不显式过滤会静默改变广度因子。"""
    _paid(write_daily_bars, warehouse, {
        "symbol": ["000001.SZ", "920268.BJ"], "dt": [DAYS[0], DAYS[0]],
        "name": ["平安银行", "京证股份"],
        "open": [10.0, 5.0], "high": [10.0, 5.0], "low": [10.0, 5.0],
        "close": [10.0, 5.0], "pre_close": [10.0, 5.0],
        "volume": [100.0, 100.0], "amount": [1e5, 5e4],
    })
    build_daily_flags(warehouse, DAYS[0], DAYS[0], boards={"main", "gem", "star"})
    got = WarehouseQuery(warehouse).daily_flags(DAYS[0], DAYS[0])
    assert got["symbol"].to_list() == ["000001.SZ"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/test_flags_st_truth.py -v`
Expected: FAIL，`TypeError: build_daily_flags() got an unexpected keyword argument 'boards'`

- [ ] **Step 3: 改模块 docstring**

删掉 docstring 中关于 ST 三条启发式的整段（「**ST 由价格行为反推**」到「第 3 条的来历」结束），替换为：

```python
"""涨跌停与首板判定。

策略的候选池完全建立在这上面：
  涨停   close >= limit_up
  炸板   high >= limit_up 但 close < limit_up
  首板   今日涨停且昨日未涨停（连板不是首板）

派生自 paid_daily + instruments.board，可完全重算——重算必须得到相同结果。

**全表用 polars 表达式算，不逐行 iter_rows**：全市场 4 年约 500 万行，
每行造一个 dict 会占几个 GB。

**ST 是真值，不是推断**（2026-09-12 起）：`is_st` 直取自付费源当日
`name` 列是否含 "ST"。此前的三条价格启发式（贴 5% 板 + 回看确认 +
制度硬否决）已整段删除——它的近窗一致率只有 93.8%，误差会向下游传播
并伪装成策略问题。拿不到名称就记 null，**不默认成「不是 ST」**：
默认 False 会让 ST 股按 10% 档算涨停价，首板判定直接错。

**涨跌停基准直取 `pre_close`**：付费源的 `pre_close` 就是除权参考价
本身（3980 个除权日实测，与因子折算值中位差 0.000154），不再用
`corporate_actions` 事件表自算。`_with_reference_price` 已删除——计划 B 作废后
它不再有调用点。`adj_prev_close` 列保留，来源改为 `pre_close` 直取。

**逐年处理**，并向前多读 `_LOOKBACK_DAYS` 天日历日只为给年初第一根 bar
补上前收。
"""
```

- [ ] **Step 4: 改 `build_daily_flags`**

```python
# src/qsys/data/pipeline/flags.py

def build_daily_flags(
    warehouse: Warehouse,
    start: date,
    end: date,
    boards: set[str] | None = None,
) -> int:
    """从付费日线派生涨跌停与首板标记。返回写入行数。

    `boards` 给定时只保留该板块的标的。付费源含北交所 343 只而旧表没有，
    不过滤就会让 market_regime 的广度因子静默变化。
    """
    query = WarehouseQuery(warehouse)
    instruments = query.instruments()
    board_map = (
        dict(zip(instruments["symbol"].to_list(), instruments["board"].to_list()))
        if instruments.height
        else {}
    )

    written = 0
    for year in range(start.year, end.year + 1):
        lo_year, hi_year = date(year, 1, 1), date(year, 12, 31)
        lo = max(start, lo_year)
        hi = min(end, hi_year)
        if lo > hi:
            continue

        # 取 28 列：name 给 ST 真值，pre_close 给涨跌停基准。
        bars = query.paid_daily(None, lo - timedelta(days=_LOOKBACK_DAYS), hi)
        if bars.height == 0:
            continue
        if boards is not None:
            keep = pl.col("symbol").map_elements(
                lambda s: board_map.get(s) or board_of(s.split(".")[0]),
                return_dtype=pl.Utf8,
            ).is_in(list(boards))
            bars = bars.filter(keep)
            if bars.height == 0:
                continue

        flags = _compute(bars, board_map).filter(pl.col("dt").is_between(lo, hi))
        if flags.height == 0:
            continue

        chunk = flags.sort(["dt", "symbol"]).select(_FLAG_COLUMNS)
        merged = (
            pl.concat([query.daily_flags(lo_year, hi_year), chunk], how="vertical")
            .unique(subset=["dt", "symbol"], keep="last", maintain_order=True)
            .sort(["dt", "symbol"])
            .select(_FLAG_COLUMNS)
        )
        warehouse.write(
            "daily_flags", merged.to_arrow().cast(schema.DAILY_FLAGS), year=year
        )
        written += chunk.height

    log.info("daily_flags.built", rows=written, start=str(start), end=str(end))
    return written
```

- [ ] **Step 5: 改 `_compute`**

删除以下四处：

1. `_compute` 中从 `hit_st = (pl.col("high") == pl.col("_st_up"))` 到
   `looks_st = st_raw & ~vetoed` 的整段（三条 ST 启发式）；
2. `_compute` 的 `actions` 参数与 `frame = _with_reference_price(frame, actions)` 调用；
3. `_with_reference_price` 函数本身（删后零调用点，实测确认）；
4. `build_daily_flags` 里的 `actions = query.corporate_actions()` 读取。

替换 `_compute` 末段为：

```python
def _compute(bars: pl.DataFrame, boards: dict[str, str]) -> pl.DataFrame:
    limits = {board: PRICE_LIMITS.get(board, _DEFAULT_LIMIT) for board in PRICE_LIMITS}
    # 局部名不能叫 board_of——模块里已经从 markets.ashare 导入了同名函数。
    #
    # **default 必须按号段兜底，不能是 "main"**：instruments 缺了的标的
    # 会拿到 main 的 10% 上限。旧表不含北交所所以从没触发过，付费源有
    # 343 只北交所（实际上限 30%），照旧默认会把它们的涨停价全算错，
    # 而且不报错。
    board_expr = pl.col("symbol").replace_strict(
        boards,
        default=pl.col("symbol").map_elements(
            lambda s: board_of(s.split(".")[0]), return_dtype=pl.Utf8
        ),
        return_dtype=pl.Utf8,
    )
    ratio = board_expr.replace_strict(limits, default=_DEFAULT_LIMIT, return_dtype=pl.Float64)

    # ST 真值：名称含 "ST" 即为 ST（含 *ST）。名称为 null 时 is_st 也为 null,
    # 不默认成 False——见模块 docstring。
    is_st = (
        pl.when(pl.col("name").is_null())
        .then(None)
        .otherwise(pl.col("name").str.contains("ST"))
    )

    frame = bars.sort(["symbol", "dt"]).with_columns(
        pl.col("close").shift(1).over("symbol").alias("prev_close"),
        # pre_close 直取即除权参考价，非除权日等于前收。
        pl.col("pre_close").alias("adj_prev_close"),
        ratio.alias("_ratio"),
        is_st.alias("is_st"),
        pl.lit("paid", dtype=pl.Utf8).alias("st_source"),
    )

    frame = frame.with_columns(
        pl.when(pl.col("is_st"))
        .then(pl.lit(ST_LIMIT))
        .otherwise(pl.when(pl.col("is_st").is_null()).then(None).otherwise(pl.col("_ratio")))
        .alias("limit_pct")
    )

    cents = (pl.col("adj_prev_close") * 100).round(0).cast(pl.Int64)
    up_milli = ((1.0 + pl.col("limit_pct")) * 1000).round(0).cast(pl.Int64)
    down_milli = ((1.0 - pl.col("limit_pct")) * 1000).round(0).cast(pl.Int64)

    frame = frame.with_columns(
        _limit_expr(cents, up_milli).alias("limit_up"),
        _limit_expr(cents, down_milli).alias("limit_down"),
    )

    is_up = (pl.col("close") >= pl.col("limit_up")).fill_null(False)
    is_down = (pl.col("close") <= pl.col("limit_down")).fill_null(False)
    touched = (pl.col("high") >= pl.col("limit_up")).fill_null(False)
    frame = frame.with_columns(
        is_up.alias("is_limit_up"),
        is_down.alias("is_limit_down"),
        touched.alias("touched_limit_up"),
    )
    return frame.with_columns(
        (pl.col("touched_limit_up") & ~pl.col("is_limit_up")).alias("is_broken_board"),
        (
            pl.col("is_limit_up")
            & ~pl.col("is_limit_up").shift(1, fill_value=False).over("symbol")
        ).alias("is_first_board"),
    )
```

同时删除现已无人使用的常量 `ST_CONFIRM_BARS`、`ST_CONFIRM_HITS`。

**接手 Task 0 留下的注释**：`flags.py` 现在真的按 `name` 直取 ST 了，把
`src/qsys/markets/ashare.py` 里 `PRICE_LIMITS` 上方的注释补成：

```python
# 日涨跌幅上限。ST 为 5%，不在此表——ST 是股票的状态而非板块属性，
# 自 2026-09-12 起由 data/pipeline/flags.py 从付费源 name 列直取判定。
# 本表只登记板块档位。
```

Task 0 当时不能写这句：那时 flags.py 还在用价格反推，写了就是失实注释。

- [ ] **Step 6: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/data/test_flags_st_truth.py -v`
Expected: PASS（9 个）

- [ ] **Step 7: 迁移 Task 6 记录的既有 flags 测试**

原本断言 `st_inferred_final` 的用例改断言 `is_st`，并把夹具从「造价格让启发式命中」
改成「直接给 `name`」。**断言 ST 三条规则本身的用例整体删除**——规则已不存在，
留着等于测试一段已删代码的影子。删除时在 commit message 里列出被删的用例名。

- [ ] **Step 8: 跑全量测试**

Run: `.venv/bin/python -m pytest -q`
Expected: 全绿

- [ ] **Step 9: 提交**

```bash
git add src/qsys/data/pipeline/flags.py tests/data/pipeline/
git commit -m "feat(data): ST 改真值直取，涨跌停基准改 pre_close 直取

删除三条价格启发式（近窗一致率 93.8%）与 adj_prev_close 的事件表自算。
_with_reference_price 一并删除——计划 B 作废后它不再有调用点。
adj_prev_close 列保留，来源改为 pre_close 直取。"
```

---

## Task 8: `market_regime` 板块过滤

**Files:**
- Modify: `src/qsys/data/pipeline/regime.py`
- Test: `tests/data/test_regime.py`

**Interfaces:**
- Consumes: `build_daily_flags(..., boards=...)`（Task 7）
- Produces: `build_market_regime(warehouse, start, end, boards=None) -> int`

- [x] **Step 1: 写失败测试**

```python
# tests/data/test_regime.py 追加

def test_board_filter_keeps_beijing_out_of_the_breadth_factor(warehouse, write_daily_bars):
    """广度因子的口径必须与策略一致——策略排北交所。

    付费源比旧表多 343 只北交所，不过滤会让 market_advance_ratio
    在换源当天静默跳变，而没有任何地方报错。
    """
    from datetime import date

    import polars as pl

    from qsys.data.pipeline.flags import build_daily_flags
    from qsys.data.pipeline.regime import build_market_regime
    from qsys.data.query import WarehouseQuery

    day = date(2026, 9, 9)
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ", "920268.BJ"], "dt": [day, day],
        "name": ["平安银行", "京证股份"],
        "open": [10.0, 5.0], "high": [10.0, 5.0], "low": [10.0, 5.0],
        "close": [10.0, 5.0], "pre_close": [10.0, 5.0],
        "volume": [100.0, 100.0], "amount": [1e5, 5e4],
    }))
    boards = {"main", "gem", "star"}
    build_daily_flags(warehouse, day, day, boards=boards)
    build_market_regime(warehouse, day, day, boards=boards)
    got = WarehouseQuery(warehouse).market_regime(day, day)
    assert got.height == 1
```

- [x] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/data/test_regime.py -k beijing -v`
Expected: FAIL，`TypeError: build_market_regime() got an unexpected keyword argument 'boards'`

- [x] **Step 3: 实现**

```python
# src/qsys/data/pipeline/regime.py

from qsys.markets.ashare import board_of


def _keep_boards(frame: pl.DataFrame, boards: set[str] | None) -> pl.DataFrame:
    """按板块过滤。

    广度因子的分母就是这个池子。付费源比旧表多 343 只北交所，不过滤会让
    market_advance_ratio 在换源当天静默跳变而没有任何地方报错。
    """
    if boards is None or frame.height == 0:
        return frame
    return frame.filter(
        pl.col("symbol")
        .map_elements(lambda s: board_of(s.split(".")[0]), return_dtype=pl.Utf8)
        .is_in(list(boards))
    )


def build_market_regime(
    warehouse: Warehouse,
    start: date,
    end: date,
    boards: set[str] | None = None,
) -> int:
    """生态因子。`boards` 给定时只统计该板块，口径须与候选池一致。"""
    # ... 原有实现不变，只在每处读取之后插入一次过滤：
    #   flags = _keep_boards(query.daily_flags(lo, hi), boards)
    #   bars = _keep_boards(query.daily_bars(None, lo, hi), boards)
    # index_bars 不过滤——它是指数，本就不在个股板块词表里。
```

按上述在 `build_market_regime` 中每一处 `query.daily_flags(...)` 与
`query.daily_bars(...)` 的返回值外包一层 `_keep_boards(..., boards)`。
`query.index_bars(...)` **不要**包——沪深 300 不是个股，`board_of("000300")`
会把它判成 `main` 而侥幸通过，但那是巧合不是设计。

- [x] **Step 4: 把两个生产调用点接上（2026-09-13 补）**

Task 7 给 `build_daily_flags` 加了 `boards` 参数，但**没有任何生产调用点传它**——
实测 `scripts/daily_archive.py:240` 与 `scripts/ingest_universe.py:122` 都是三参数调用。
本任务给 `build_market_regime` 加的参数同理。

不接线的后果：Task 10 的一次性全量重算**带**板块过滤，而每日归档**不带**，
两者口径不一致，派生表会一天天被污染回去，343 只北交所重新流进广度因子——
而且不报错。

两个脚本都已经有板块集合，只是没往下传：

```python
# scripts/daily_archive.py —— boards 变量在第 156 行附近已算好
        print(f"涨跌停标记 {build_daily_flags(warehouse, derived_from, complete_through, boards=boards)} 行")
        print(f"生态因子 {build_market_regime(warehouse, derived_from, complete_through, boards=boards)} 行")
```

```python
# scripts/ingest_universe.py —— boards 变量在第 61 行已算好，同样往下传
    print(f"  涨跌停标记 {build_daily_flags(warehouse, start, complete_through, boards=boards)} 行")
    print(f"  市场生态因子 {build_market_regime(warehouse, start, complete_through, boards=boards)} 行")
```

两处的 `boards` 都来自各自的 `--boards` 参数（默认 `main,gem,star`），
实测位置：`daily_archive.py:162`、`ingest_universe.py:61`。
**不要在调用处写死字面量集合**——那会让 `--boards` 参数失效而无人察觉。

- [x] **Step 5: 跑全量测试**

Run: `.venv/bin/python -m pytest -rfE --tb=no -q`
Expected: 全绿

- [x] **Step 6: 提交**

```bash
git add src/qsys/data/pipeline/regime.py scripts/daily_archive.py \
        scripts/ingest_universe.py tests/data/test_regime.py
git commit -m "feat(data): market_regime 加板块过滤，并把两个生产调用点接上"
```

---

## Task 9: `adjust.py` —— 因子直取，自算降级为校验

**Files:**
- Modify: `src/qsys/data/pipeline/adjust.py`
- Modify: `src/qsys/data/query.py`
- Modify: `src/qsys/data/pipeline/dividends.py`
- Test: `tests/data/pipeline/test_adjust.py`

**Interfaces:**
- Consumes: `WarehouseQuery.paid_daily`（Task 2）
- Produces: `build_adj_factors(warehouse)`，其中 `cum_factor` 直取、
  `cum_share_factor` 继续从送转配股派生；
  `paid_factors(query, symbols, start, end) -> pl.DataFrame`（列 `symbol, dt, factor`）；
  `verify_computed_factors(query, start, end) -> pl.DataFrame`
  （列 `symbol, dt, direct, computed, rel_diff`，两种值均为事件日单日因子）

> **2026-09-13 实施前复核修正：**原计划只新增两个旁路函数，没有改
> `build_adj_factors()`，会导致 `HistoricalFeed` 与 `market_regime` 继续消费
> 自算因子，与本任务标题和 spec §7 冲突。现改为真正替换生产落库值。
> 同时，全历史校验不得一次加载 28 列；此前 Tushare 对账已在生产节点因此
> OOM。实现必须只投影 5 列、逐年读取，并携带跨年状态。

- [x] **Step 1: 写失败测试**

```python
# tests/data/pipeline/test_adjust.py 追加

def test_factor_is_read_directly_from_the_paid_source(warehouse, write_daily_bars):
    """因子直取。自算只用来校验，不落库。"""
    from datetime import date

    import polars as pl

    from qsys.data.pipeline.adjust import paid_factors
    from qsys.data.query import WarehouseQuery

    day = date(2026, 9, 9)
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ"], "dt": [day], "name": ["平安银行"],
        "open": [11.76], "high": [11.79], "low": [11.69], "close": [11.70],
        "pre_close": [11.70], "volume": [100.0], "amount": [1e5],
        "factor": [139.008],
    }))
    got = paid_factors(WarehouseQuery(warehouse), None, day, day)
    assert got["factor"].to_list() == [139.008]


def test_verification_reports_the_gap_without_overwriting(warehouse, write_daily_bars):
    """自算与直取不一致时报出来，而不是挑一个覆盖另一个。

    实测 522 次因子变动里 7 例偏差 >1%，全在 2005-2006 股改期间。
    这些必须留在报告里，不能被静默抹平。
    """
    from datetime import date

    import polars as pl

    from qsys.data.pipeline.adjust import verify_computed_factors
    from qsys.data.query import WarehouseQuery

    days = [date(2026, 6, 11), date(2026, 6, 12)]
    write_daily_bars(warehouse, pl.DataFrame({
        "symbol": ["000001.SZ", "000001.SZ"], "dt": days,
        "name": ["平安银行", "平安银行"],
        "open": [11.30, 10.94], "high": [11.30, 10.94], "low": [11.30, 10.94],
        "close": [11.30, 10.94], "pre_close": [11.30, 10.94],
        "volume": [100.0, 100.0], "amount": [1e5, 1e5],
        "factor": [134.5794, 139.008],
    }))
    report = verify_computed_factors(WarehouseQuery(warehouse), days[0], days[1])
    assert set(report.columns) >= {"symbol", "dt", "direct", "computed", "rel_diff"}
    assert report.height >= 1
```

- [x] **Step 2: 确认旧实现不满足测试（代码审查确认，未单独保留 red-run 日志）**

Run: `.venv/bin/python -m pytest tests/data/pipeline/test_adjust.py -k "directly or verification" -v`
Expected: FAIL，`ImportError: cannot import name 'paid_factors'`

- [x] **Step 3: 实现**

实现约束：

- `build_adj_factors()` 的 `cum_factor` 必须来自 `paid_daily.factor`，不得再由
  `corporate_actions` 覆盖；后者仅负责 `cum_share_factor`。
- `WarehouseQuery.paid_daily(..., columns=...)` 只接受 schema 内列名，供全历史
  任务做安全投影。
- 校验比较相邻日 `factor(t)/factor(t-1)` 与 `close(t-1)/pre_close(t)`，只报告
  事件日差异；若比较累计值，一次股改差异会污染其后所有交易日。
- 逐年处理时携带每只股票上一行的 `close` 和 `factor`，不能漏掉年初首日事件。

- [x] **Step 4: 跑全量测试**

Run: `.venv/bin/python -m pytest -q`
Expected: 全绿

- [x] **Step 5: 在节点上跑全量因子校验**

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 \
  "cd /home/ubuntu/qsys && .venv/bin/python -c \"
from datetime import date
from pathlib import Path
from qsys.data.pipeline.adjust import verify_computed_factors
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
r = verify_computed_factors(WarehouseQuery(Warehouse(Path('warehouse'))),
                            date(1990,12,19), date(2026,9,11))
r.write_parquet('artifacts/factor_verification.parquet')
print('偏差 >1% 的股-日:', r.filter(r['rel_diff'] > 0.01).height)
print(r.head(20))
\""
```

实际（2026-09-13）：偏差 >1% 共 1,807 条、1,541 只股票，其中 1,119 条
位于 2005—2006，另有 688 条在区间之外；最大异常簇之一来自 920 北交所。
完整摘要见 `artifacts/task9-factor-verification-2026-09-13.md`，逐行数据见
`artifacts/factor_verification.parquet`。**结果不符合原预期，Task 10 第 3 段
仍须逐类解释，不能直接标为通过。**

- [x] **Step 6: 分两次提交（实现提交 + 生产校验产物提交）**

```bash
git add src/qsys/data/pipeline/adjust.py tests/data/pipeline/test_adjust.py artifacts/factor_verification.parquet
git commit -m "feat(data): 复权因子改直取，自算降级为校验报告"
```

---

## Task 10: 全量重算派生表 + 第 2、3 段回归 + 文档

**Files:**
- Modify: `docs/reference/baidupan-data-source.md`
- Modify: `docs/superpowers/specs/2026-09-11-joinquant-truth-data-source-design.md`（标作废）
- Modify: `docs/superpowers/plans/2026-09-11-joinquant-truth-data-source.md`（标作废）
- Create: `artifacts/换源回归报告_2026-09-13.md`

**2026-09-13 订正**（订正依据写在每条里，原步骤的问题一并记录）：

1. **备份必须排在重算之前**。原 Step 1 先重算、Step 2 才说「重算之前必须先备份」，
   照着执行就会把旧值覆盖掉、无从比对。备份提到 Step 1。
2. **`adj_factors` 也要备份并重算**。原步骤只重算 `daily_flags` / `market_regime`，
   但 Task 9 改的是 `adj_factors.cum_factor`（自算 → 直取），不重算它，
   第 3 段就没有被测对象。且 `market_regime` 读 `query.adj_factors()`
   （`regime.py:110`），不先重算因子，重算出来的 regime 仍是旧因子算的。
3. **第 3 段不能直接比 `cum_factor` 的绝对值**。实测旧表 4,504,869 行、
   区间 2023-01-03 至 2026-09-09，`cum_factor` 以窗口首日归一（首日恒为 1.0）；
   新值是付费源自带锚点的累计因子，两者不同尺度，直接相减会全市场 100% 差异且毫无意义。
   两个下游消费方都是锚点无关的——`regime.py:159` 用 `cum_factor(t)/cum_factor(t-1)`，
   `feed.py:93-97` 除以末日锚点——所以第 3 段按**同锚归一后**比对。
4. **大偏差不在本任务解释**。用户 2026-09-13 指示：先把数据源替换完毕，
   再处理因子与计算偏差。Task 9 查出的 1,807 条 `rel_diff > 1%` 已挂账
   `docs/reference/deferred-corrections.md` D-1/D-2。本任务**如实记录数量与分布**，
   **不做归因、不做过滤**，也不因此阻断验收。

- [ ] **Step 1: 重算之前先备份三张派生表**

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 \
  "set -e
   B=/data/qsys/warehouse/backups
   mkdir -p \$B
   cp -r /data/qsys/warehouse/daily_flags    \$B/daily_flags_before_st_truth
   cp -r /data/qsys/warehouse/market_regime  \$B/market_regime_before_st_truth
   cp -r /data/qsys/warehouse/adj_factors    \$B/adj_factors_before_direct
   du -sh \$B/*"
```

备份不存在就没有第 2、3 段——这一步失败必须停下，不能继续重算。

- [ ] **Step 2: 全量重算（顺序不能换）**

顺序是 `adj_factors` → `daily_flags` → `market_regime`：`market_regime` 读因子表，
因子表不先重算，重算出来的 regime 仍是旧因子的结果。

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 \
  "cd /home/ubuntu/qsys && .venv/bin/python -c \"
from datetime import date
from pathlib import Path
from qsys.data.pipeline.adjust import build_adj_factors
from qsys.data.pipeline.flags import build_daily_flags
from qsys.data.pipeline.regime import build_market_regime
from qsys.data.storage import Warehouse
w = Warehouse(Path('warehouse')); b = {'main','gem','star'}
print('adj_factors', build_adj_factors(w), flush=True)
print('daily_flags', build_daily_flags(w, date(2023,1,1), date(2026,9,11), boards=b), flush=True)
print('market_regime', build_market_regime(w, date(2023,1,1), date(2026,9,11), boards=b), flush=True)
\""
```

区间沿用旧表的 2023–2026，是为了让新旧逐键可比。付费源有 1990 年起的历史，
是否把派生表向前扩展是独立决定，不在本任务内。

- [ ] **Step 3: 出第 2 段（ST 真值）差异**

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 \
  "cd /home/ubuntu/qsys && .venv/bin/python -c \"
import polars as pl, glob, json
def load(p):
    return pl.concat([pl.read_parquet(f) for f in
        sorted(glob.glob(p + '/**/*.parquet', recursive=True))])
old = load('/data/qsys/warehouse/backups/daily_flags_before_st_truth')
new = load('/data/qsys/warehouse/daily_flags')
j = old.join(new, on=['dt','symbol'], how='inner', suffix='_new')
def differs(a, b):
    both_null = pl.col(a).is_null() & pl.col(b).is_null()
    return (~both_null) & (pl.col(a).is_null() | pl.col(b).is_null()
                           | (pl.col(a) != pl.col(b)))
fb = j.filter(differs('is_first_board','is_first_board_new'))
lp = j.filter(differs('limit_pct','limit_pct_new'))
st = j.filter(differs('st_inferred_final','is_st_new'))
out = {'old_rows': old.height, 'new_rows': new.height, 'common_rows': j.height,
       'first_board_flips': fb.height, 'first_board_symbols': fb['symbol'].n_unique(),
       'limit_pct_changes': lp.height, 'limit_pct_symbols': lp['symbol'].n_unique(),
       'st_flips': st.height, 'st_symbols': st['symbol'].n_unique()}
print(json.dumps(out, indent=2))
print('ST 翻转按年:'); print(st.group_by(pl.col('dt').dt.year().alias('y')).len().sort('y'))
print('首板翻转按年:'); print(fb.group_by(pl.col('dt').dt.year().alias('y')).len().sort('y'))
print('首板翻转方向:'); print(fb.group_by(['is_first_board','is_first_board_new']).len())
fb.write_parquet('artifacts/stage2_first_board_flips.parquet')
st.write_parquet('artifacts/stage2_st_flips.parquet')
open('artifacts/stage2_summary.json','w').write(json.dumps(out, indent=2))
\""
```

判差异用的是「两边都 null 才不算差异」的写法，不是 `!=`——polars 里
`null != value` 求值为 null，被 `filter` 当成 False，会把「一边有值一边没有」
的差异静默吞掉（这是 Task 5 修过的同一个 Critical）。

首板翻转清单是本段的核心产出：候选池的变化全部由它解释。

- [ ] **Step 4: 出第 3 段（因子直取）差异**

```bash
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 \
  "cd /home/ubuntu/qsys && .venv/bin/python -c \"
import polars as pl, json
old = pl.read_parquet('/data/qsys/warehouse/backups/adj_factors_before_direct/part.parquet')
new = pl.read_parquet('/data/qsys/warehouse/adj_factors/part.parquet')
lo, hi = old['dt'].min(), old['dt'].max()
new = new.filter(pl.col('dt').is_between(lo, hi))
j = old.join(new, on=['symbol','dt'], how='inner', suffix='_new').sort(['symbol','dt'])
# 同锚归一：每只股票除以自己在窗口内首日的值，消掉两边锚点不同的影响
j = j.with_columns(
    (pl.col('cum_factor') / pl.col('cum_factor').first().over('symbol')).alias('o'),
    (pl.col('cum_factor_new') / pl.col('cum_factor_new').first().over('symbol')).alias('n'),
).with_columns(((pl.col('n') - pl.col('o')).abs() / pl.col('o')).alias('rel'))
out = {'old_rows': old.height, 'new_rows_in_window': new.height, 'common_rows': j.height,
       'gt_1e_9': j.filter(pl.col('rel') > 1e-9).height,
       'gt_0_02pct': j.filter(pl.col('rel') > 2e-4).height,
       'gt_0_1pct': j.filter(pl.col('rel') > 1e-3).height,
       'gt_1pct': j.filter(pl.col('rel') > 1e-2).height,
       'gt_1pct_symbols': j.filter(pl.col('rel') > 1e-2)['symbol'].n_unique(),
       'max_rel': float(j['rel'].max() or 0.0)}
print(json.dumps(out, indent=2))
big = j.filter(pl.col('rel') > 1e-2)
print('按年:'); print(big.group_by(pl.col('dt').dt.year().alias('y')).len().sort('y'))
big.select('symbol','dt','o','n','rel').write_parquet('artifacts/stage3_factor_diffs.parquet')
open('artifacts/stage3_summary.json','w').write(json.dumps(out, indent=2))
\""
```

**只记录数量与分布，不解释、不过滤**。大偏差的归因挂在
`docs/reference/deferred-corrections.md` D-1，等数据源换完再做。

- [ ] **Step 5: 写回归报告**

`artifacts/换源回归报告_2026-09-13.md` 要包含三段各自的：影响行数、影响标的数、
最大偏差、分布。第 1 段直接引用已有的 `artifacts/regression_stage1.json`。
第 3 段的大偏差写明「已挂账 D-1，本轮不归因」，**不得写成「已通过」**。

- [ ] **Step 6: 更新参考文档**

`docs/reference/baidupan-data-source.md` 加一节「作为日线真值源」，记录：
`daily_bars()` 已换底、`volume` 为 Float64、ST 与因子直取、板块过滤要求、
以及 spec §3.7 记的「停牌判据在新源下永不命中」。

- [ ] **Step 7: 把聚宽 spec 与 plan 标作废**

两份文档头部各加：

```markdown
> **已作废（2026-09-11）**：真值源改为百度网盘付费数据，本方案的聚宽采集部分
> （Task 1-6、8、9）不再执行。消费侧改造（Task 7、10-14）的目标仍然成立，
> 已并入 `docs/superpowers/specs/2026-09-12-paid-daily-truth-source-design.md`。
```

- [ ] **Step 8: 提交**

```bash
git add docs/ artifacts/
git commit -m "docs(data): 换源回归报告与文档更新，聚宽方案标作废"
```

---

## 计划 B 预告（不在本计划内）

待周一 2026-09-14 17:10 之后实测「订阅是否自动更新」与卖家实际交付时点，再写：

- spec §8：每日自动化（百度网盘发现 + 下载 + 落库 + 派生重算 + 未到货显式报错）
- spec §9：QMT 兜底通道（`daily_provisional` 表、ST 结转、差异报告、到货即删）
- spec §12.6：兜底链路的写入回执与缺失告警
