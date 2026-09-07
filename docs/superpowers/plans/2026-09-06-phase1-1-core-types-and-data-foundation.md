# 一期 1-1：内核类型与数据地基 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立项目骨架、核心类型与交易日历，并构建一个可查询的本地数据仓库——含 `instruments` / `bars_daily` / `corporate_actions` / `adj_factors` 四表、免费数据源适配器、幂等采集流水线、三类质量校验，以及全系统地基性的 `as_of` PIT 访问契约。

**Architecture:** 数据以 Parquet 分区文件落盘，DuckDB 作为查询层。行情事实表只追加永不重写，复权因子单独存储并可随时重算，从结构上杜绝数据腐烂。对外只暴露带 `as_of` 语义的 `HistoricalFeed.get()`，未来函数在 API 层面被堵死。外部数据源全部藏在 `Provider` 抽象之后，单元测试一律跑录制好的 fixture，不触网。

**Tech Stack:** Python 3.11+ · uv · polars · duckdb · pyarrow · pydantic · structlog · akshare · mootdx · pytest · hypothesis

**Spec:** `docs/superpowers/specs/2026-09-06-personal-quant-system-design.md`（本计划实现 §3.4 的 `core/` 与 `data/`、§4 全部、§10 一期中的数据部分）

## Global Constraints

- Python **3.11+**（spec §11）
- 存储格式：**Parquet 文件 + DuckDB 查询层**；不引入 PostgreSQL / ClickHouse（spec §4.1）
- `bars_daily` 存**不复权**价格，**只追加，永不重写**；复权因子单独存于 `adj_factors`，可随时重算（spec §4.2）
- 数据层对外**只暴露带 `as_of` 语义的接口**，禁止提供无 `as_of` 的历史数据读取函数（spec §4.4）
- 股票池在历史每一天都必须包含当时仍在市的全部股票，`instruments` 必须含已退市标的（spec §4.5）
- 采集必须**幂等**：同日重跑结果一致，靠 `(dataset, date)` 主键覆盖写（spec §4.7）
- 所有外部数据源藏在 `Provider` 抽象之后，**单元测试不得发起网络请求**，一律使用录制的 fixture
- 日志使用 **structlog** 结构化输出（spec §9）
- `core/` 不得出现任何 A 股专有常量或规则（涨跌停幅度、T+1 等），保持多市场中立（spec §3.4）
- 依赖管理使用 **uv**，锁文件必须提交（spec §11）

---

## File Structure

```
pyproject.toml                          项目元数据与依赖
uv.lock                                 依赖锁文件（提交）
src/qsys/__init__.py
src/qsys/core/
  __init__.py
  types.py                              Market / Frequency / Symbol / Bar —— 市场中立
  calendar.py                           TradingCalendar 抽象 + AShareCalendar
src/qsys/data/
  __init__.py
  schema.py                             四张表的 pyarrow schema
  storage.py                            Warehouse：Parquet 分区幂等读写
  query.py                              WarehouseQuery：DuckDB 查询层
  feed.py                               DataFeed 抽象 + HistoricalFeed（as_of 契约）
  providers/
    __init__.py
    base.py                             Provider 抽象与数据类
    mootdx_provider.py                  日线行情
    akshare_provider.py                 标的主表 / 交易日历 / 分红送转
  pipeline/
    __init__.py
    ingest_log.py                       采集日志表读写
    instruments.py                      标的主表构建
    bars.py                             日线采集与增量更新
    adjust.py                           复权因子计算
  quality/
    __init__.py
    rules.py                            质量规则 1–3
    report.py                           质量报告与 blocking 闸门
tests/
  conftest.py                           共享 fixture（临时仓库、样例日历）
  core/test_types.py
  core/test_calendar.py
  data/test_storage.py
  data/test_query.py
  data/providers/test_mootdx_provider.py
  data/providers/test_akshare_provider.py
  data/pipeline/test_instruments.py
  data/pipeline/test_bars.py
  data/pipeline/test_adjust.py
  data/quality/test_rules.py
  data/quality/test_report.py
  data/test_feed.py
  fixtures/                             录制的外部 API 返回样本（JSON/CSV）
scripts/
  record_fixtures.py                    一次性探针：打印并保存真实 API 返回结构
```

**责任边界**：`core/` 只定义与市场无关的值类型和日历协议；`data/storage.py` 只管字节进出文件，不懂业务；`data/query.py` 只管 SQL，不懂 PIT；`data/feed.py` 是唯一懂 `as_of` 语义的地方；`pipeline/` 只负责把外部数据变成仓库里的表；`quality/` 只负责判定，不负责修数据。

---

## Task 1: 项目脚手架与核心值类型

**Files:**
- Create: `pyproject.toml`
- Create: `src/qsys/__init__.py`
- Create: `src/qsys/core/__init__.py`
- Create: `src/qsys/core/types.py`
- Test: `tests/core/test_types.py`

**Interfaces:**
- Consumes: 无（首个任务）
- Produces: `Market` (StrEnum: SH/SZ/BJ)、`Frequency` (StrEnum: DAILY="1d"/MIN1="1m")、`Symbol` (frozen dataclass: `code: str`, `market: Market`, `__str__() -> "600036.SH"`, `Symbol.parse(str) -> Symbol`)、`Bar` (frozen dataclass: `symbol: Symbol`, `dt: date`, `open/high/low/close: float`, `volume: int`, `amount: float`)

- [ ] **Step 1: 创建 `pyproject.toml`**

```toml
[project]
name = "qsys"
version = "0.1.0"
description = "Personal quantitative trading system"
requires-python = ">=3.11"
dependencies = [
    "polars>=1.0",
    "duckdb>=1.0",
    "pyarrow>=16.0",
    "pydantic>=2.0",
    "structlog>=24.1",
    "akshare>=1.13",
    "mootdx>=0.11",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "hypothesis>=6.100",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/qsys"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 2: 安装依赖并确认环境**

Run: `uv sync`
Expected: 生成 `.venv/` 与 `uv.lock`，无报错

- [ ] **Step 3: 创建包目录占位文件**

```bash
mkdir -p src/qsys/core tests/core
touch src/qsys/__init__.py src/qsys/core/__init__.py
```

- [ ] **Step 4: 写失败测试**

Create `tests/core/test_types.py`:

```python
from datetime import date

import pytest

from qsys.core.types import Bar, Frequency, Market, Symbol


def test_symbol_str_is_code_dot_market():
    assert str(Symbol("600036", Market.SH)) == "600036.SH"


def test_symbol_parse_round_trip():
    s = Symbol.parse("000001.SZ")
    assert s.code == "000001"
    assert s.market is Market.SZ
    assert str(s) == "000001.SZ"


def test_symbol_parse_is_case_insensitive_on_market():
    assert Symbol.parse("688981.sh") == Symbol("688981", Market.SH)


def test_symbol_rejects_non_six_digit_code():
    with pytest.raises(ValueError, match="invalid code"):
        Symbol("60036", Market.SH)


def test_symbol_parse_rejects_missing_market_suffix():
    with pytest.raises(ValueError, match="missing market suffix"):
        Symbol.parse("600036")


def test_symbol_is_hashable_and_usable_as_dict_key():
    d = {Symbol("600036", Market.SH): 1}
    assert d[Symbol.parse("600036.SH")] == 1


def test_frequency_values():
    assert Frequency.DAILY.value == "1d"
    assert Frequency.MIN1.value == "1m"


def test_bar_holds_ohlcv():
    bar = Bar(
        symbol=Symbol("600036", Market.SH),
        dt=date(2024, 1, 2),
        open=30.0,
        high=31.0,
        low=29.5,
        close=30.5,
        volume=1000,
        amount=30_500.0,
    )
    assert bar.close == 30.5
    assert bar.dt == date(2024, 1, 2)
```

- [ ] **Step 5: 运行测试确认失败**

Run: `uv run pytest tests/core/test_types.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.core.types'`

- [ ] **Step 6: 实现 `src/qsys/core/types.py`**

```python
"""市场中立的核心值类型。

本模块不得包含任何 A 股专有规则（涨跌停、T+1 等）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Market(StrEnum):
    SH = "SH"
    SZ = "SZ"
    BJ = "BJ"


class Frequency(StrEnum):
    DAILY = "1d"
    MIN1 = "1m"


@dataclass(frozen=True, slots=True)
class Symbol:
    code: str
    market: Market

    def __post_init__(self) -> None:
        if not (len(self.code) == 6 and self.code.isdigit()):
            raise ValueError(f"invalid code: {self.code!r}")

    def __str__(self) -> str:
        return f"{self.code}.{self.market.value}"

    @classmethod
    def parse(cls, text: str) -> Symbol:
        code, sep, market = text.partition(".")
        if not sep:
            raise ValueError(f"missing market suffix: {text!r}")
        return cls(code=code, market=Market(market.upper()))


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: Symbol
    dt: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float
```

- [ ] **Step 7: 运行测试确认通过**

Run: `uv run pytest tests/core/test_types.py -v`
Expected: PASS —— 8 passed

- [ ] **Step 8: 提交**

```bash
git add pyproject.toml uv.lock src/qsys tests/core/test_types.py
git commit -m "feat(core): 项目脚手架与市场中立的核心值类型"
```

---

## Task 2: 交易日历

`close_time()` 是后续 `as_of` PIT 契约的时间基准——日线数据在收盘时刻之后才可见。

**Files:**
- Create: `src/qsys/core/calendar.py`
- Test: `tests/core/test_calendar.py`

**Interfaces:**
- Consumes: 无
- Produces: `TradingCalendar` (ABC: `is_trading_day(date) -> bool`, `sessions_in_range(date, date) -> list[date]`, `close_time(date) -> datetime`, `next_session(date) -> date`, `prev_session(date) -> date`)、`AShareCalendar(sessions: Iterable[date])`（收盘时刻固定 15:00）

- [ ] **Step 1: 写失败测试**

Create `tests/core/test_calendar.py`:

```python
from datetime import date, datetime

import pytest

from qsys.core.calendar import AShareCalendar

SESSIONS = [
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 1, 8),
]


@pytest.fixture
def cal():
    return AShareCalendar(SESSIONS)


def test_is_trading_day(cal):
    assert cal.is_trading_day(date(2024, 1, 3)) is True
    assert cal.is_trading_day(date(2024, 1, 6)) is False


def test_sessions_in_range_is_inclusive_on_both_ends(cal):
    assert cal.sessions_in_range(date(2024, 1, 3), date(2024, 1, 5)) == [
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
    ]


def test_sessions_in_range_clips_to_known_sessions(cal):
    assert cal.sessions_in_range(date(2023, 1, 1), date(2024, 1, 2)) == [date(2024, 1, 2)]


def test_close_time_is_fifteen_hundred(cal):
    assert cal.close_time(date(2024, 1, 3)) == datetime(2024, 1, 3, 15, 0)


def test_close_time_rejects_non_trading_day(cal):
    with pytest.raises(ValueError, match="not a trading day"):
        cal.close_time(date(2024, 1, 6))


def test_next_session_skips_weekend(cal):
    assert cal.next_session(date(2024, 1, 5)) == date(2024, 1, 8)


def test_next_session_from_non_trading_day(cal):
    assert cal.next_session(date(2024, 1, 6)) == date(2024, 1, 8)


def test_prev_session(cal):
    assert cal.prev_session(date(2024, 1, 8)) == date(2024, 1, 5)


def test_next_session_beyond_known_range_raises(cal):
    with pytest.raises(ValueError, match="beyond calendar range"):
        cal.next_session(date(2024, 1, 8))


def test_sessions_are_deduplicated_and_sorted():
    cal = AShareCalendar([date(2024, 1, 3), date(2024, 1, 2), date(2024, 1, 3)])
    assert cal.sessions_in_range(date(2024, 1, 1), date(2024, 1, 31)) == [
        date(2024, 1, 2),
        date(2024, 1, 3),
    ]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/core/test_calendar.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.core.calendar'`

- [ ] **Step 3: 实现 `src/qsys/core/calendar.py`**

```python
"""交易日历。

`close_time()` 是 PIT 契约的时间基准：日线数据在该时刻之后才可见。
"""

from __future__ import annotations

import bisect
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import date, datetime, time


class TradingCalendar(ABC):
    @abstractmethod
    def is_trading_day(self, day: date) -> bool: ...

    @abstractmethod
    def sessions_in_range(self, start: date, end: date) -> list[date]: ...

    @abstractmethod
    def close_time(self, day: date) -> datetime: ...

    @abstractmethod
    def next_session(self, day: date) -> date: ...

    @abstractmethod
    def prev_session(self, day: date) -> date: ...


class AShareCalendar(TradingCalendar):
    CLOSE = time(15, 0)

    def __init__(self, sessions: Iterable[date]) -> None:
        self._sessions = sorted(set(sessions))
        self._lookup = set(self._sessions)

    def is_trading_day(self, day: date) -> bool:
        return day in self._lookup

    def sessions_in_range(self, start: date, end: date) -> list[date]:
        lo = bisect.bisect_left(self._sessions, start)
        hi = bisect.bisect_right(self._sessions, end)
        return self._sessions[lo:hi]

    def close_time(self, day: date) -> datetime:
        if day not in self._lookup:
            raise ValueError(f"{day} is not a trading day")
        return datetime.combine(day, self.CLOSE)

    def next_session(self, day: date) -> date:
        idx = bisect.bisect_right(self._sessions, day)
        if idx >= len(self._sessions):
            raise ValueError(f"{day} is beyond calendar range")
        return self._sessions[idx]

    def prev_session(self, day: date) -> date:
        idx = bisect.bisect_left(self._sessions, day)
        if idx == 0:
            raise ValueError(f"{day} is beyond calendar range")
        return self._sessions[idx - 1]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/core/test_calendar.py -v`
Expected: PASS —— 10 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/core/calendar.py tests/core/test_calendar.py
git commit -m "feat(core): 交易日历抽象与 A 股实现"
```

---

## Task 3: 仓库 schema 与幂等 Parquet 存储

**Files:**
- Create: `src/qsys/data/__init__.py`
- Create: `src/qsys/data/schema.py`
- Create: `src/qsys/data/storage.py`
- Test: `tests/data/test_storage.py`
- Test: `tests/conftest.py`

**Interfaces:**
- Consumes: 无
- Produces: `schema.INSTRUMENTS / BARS_DAILY / CORPORATE_ACTIONS / ADJ_FACTORS / INGEST_LOG`（`pyarrow.Schema`）；`Warehouse(root: Path)` 提供 `write(dataset: str, table: pa.Table, *, year: int | None = None) -> Path`（原子幂等覆盖写）、`path_for(dataset, year) -> Path`、`root: Path`

- [ ] **Step 1: 写失败测试**

Create `tests/conftest.py`:

```python
from pathlib import Path

import pytest

from qsys.data.storage import Warehouse


@pytest.fixture
def warehouse(tmp_path: Path) -> Warehouse:
    return Warehouse(tmp_path / "warehouse")
```

Create `tests/data/test_storage.py`:

```python
from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from qsys.data import schema
from qsys.data.storage import Warehouse


def _bars(close: float) -> pa.Table:
    return pa.table(
        {
            "symbol": ["600036.SH"],
            "dt": [date(2024, 1, 2)],
            "open": [30.0],
            "high": [31.0],
            "low": [29.5],
            "close": [close],
            "volume": [1000],
            "amount": [30500.0],
        },
        schema=schema.BARS_DAILY,
    )


def test_write_creates_partitioned_file(warehouse: Warehouse):
    path = warehouse.write("bars_daily", _bars(30.5), year=2024)
    assert path.exists()
    assert path == warehouse.root / "bars_daily" / "year=2024" / "part.parquet"


def test_write_is_idempotent_overwrite(warehouse: Warehouse):
    warehouse.write("bars_daily", _bars(30.5), year=2024)
    path = warehouse.write("bars_daily", _bars(31.5), year=2024)
    table = pq.read_table(path)
    assert table.num_rows == 1
    assert table.column("close").to_pylist() == [31.5]


def test_write_leaves_no_temp_files(warehouse: Warehouse):
    warehouse.write("bars_daily", _bars(30.5), year=2024)
    leftovers = list((warehouse.root / "bars_daily" / "year=2024").glob("*.tmp*"))
    assert leftovers == []


def test_write_rejects_schema_mismatch(warehouse: Warehouse):
    wrong = pa.table({"symbol": ["600036.SH"]})
    with pytest.raises(ValueError, match="schema mismatch"):
        warehouse.write("bars_daily", wrong, year=2024)


def test_write_unpartitioned_dataset(warehouse: Warehouse):
    table = pa.table(
        {
            "symbol": ["600036.SH"],
            "name": ["招商银行"],
            "market": ["SH"],
            "list_date": [date(2002, 4, 9)],
            "delist_date": [None],
            "board": ["main"],
        },
        schema=schema.INSTRUMENTS,
    )
    path = warehouse.write("instruments", table)
    assert path == warehouse.root / "instruments" / "part.parquet"


def test_write_rejects_unknown_dataset(warehouse: Warehouse):
    with pytest.raises(KeyError, match="unknown dataset"):
        warehouse.write("nope", _bars(1.0), year=2024)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/test_storage.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.schema'`

- [ ] **Step 3: 实现 `src/qsys/data/schema.py`**

```python
"""仓库四张地基表 + 采集日志的 pyarrow schema。

bars_daily 存不复权价格，只追加永不重写；复权因子单独存于 adj_factors。
"""

from __future__ import annotations

import pyarrow as pa

INSTRUMENTS = pa.schema(
    [
        ("symbol", pa.string()),
        ("name", pa.string()),
        ("market", pa.string()),
        ("list_date", pa.date32()),
        ("delist_date", pa.date32()),  # 未退市为 null
        ("board", pa.string()),  # main / gem / star / bse
    ]
)

BARS_DAILY = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.int64()),
        ("amount", pa.float64()),
    ]
)

CORPORATE_ACTIONS = pa.schema(
    [
        ("symbol", pa.string()),
        ("ex_date", pa.date32()),  # 除权除息日
        ("cash_per_share", pa.float64()),  # 每股现金红利（税前）
        ("stock_per_share", pa.float64()),  # 每股送转股数
        ("rights_per_share", pa.float64()),  # 每股配股数
        ("rights_price", pa.float64()),  # 配股价
    ]
)

ADJ_FACTORS = pa.schema(
    [
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("cum_factor", pa.float64()),  # 后复权累计因子
    ]
)

INGEST_LOG = pa.schema(
    [
        ("dataset", pa.string()),
        ("dt", pa.date32()),
        ("provider", pa.string()),
        ("ingested_at", pa.timestamp("us")),
        ("row_count", pa.int64()),
        ("ok", pa.bool_()),
        ("message", pa.string()),
    ]
)

PARTITIONED: dict[str, pa.Schema] = {
    "bars_daily": BARS_DAILY,
    "ingest_log": INGEST_LOG,
}

UNPARTITIONED: dict[str, pa.Schema] = {
    "instruments": INSTRUMENTS,
    "corporate_actions": CORPORATE_ACTIONS,
    "adj_factors": ADJ_FACTORS,
}

ALL: dict[str, pa.Schema] = {**PARTITIONED, **UNPARTITIONED}
```

- [ ] **Step 4: 实现 `src/qsys/data/storage.py`**

```python
"""Parquet 分区仓库的字节层。只管文件进出，不懂业务语义。"""

from __future__ import annotations

import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from qsys.data import schema


class Warehouse:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, dataset: str, year: int | None = None) -> Path:
        if dataset not in schema.ALL:
            raise KeyError(f"unknown dataset: {dataset!r}")
        base = self.root / dataset
        if year is not None:
            base = base / f"year={year}"
        return base / "part.parquet"

    def write(self, dataset: str, table: pa.Table, *, year: int | None = None) -> Path:
        """原子幂等覆盖写：同一 (dataset, year) 重复写入结果一致。"""
        expected = schema.ALL.get(dataset)
        if expected is None:
            raise KeyError(f"unknown dataset: {dataset!r}")
        if not table.schema.equals(expected):
            raise ValueError(f"schema mismatch for {dataset!r}")

        path = self.path_for(dataset, year)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp)
        os.replace(tmp, path)
        return path
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/data/test_storage.py -v`
Expected: PASS —— 6 passed

- [ ] **Step 6: 提交**

```bash
git add src/qsys/data tests/conftest.py tests/data/test_storage.py
git commit -m "feat(data): 仓库 schema 与原子幂等 Parquet 存储层"
```

---

## Task 4: DuckDB 查询层

**Files:**
- Create: `src/qsys/data/query.py`
- Test: `tests/data/test_query.py`

**Interfaces:**
- Consumes: `Warehouse`（Task 3）、`schema`（Task 3）
- Produces: `WarehouseQuery(warehouse: Warehouse)` 提供 `daily_bars(symbols: Sequence[str] | None, start: date, end: date) -> pl.DataFrame`、`instruments() -> pl.DataFrame`、`corporate_actions(symbols=None) -> pl.DataFrame`、`adj_factors(symbols=None) -> pl.DataFrame`；数据集为空时返回**带正确列名的空 DataFrame**而非抛错

- [ ] **Step 1: 写失败测试**

Create `tests/data/test_query.py`:

```python
from datetime import date

import pyarrow as pa
import polars as pl

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


def _seed(warehouse: Warehouse) -> None:
    warehouse.write(
        "bars_daily",
        pa.table(
            {
                "symbol": ["600036.SH", "600036.SH", "000001.SZ"],
                "dt": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 2)],
                "open": [30.0, 30.5, 10.0],
                "high": [31.0, 31.5, 10.5],
                "low": [29.5, 30.0, 9.8],
                "close": [30.5, 31.0, 10.2],
                "volume": [1000, 1100, 2000],
                "amount": [30500.0, 34100.0, 20400.0],
            },
            schema=schema.BARS_DAILY,
        ),
        year=2024,
    )


def test_daily_bars_filters_by_date_range(warehouse: Warehouse):
    _seed(warehouse)
    q = WarehouseQuery(warehouse)
    df = q.daily_bars(None, date(2024, 1, 3), date(2024, 1, 3))
    assert df.height == 1
    assert df["symbol"].to_list() == ["600036.SH"]


def test_daily_bars_filters_by_symbols(warehouse: Warehouse):
    _seed(warehouse)
    q = WarehouseQuery(warehouse)
    df = q.daily_bars(["000001.SZ"], date(2024, 1, 1), date(2024, 12, 31))
    assert df["symbol"].to_list() == ["000001.SZ"]


def test_daily_bars_is_sorted_by_symbol_then_dt(warehouse: Warehouse):
    _seed(warehouse)
    q = WarehouseQuery(warehouse)
    df = q.daily_bars(None, date(2024, 1, 1), date(2024, 12, 31))
    assert df["symbol"].to_list() == ["000001.SZ", "600036.SH", "600036.SH"]
    assert df["dt"].to_list() == [date(2024, 1, 2), date(2024, 1, 2), date(2024, 1, 3)]


def test_daily_bars_on_empty_warehouse_returns_empty_frame_with_columns(warehouse: Warehouse):
    q = WarehouseQuery(warehouse)
    df = q.daily_bars(None, date(2024, 1, 1), date(2024, 12, 31))
    assert df.height == 0
    assert df.columns == [f.name for f in schema.BARS_DAILY]


def test_instruments_on_empty_warehouse_returns_empty_frame(warehouse: Warehouse):
    q = WarehouseQuery(warehouse)
    df = q.instruments()
    assert df.height == 0
    assert df.columns == [f.name for f in schema.INSTRUMENTS]


def test_empty_symbol_list_returns_nothing(warehouse: Warehouse):
    _seed(warehouse)
    q = WarehouseQuery(warehouse)
    assert q.daily_bars([], date(2024, 1, 1), date(2024, 12, 31)).height == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/test_query.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.query'`

- [ ] **Step 3: 实现 `src/qsys/data/query.py`**

```python
"""DuckDB 查询层。只管 SQL，不懂 PIT 语义——PIT 由 feed.py 负责。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import duckdb
import polars as pl
import pyarrow as pa

from qsys.data import schema
from qsys.data.storage import Warehouse


def _empty(arrow_schema: pa.Schema) -> pl.DataFrame:
    return pl.from_arrow(arrow_schema.empty_table())


class WarehouseQuery:
    def __init__(self, warehouse: Warehouse) -> None:
        self._wh = warehouse
        self._con = duckdb.connect()

    def _glob(self, dataset: str, partitioned: bool) -> str | None:
        base = self._wh.root / dataset
        pattern = "year=*/part.parquet" if partitioned else "part.parquet"
        if not list(base.glob(pattern)):
            return None
        return str(base / pattern)

    def daily_bars(
        self,
        symbols: Sequence[str] | None,
        start: date,
        end: date,
    ) -> pl.DataFrame:
        if symbols is not None and len(symbols) == 0:
            return _empty(schema.BARS_DAILY)
        glob = self._glob("bars_daily", partitioned=True)
        if glob is None:
            return _empty(schema.BARS_DAILY)

        sql = (
            "SELECT symbol, dt, open, high, low, close, volume, amount "
            f"FROM read_parquet('{glob}') WHERE dt BETWEEN ? AND ?"
        )
        params: list[object] = [start, end]
        if symbols is not None:
            placeholders = ", ".join("?" for _ in symbols)
            sql += f" AND symbol IN ({placeholders})"
            params.extend(symbols)
        sql += " ORDER BY symbol, dt"
        return self._con.execute(sql, params).pl()

    def _read_unpartitioned(
        self,
        dataset: str,
        arrow_schema: pa.Schema,
        symbols: Sequence[str] | None = None,
    ) -> pl.DataFrame:
        if symbols is not None and len(symbols) == 0:
            return _empty(arrow_schema)
        glob = self._glob(dataset, partitioned=False)
        if glob is None:
            return _empty(arrow_schema)
        sql = f"SELECT * FROM read_parquet('{glob}')"
        params: list[object] = []
        if symbols is not None:
            placeholders = ", ".join("?" for _ in symbols)
            sql += f" WHERE symbol IN ({placeholders})"
            params.extend(symbols)
        return self._con.execute(sql, params).pl()

    def instruments(self) -> pl.DataFrame:
        return self._read_unpartitioned("instruments", schema.INSTRUMENTS)

    def corporate_actions(self, symbols: Sequence[str] | None = None) -> pl.DataFrame:
        return self._read_unpartitioned(
            "corporate_actions", schema.CORPORATE_ACTIONS, symbols
        )

    def adj_factors(self, symbols: Sequence[str] | None = None) -> pl.DataFrame:
        return self._read_unpartitioned("adj_factors", schema.ADJ_FACTORS, symbols)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/data/test_query.py -v`
Expected: PASS —— 6 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/query.py tests/data/test_query.py
git commit -m "feat(data): DuckDB 查询层"
```

---

## Task 5: Provider 抽象与 fixture 录制探针

外部数据源的真实返回结构必须先探明再编码。本任务先建立抽象与探针脚本，后两个任务实现具体 provider。

**Files:**
- Create: `src/qsys/data/providers/__init__.py`
- Create: `src/qsys/data/providers/base.py`
- Create: `scripts/record_fixtures.py`
- Test: `tests/data/providers/test_base.py`

**Interfaces:**
- Consumes: `Symbol`、`Market`（Task 1）
- Produces: `BarsProvider` (ABC: `name: str`, `fetch_daily_bars(symbol: str, start: date, end: date) -> pl.DataFrame` 返回列 `dt/open/high/low/close/volume/amount`)、`ReferenceProvider` (ABC: `name: str`, `fetch_instruments() -> pl.DataFrame`, `fetch_sessions(start: date, end: date) -> list[date]`, `fetch_corporate_actions(symbol: str) -> pl.DataFrame`)、`normalize_symbol(raw_code: str) -> str`（六位裸码 → `"600036.SH"`）

- [ ] **Step 1: 写失败测试**

Create `tests/data/providers/test_base.py`:

```python
import pytest

from qsys.data.providers.base import normalize_symbol


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("600036", "600036.SH"),
        ("601318", "601318.SH"),
        ("603288", "603288.SH"),
        ("688981", "688981.SH"),
        ("000001", "000001.SZ"),
        ("002415", "002415.SZ"),
        ("300750", "300750.SZ"),
        ("430047", "430047.BJ"),
        ("830799", "830799.BJ"),
        ("920001", "920001.BJ"),
    ],
)
def test_normalize_symbol_infers_market_from_prefix(raw, expected):
    assert normalize_symbol(raw) == expected


def test_normalize_symbol_strips_existing_suffix():
    assert normalize_symbol("600036.SH") == "600036.SH"
    assert normalize_symbol("sh600036") == "600036.SH"


def test_normalize_symbol_rejects_unknown_prefix():
    with pytest.raises(ValueError, match="cannot infer market"):
        normalize_symbol("123456")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/providers/test_base.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.providers'`

- [ ] **Step 3: 实现 `src/qsys/data/providers/base.py`**

```python
"""数据源适配器抽象。

上层只依赖这些接口，免费源接口变更时可整体换源。
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import date

import polars as pl

_SH_PREFIXES = ("60", "68", "51", "58")
_SZ_PREFIXES = ("00", "30", "15", "16")
_BJ_PREFIXES = ("43", "83", "87", "92")

_CODE_RE = re.compile(r"(\d{6})")


def normalize_symbol(raw_code: str) -> str:
    """六位裸码或带前后缀的代码 → 规范形式 '600036.SH'。"""
    match = _CODE_RE.search(raw_code)
    if match is None:
        raise ValueError(f"cannot infer market: {raw_code!r}")
    code = match.group(1)
    prefix = code[:2]
    if prefix in _SH_PREFIXES:
        return f"{code}.SH"
    if prefix in _SZ_PREFIXES:
        return f"{code}.SZ"
    if prefix in _BJ_PREFIXES:
        return f"{code}.BJ"
    raise ValueError(f"cannot infer market: {raw_code!r}")


class BarsProvider(ABC):
    name: str

    @abstractmethod
    def fetch_daily_bars(self, symbol: str, start: date, end: date) -> pl.DataFrame:
        """返回列：dt, open, high, low, close, volume, amount（不复权）。"""


class ReferenceProvider(ABC):
    name: str

    @abstractmethod
    def fetch_instruments(self) -> pl.DataFrame:
        """返回列：symbol, name, market, list_date, delist_date, board。"""

    @abstractmethod
    def fetch_sessions(self, start: date, end: date) -> list[date]:
        """返回区间内的交易日列表。"""

    @abstractmethod
    def fetch_corporate_actions(self, symbol: str) -> pl.DataFrame:
        """返回列：ex_date, cash_per_share, stock_per_share, rights_per_share, rights_price。"""
```

- [ ] **Step 4: 编写 fixture 录制探针 `scripts/record_fixtures.py`**

```python
"""一次性探针：打印并保存外部数据源的真实返回结构。

单元测试不触网，一律读 tests/fixtures/ 下的录制样本。
本脚本手动运行，用于建立或刷新这些样本。

用法：uv run python scripts/record_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

import akshare as ak
from mootdx.quotes import Quotes

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def dump(name: str, frame) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / f"{name}.csv"
    frame.to_csv(path, index=False)
    print(f"=== {name} -> {path}")
    print("columns:", list(frame.columns))
    print("dtypes:\n", frame.dtypes)
    print(frame.head(5).to_string())
    print()


def main() -> None:
    dump("ak_trade_dates", ak.tool_trade_date_hist_sina())
    dump("ak_code_name", ak.stock_info_a_code_name())
    dump(
        "ak_daily_600036",
        ak.stock_zh_a_hist(
            symbol="600036", period="daily",
            start_date="20240101", end_date="20240131", adjust="",
        ),
    )
    dump("ak_dividend_600036", ak.stock_fhps_detail_em(symbol="600036"))
    dump("ak_delist_sh", ak.stock_info_sh_delist())
    dump("ak_delist_sz", ak.stock_info_sz_delist())

    client = Quotes.factory(market="std")
    dump("mootdx_daily_600036", client.bars(symbol="600036", frequency=9, offset=30))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 运行探针，记录真实结构**

Run: `uv run python scripts/record_fixtures.py`
Expected: `tests/fixtures/` 下生成 7 个 CSV，终端打印每个的列名与前 5 行。

**若某个 akshare/mootdx 函数名或签名与本计划不符**（免费源接口会变），以终端实际输出为准，修正 `scripts/record_fixtures.py` 后重跑，并在后续 Task 6/7 的解析代码中使用实际列名。**不要跳过这一步**——后两个任务的列名映射全部依赖此处录得的真实结构。

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run pytest tests/data/providers/test_base.py -v`
Expected: PASS —— 14 passed

- [ ] **Step 7: 提交**

```bash
git add src/qsys/data/providers scripts/record_fixtures.py tests/data/providers/test_base.py tests/fixtures
git commit -m "feat(data): provider 抽象、代码规范化与 fixture 录制探针"
```

---

## Task 6: mootdx 日线 provider

**Files:**
- Create: `src/qsys/data/providers/mootdx_provider.py`
- Test: `tests/data/providers/test_mootdx_provider.py`

**Interfaces:**
- Consumes: `BarsProvider`、`normalize_symbol`（Task 5）
- Produces: `MootdxProvider(client=None)` 实现 `BarsProvider`，`name = "mootdx"`；构造函数接受可注入的 client 以便测试

- [ ] **Step 1: 写失败测试**

Create `tests/data/providers/test_mootdx_provider.py`:

```python
from datetime import date

import pandas as pd
import polars as pl
import pytest

from qsys.data.providers.mootdx_provider import MootdxProvider


class FakeClient:
    """模拟 mootdx Quotes 客户端。列名以 Task 5 探针录得的结构为准。"""

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame
        self.calls: list[dict] = []

    def bars(self, symbol, frequency, offset):
        self.calls.append({"symbol": symbol, "frequency": frequency, "offset": offset})
        return self._frame


@pytest.fixture
def frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": ["2024-01-02 00:00", "2024-01-03 00:00", "2024-01-04 00:00"],
            "open": [30.0, 30.5, 31.0],
            "high": [31.0, 31.5, 31.2],
            "low": [29.5, 30.0, 30.4],
            "close": [30.5, 31.0, 30.6],
            "vol": [1000.0, 1100.0, 900.0],
            "amount": [30500.0, 34100.0, 27540.0],
        }
    )


def test_returns_normalized_columns(frame):
    provider = MootdxProvider(client=FakeClient(frame))
    df = provider.fetch_daily_bars("600036.SH", date(2024, 1, 1), date(2024, 1, 31))
    assert df.columns == ["dt", "open", "high", "low", "close", "volume", "amount"]


def test_parses_dates_and_casts_volume_to_int(frame):
    provider = MootdxProvider(client=FakeClient(frame))
    df = provider.fetch_daily_bars("600036.SH", date(2024, 1, 1), date(2024, 1, 31))
    assert df["dt"].to_list()[0] == date(2024, 1, 2)
    assert df["volume"].dtype == pl.Int64
    assert df["volume"].to_list() == [1000, 1100, 900]


def test_filters_to_requested_range(frame):
    provider = MootdxProvider(client=FakeClient(frame))
    df = provider.fetch_daily_bars("600036.SH", date(2024, 1, 3), date(2024, 1, 3))
    assert df["dt"].to_list() == [date(2024, 1, 3)]


def test_strips_market_suffix_when_calling_client(frame):
    client = FakeClient(frame)
    MootdxProvider(client=client).fetch_daily_bars(
        "600036.SH", date(2024, 1, 1), date(2024, 1, 31)
    )
    assert client.calls[0]["symbol"] == "600036"
    assert client.calls[0]["frequency"] == 9


def test_empty_response_returns_empty_frame_with_columns():
    provider = MootdxProvider(client=FakeClient(pd.DataFrame()))
    df = provider.fetch_daily_bars("600036.SH", date(2024, 1, 1), date(2024, 1, 31))
    assert df.height == 0
    assert df.columns == ["dt", "open", "high", "low", "close", "volume", "amount"]


def test_none_response_returns_empty_frame():
    class NoneClient:
        def bars(self, **kwargs):
            return None

    provider = MootdxProvider(client=NoneClient())
    df = provider.fetch_daily_bars("600036.SH", date(2024, 1, 1), date(2024, 1, 31))
    assert df.height == 0


def test_provider_name():
    assert MootdxProvider(client=FakeClient(pd.DataFrame())).name == "mootdx"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/providers/test_mootdx_provider.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.providers.mootdx_provider'`

- [ ] **Step 3: 实现 `src/qsys/data/providers/mootdx_provider.py`**

```python
"""mootdx 日线行情适配器（不复权）。"""

from __future__ import annotations

from datetime import date

import polars as pl

from qsys.data.providers.base import BarsProvider

COLUMNS = ["dt", "open", "high", "low", "close", "volume", "amount"]

_DAILY_FREQUENCY = 9  # mootdx 频率码：日线
_MAX_OFFSET = 800


def _empty() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "dt": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Int64,
            "amount": pl.Float64,
        }
    )


class MootdxProvider(BarsProvider):
    name = "mootdx"

    def __init__(self, client=None) -> None:
        if client is None:
            from mootdx.quotes import Quotes

            client = Quotes.factory(market="std")
        self._client = client

    def fetch_daily_bars(self, symbol: str, start: date, end: date) -> pl.DataFrame:
        code = symbol.split(".")[0]
        raw = self._client.bars(
            symbol=code, frequency=_DAILY_FREQUENCY, offset=_MAX_OFFSET
        )
        if raw is None or len(raw) == 0:
            return _empty()

        df = pl.from_pandas(raw.reset_index(drop=True))
        return (
            df.select(
                pl.col("datetime").str.slice(0, 10).str.to_date().alias("dt"),
                pl.col("open").cast(pl.Float64),
                pl.col("high").cast(pl.Float64),
                pl.col("low").cast(pl.Float64),
                pl.col("close").cast(pl.Float64),
                pl.col("vol").cast(pl.Int64).alias("volume"),
                pl.col("amount").cast(pl.Float64),
            )
            .filter(pl.col("dt").is_between(start, end))
            .sort("dt")
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/data/providers/test_mootdx_provider.py -v`
Expected: PASS —— 7 passed

- [ ] **Step 5: 用真实客户端做一次冒烟验证**

Run: `uv run python -c "
from datetime import date
from qsys.data.providers.mootdx_provider import MootdxProvider
df = MootdxProvider().fetch_daily_bars('600036.SH', date(2024,1,1), date(2024,1,31))
print(df)
"`
Expected: 打印 2024 年 1 月招商银行的日线，约 20 余行，价格量级在 30 元左右。

若列名与 Task 5 录得的 fixture 不符，以真实返回为准修正 `fetch_daily_bars` 中的列映射，并同步更新测试里的 `FakeClient` 数据。

- [ ] **Step 6: 提交**

```bash
git add src/qsys/data/providers/mootdx_provider.py tests/data/providers/test_mootdx_provider.py
git commit -m "feat(data): mootdx 日线行情 provider"
```

---

## Task 7: akshare 参考数据 provider

**Files:**
- Create: `src/qsys/data/providers/akshare_provider.py`
- Test: `tests/data/providers/test_akshare_provider.py`

**Interfaces:**
- Consumes: `ReferenceProvider`、`normalize_symbol`（Task 5）
- Produces: `AkshareProvider(api=None)` 实现 `ReferenceProvider`，`name = "akshare"`；`api` 为可注入的模块级门面对象，暴露 `trade_dates()`、`code_name()`、`delist_sh()`、`delist_sz()`、`dividends(code)` 五个方法

- [ ] **Step 1: 写失败测试**

Create `tests/data/providers/test_akshare_provider.py`:

```python
from datetime import date

import pandas as pd
import pytest

from qsys.data.providers.akshare_provider import AkshareProvider


class FakeApi:
    def trade_dates(self):
        return pd.DataFrame({"trade_date": ["2024-01-02", "2024-01-03", "2024-01-04"]})

    def code_name(self):
        return pd.DataFrame(
            {"code": ["600036", "300750", "830799"], "name": ["招商银行", "宁德时代", "圣泉集团"]}
        )

    def delist_sh(self):
        return pd.DataFrame({"公司代码": ["600001"], "暂停上市日期": ["2019-06-25"]})

    def delist_sz(self):
        return pd.DataFrame({"证券代码": ["000003"], "终止上市日期": ["2002-06-14"]})

    def dividends(self, code):
        return pd.DataFrame(
            {
                "除权除息日": ["2024-07-11"],
                "现金分红-现金分红比例": [19.72],   # 每 10 股派 19.72 元
                "送转股份-送股比例": [0.0],
                "送转股份-转股比例": [0.0],
            }
        )


@pytest.fixture
def provider() -> AkshareProvider:
    return AkshareProvider(api=FakeApi())


def test_fetch_sessions_filters_range(provider):
    assert provider.fetch_sessions(date(2024, 1, 3), date(2024, 1, 4)) == [
        date(2024, 1, 3),
        date(2024, 1, 4),
    ]


def test_fetch_instruments_columns(provider):
    df = provider.fetch_instruments()
    assert df.columns == ["symbol", "name", "market", "list_date", "delist_date", "board"]


def test_fetch_instruments_normalizes_symbols_and_boards(provider):
    df = provider.fetch_instruments().sort("symbol")
    assert df["symbol"].to_list() == ["300750.SZ", "600036.SH", "830799.BJ"]
    assert df["board"].to_list() == ["gem", "main", "bse"]
    assert df["market"].to_list() == ["SZ", "SH", "BJ"]


def test_fetch_instruments_includes_delisted(provider):
    df = provider.fetch_instruments()
    delisted = df.filter(df["delist_date"].is_not_null()).sort("symbol")
    assert delisted["symbol"].to_list() == ["000003.SZ", "600001.SH"]
    assert delisted["delist_date"].to_list() == [date(2002, 6, 14), date(2019, 6, 25)]


def test_fetch_corporate_actions_converts_per_ten_shares_to_per_share(provider):
    df = provider.fetch_corporate_actions("600036.SH")
    assert df["ex_date"].to_list() == [date(2024, 7, 11)]
    assert df["cash_per_share"].to_list() == [pytest.approx(1.972)]
    assert df["stock_per_share"].to_list() == [0.0]


def test_fetch_corporate_actions_columns(provider):
    df = provider.fetch_corporate_actions("600036.SH")
    assert df.columns == [
        "ex_date",
        "cash_per_share",
        "stock_per_share",
        "rights_per_share",
        "rights_price",
    ]


def test_provider_name(provider):
    assert provider.name == "akshare"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/providers/test_akshare_provider.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.providers.akshare_provider'`

- [ ] **Step 3: 实现 `src/qsys/data/providers/akshare_provider.py`**

```python
"""akshare 参考数据适配器：交易日历、标的主表（含退市）、分红送转。

akshare 的返回列名会随版本变化，全部集中在 _AkshareApi 门面与本文件的
列名常量中，变更时只需改这一处。
"""

from __future__ import annotations

from datetime import date

import polars as pl

from qsys.data.providers.base import ReferenceProvider, normalize_symbol

INSTRUMENT_COLUMNS = ["symbol", "name", "market", "list_date", "delist_date", "board"]
ACTION_COLUMNS = [
    "ex_date",
    "cash_per_share",
    "stock_per_share",
    "rights_per_share",
    "rights_price",
]


class _AkshareApi:
    """真实 akshare 调用的薄门面，便于测试注入。"""

    def trade_dates(self):
        import akshare as ak

        return ak.tool_trade_date_hist_sina()

    def code_name(self):
        import akshare as ak

        return ak.stock_info_a_code_name()

    def delist_sh(self):
        import akshare as ak

        return ak.stock_info_sh_delist()

    def delist_sz(self):
        import akshare as ak

        return ak.stock_info_sz_delist()

    def dividends(self, code: str):
        import akshare as ak

        return ak.stock_fhps_detail_em(symbol=code)


def _board_of(code: str) -> str:
    if code.startswith("688"):
        return "star"
    if code.startswith("300") or code.startswith("301"):
        return "gem"
    if code[:2] in ("43", "83", "87", "92"):
        return "bse"
    return "main"


def _to_date_series(values) -> pl.Series:
    return (
        pl.Series(values, dtype=pl.Utf8)
        .str.replace_all(r"[^0-9]", "")
        .str.strptime(pl.Date, format="%Y%m%d", strict=False)
    )


class AkshareProvider(ReferenceProvider):
    name = "akshare"

    def __init__(self, api=None) -> None:
        self._api = api if api is not None else _AkshareApi()

    def fetch_sessions(self, start: date, end: date) -> list[date]:
        raw = pl.from_pandas(self._api.trade_dates())
        col = raw.columns[0]
        days = _to_date_series(raw[col].cast(pl.Utf8))
        return [d for d in days.drop_nulls().to_list() if start <= d <= end]

    def _delisted(self) -> dict[str, date]:
        out: dict[str, date] = {}
        for frame, code_col, date_col in (
            (self._api.delist_sh(), "公司代码", "暂停上市日期"),
            (self._api.delist_sz(), "证券代码", "终止上市日期"),
        ):
            df = pl.from_pandas(frame)
            if df.height == 0:
                continue
            codes = df[code_col].cast(pl.Utf8).to_list()
            dates = _to_date_series(df[date_col].cast(pl.Utf8)).to_list()
            for raw_code, delist in zip(codes, dates, strict=True):
                if delist is None:
                    continue
                out[normalize_symbol(raw_code)] = delist
        return out

    def fetch_instruments(self) -> pl.DataFrame:
        listed = pl.from_pandas(self._api.code_name())
        code_col, name_col = listed.columns[0], listed.columns[1]

        rows: list[dict] = []
        for raw_code, name in zip(
            listed[code_col].cast(pl.Utf8).to_list(),
            listed[name_col].cast(pl.Utf8).to_list(),
            strict=True,
        ):
            symbol = normalize_symbol(raw_code)
            rows.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "market": symbol.split(".")[1],
                    "list_date": None,
                    "delist_date": None,
                    "board": _board_of(symbol.split(".")[0]),
                }
            )

        for symbol, delist in self._delisted().items():
            rows.append(
                {
                    "symbol": symbol,
                    "name": "",
                    "market": symbol.split(".")[1],
                    "list_date": None,
                    "delist_date": delist,
                    "board": _board_of(symbol.split(".")[0]),
                }
            )

        return pl.DataFrame(
            rows,
            schema={
                "symbol": pl.Utf8,
                "name": pl.Utf8,
                "market": pl.Utf8,
                "list_date": pl.Date,
                "delist_date": pl.Date,
                "board": pl.Utf8,
            },
        ).select(INSTRUMENT_COLUMNS)

    def fetch_corporate_actions(self, symbol: str) -> pl.DataFrame:
        code = symbol.split(".")[0]
        raw = pl.from_pandas(self._api.dividends(code))
        empty = pl.DataFrame(
            schema={
                "ex_date": pl.Date,
                "cash_per_share": pl.Float64,
                "stock_per_share": pl.Float64,
                "rights_per_share": pl.Float64,
                "rights_price": pl.Float64,
            }
        )
        if raw.height == 0:
            return empty

        return (
            raw.select(
                _to_date_series(raw["除权除息日"].cast(pl.Utf8)).alias("ex_date"),
                # akshare 以"每 10 股"为单位，此处统一折算为每股
                (pl.col("现金分红-现金分红比例").cast(pl.Float64) / 10.0).alias(
                    "cash_per_share"
                ),
                (
                    (
                        pl.col("送转股份-送股比例").cast(pl.Float64)
                        + pl.col("送转股份-转股比例").cast(pl.Float64)
                    )
                    / 10.0
                ).alias("stock_per_share"),
                pl.lit(0.0).alias("rights_per_share"),
                pl.lit(0.0).alias("rights_price"),
            )
            .drop_nulls("ex_date")
            .fill_null(0.0)
            .sort("ex_date")
            .select(ACTION_COLUMNS)
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/data/providers/test_akshare_provider.py -v`
Expected: PASS —— 7 passed

- [ ] **Step 5: 用真实 API 做一次冒烟验证**

Run: `uv run python -c "
from datetime import date
from qsys.data.providers.akshare_provider import AkshareProvider
p = AkshareProvider()
print('sessions:', p.fetch_sessions(date(2024,1,1), date(2024,1,10)))
inst = p.fetch_instruments()
print('instruments:', inst.height)
print('delisted:', inst.filter(inst['delist_date'].is_not_null()).height)
print(p.fetch_corporate_actions('600036.SH'))
"`
Expected: sessions 为 2024 年 1 月上旬的交易日；instruments 行数 5000+ 且 delisted 数量大于 0；分红表含除权日与每股现金分红。

若某列名不存在（akshare 版本差异），以 Task 5 录得的 fixture 为准修正 `_AkshareApi` 与列名常量，并同步更新测试里的 `FakeApi`。

- [ ] **Step 6: 提交**

```bash
git add src/qsys/data/providers/akshare_provider.py tests/data/providers/test_akshare_provider.py
git commit -m "feat(data): akshare 参考数据 provider（日历/标的主表/分红送转）"
```

---

## Task 8: 采集日志

**Files:**
- Create: `src/qsys/data/pipeline/__init__.py`
- Create: `src/qsys/data/pipeline/ingest_log.py`
- Test: `tests/data/pipeline/test_ingest_log.py`

**Interfaces:**
- Consumes: `Warehouse`（Task 3）、`WarehouseQuery`（Task 4）、`schema.INGEST_LOG`（Task 3）
- Produces: `IngestLog(warehouse: Warehouse)` 提供 `record(dataset: str, dt: date, provider: str, row_count: int, ok: bool, message: str = "") -> None`（按 `(dataset, dt, provider)` 幂等去重，保留最后一次）与 `entries(year: int) -> pl.DataFrame`

- [ ] **Step 1: 写失败测试**

Create `tests/data/pipeline/test_ingest_log.py`:

```python
from datetime import date

from qsys.data.pipeline.ingest_log import IngestLog
from qsys.data.storage import Warehouse


def test_record_appends_entry(warehouse: Warehouse):
    log = IngestLog(warehouse)
    log.record("bars_daily", date(2024, 1, 2), "mootdx", 5000, True)
    entries = log.entries(2024)
    assert entries.height == 1
    assert entries["dataset"].to_list() == ["bars_daily"]
    assert entries["row_count"].to_list() == [5000]
    assert entries["ok"].to_list() == [True]


def test_record_is_idempotent_on_same_key(warehouse: Warehouse):
    log = IngestLog(warehouse)
    log.record("bars_daily", date(2024, 1, 2), "mootdx", 5000, True)
    log.record("bars_daily", date(2024, 1, 2), "mootdx", 5100, True)
    entries = log.entries(2024)
    assert entries.height == 1
    assert entries["row_count"].to_list() == [5100]


def test_different_providers_are_separate_entries(warehouse: Warehouse):
    log = IngestLog(warehouse)
    log.record("bars_daily", date(2024, 1, 2), "mootdx", 5000, True)
    log.record("bars_daily", date(2024, 1, 2), "akshare", 4900, True)
    assert log.entries(2024).height == 2


def test_failure_is_recorded_with_message(warehouse: Warehouse):
    log = IngestLog(warehouse)
    log.record("bars_daily", date(2024, 1, 2), "mootdx", 0, False, "connection reset")
    entries = log.entries(2024)
    assert entries["ok"].to_list() == [False]
    assert entries["message"].to_list() == ["connection reset"]


def test_entries_on_empty_year_returns_empty_frame(warehouse: Warehouse):
    assert IngestLog(warehouse).entries(2024).height == 0


def test_entries_are_partitioned_by_year(warehouse: Warehouse):
    log = IngestLog(warehouse)
    log.record("bars_daily", date(2023, 12, 29), "mootdx", 1, True)
    log.record("bars_daily", date(2024, 1, 2), "mootdx", 2, True)
    assert log.entries(2023).height == 1
    assert log.entries(2024).height == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/pipeline/test_ingest_log.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.pipeline'`

- [ ] **Step 3: 实现 `src/qsys/data/pipeline/ingest_log.py`**

```python
"""采集日志：记录哪天、哪个源、何时采、成败，供数据问题回溯。"""

from __future__ import annotations

from datetime import date, datetime

import polars as pl

from qsys.data import schema
from qsys.data.storage import Warehouse

_KEY = ["dataset", "dt", "provider"]


class IngestLog:
    def __init__(self, warehouse: Warehouse) -> None:
        self._wh = warehouse

    def _path(self, year: int):
        return self._wh.path_for("ingest_log", year)

    def entries(self, year: int) -> pl.DataFrame:
        path = self._path(year)
        if not path.exists():
            return pl.from_arrow(schema.INGEST_LOG.empty_table())
        return pl.read_parquet(path)

    def record(
        self,
        dataset: str,
        dt: date,
        provider: str,
        row_count: int,
        ok: bool,
        message: str = "",
    ) -> None:
        year = dt.year
        new = pl.DataFrame(
            {
                "dataset": [dataset],
                "dt": [dt],
                "provider": [provider],
                "ingested_at": [datetime.now()],
                "row_count": [row_count],
                "ok": [ok],
                "message": [message],
            },
            schema={
                "dataset": pl.Utf8,
                "dt": pl.Date,
                "provider": pl.Utf8,
                "ingested_at": pl.Datetime("us"),
                "row_count": pl.Int64,
                "ok": pl.Boolean,
                "message": pl.Utf8,
            },
        )
        merged = (
            pl.concat([self.entries(year), new], how="vertical")
            .unique(subset=_KEY, keep="last")
            .sort(_KEY)
        )
        self._wh.write("ingest_log", merged.to_arrow().cast(schema.INGEST_LOG), year=year)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/data/pipeline/test_ingest_log.py -v`
Expected: PASS —— 6 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/pipeline tests/data/pipeline/test_ingest_log.py
git commit -m "feat(data): 采集日志表"
```

---

## Task 9: 标的主表流水线

**Files:**
- Create: `src/qsys/data/pipeline/instruments.py`
- Test: `tests/data/pipeline/test_instruments.py`

**Interfaces:**
- Consumes: `ReferenceProvider`（Task 5）、`Warehouse`（Task 3）、`WarehouseQuery`（Task 4）、`IngestLog`（Task 8）
- Produces: `build_instruments(provider: ReferenceProvider, warehouse: Warehouse) -> int`（返回写入行数）、`alive_on(instruments: pl.DataFrame, day: date) -> pl.DataFrame`（返回该日仍在市的标的）

- [ ] **Step 1: 写失败测试**

Create `tests/data/pipeline/test_instruments.py`:

```python
from datetime import date

import polars as pl

from qsys.data.pipeline.instruments import alive_on, build_instruments
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


class FakeProvider:
    name = "fake"

    def __init__(self, frame: pl.DataFrame) -> None:
        self._frame = frame

    def fetch_instruments(self) -> pl.DataFrame:
        return self._frame

    def fetch_sessions(self, start, end):
        raise NotImplementedError

    def fetch_corporate_actions(self, symbol):
        raise NotImplementedError


def _frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["600036.SH", "000003.SZ", "300750.SZ", "600036.SH"],
            "name": ["招商银行", "PT金田A", "宁德时代", "招商银行"],
            "market": ["SH", "SZ", "SZ", "SH"],
            "list_date": [date(2002, 4, 9), date(1991, 7, 3), date(2018, 6, 11), None],
            "delist_date": [None, date(2002, 6, 14), None, None],
            "board": ["main", "main", "gem", "main"],
        },
        schema={
            "symbol": pl.Utf8,
            "name": pl.Utf8,
            "market": pl.Utf8,
            "list_date": pl.Date,
            "delist_date": pl.Date,
            "board": pl.Utf8,
        },
    )


def test_build_writes_deduplicated_instruments(warehouse: Warehouse):
    count = build_instruments(FakeProvider(_frame()), warehouse)
    assert count == 3
    df = WarehouseQuery(warehouse).instruments()
    assert sorted(df["symbol"].to_list()) == ["000003.SZ", "300750.SZ", "600036.SH"]


def test_build_keeps_delisted_instruments(warehouse: Warehouse):
    build_instruments(FakeProvider(_frame()), warehouse)
    df = WarehouseQuery(warehouse).instruments()
    delisted = df.filter(pl.col("delist_date").is_not_null())
    assert delisted["symbol"].to_list() == ["000003.SZ"]


def test_build_is_idempotent(warehouse: Warehouse):
    build_instruments(FakeProvider(_frame()), warehouse)
    build_instruments(FakeProvider(_frame()), warehouse)
    assert WarehouseQuery(warehouse).instruments().height == 3


def test_dedupe_prefers_row_with_more_information(warehouse: Warehouse):
    build_instruments(FakeProvider(_frame()), warehouse)
    df = WarehouseQuery(warehouse).instruments()
    cmb = df.filter(pl.col("symbol") == "600036.SH")
    assert cmb["list_date"].to_list() == [date(2002, 4, 9)]


def test_alive_on_excludes_not_yet_listed():
    df = _frame().unique(subset=["symbol"], keep="first")
    alive = alive_on(df, date(2010, 1, 4))
    assert "300750.SZ" not in alive["symbol"].to_list()


def test_alive_on_excludes_already_delisted():
    df = _frame().unique(subset=["symbol"], keep="first")
    alive = alive_on(df, date(2010, 1, 4))
    assert "000003.SZ" not in alive["symbol"].to_list()


def test_alive_on_includes_still_listed():
    df = _frame().unique(subset=["symbol"], keep="first")
    alive = alive_on(df, date(2010, 1, 4))
    assert alive["symbol"].to_list() == ["600036.SH"]


def test_alive_on_includes_instrument_alive_on_its_delist_date():
    df = _frame().unique(subset=["symbol"], keep="first")
    alive = alive_on(df, date(2002, 6, 14))
    assert "000003.SZ" in alive["symbol"].to_list()


def test_alive_on_treats_null_list_date_as_always_listed():
    df = pl.DataFrame(
        {
            "symbol": ["600036.SH"],
            "name": ["招商银行"],
            "market": ["SH"],
            "list_date": [None],
            "delist_date": [None],
            "board": ["main"],
        },
        schema={
            "symbol": pl.Utf8,
            "name": pl.Utf8,
            "market": pl.Utf8,
            "list_date": pl.Date,
            "delist_date": pl.Date,
            "board": pl.Utf8,
        },
    )
    assert alive_on(df, date(1990, 1, 1)).height == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/pipeline/test_instruments.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.pipeline.instruments'`

- [ ] **Step 3: 实现 `src/qsys/data/pipeline/instruments.py`**

```python
"""标的主表流水线。

必须保留已退市标的：股票池在历史每一天都要包含当时仍在市的全部股票，
否则幸存者偏差会使回测系统性虚高。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.providers.base import ReferenceProvider
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

COLUMNS = ["symbol", "name", "market", "list_date", "delist_date", "board"]


def build_instruments(provider: ReferenceProvider, warehouse: Warehouse) -> int:
    raw = provider.fetch_instruments().select(COLUMNS)

    # 同一 symbol 可能来自多个来源（在市表 / 退市表），合并时保留信息最全的一行：
    # 按非空字段数降序排序后取第一行。
    informativeness = (
        pl.col("name").is_not_null().cast(pl.Int32)
        + pl.col("list_date").is_not_null().cast(pl.Int32)
        + pl.col("delist_date").is_not_null().cast(pl.Int32)
    )
    merged = (
        raw.with_columns(informativeness.alias("_info"))
        .sort(["symbol", "_info"], descending=[False, True])
        .unique(subset=["symbol"], keep="first")
        .drop("_info")
        .sort("symbol")
        .select(COLUMNS)
    )

    warehouse.write("instruments", merged.to_arrow().cast(schema.INSTRUMENTS))
    log.info("instruments.built", provider=provider.name, rows=merged.height)
    return merged.height


def alive_on(instruments: pl.DataFrame, day: date) -> pl.DataFrame:
    """返回 `day` 当天仍在市的标的（上市日 <= day <= 退市日；null 视为无界）。"""
    return instruments.filter(
        (pl.col("list_date").is_null() | (pl.col("list_date") <= day))
        & (pl.col("delist_date").is_null() | (pl.col("delist_date") >= day))
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/data/pipeline/test_instruments.py -v`
Expected: PASS —— 9 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/pipeline/instruments.py tests/data/pipeline/test_instruments.py
git commit -m "feat(data): 标的主表流水线（含退市股保留与在市判定）"
```

---

## Task 10: 日线采集流水线

**Files:**
- Create: `src/qsys/data/pipeline/bars.py`
- Test: `tests/data/pipeline/test_bars.py`

**Interfaces:**
- Consumes: `BarsProvider`（Task 5）、`Warehouse`（Task 3）、`WarehouseQuery`（Task 4）、`IngestLog`（Task 8）
- Produces: `ingest_daily_bars(provider: BarsProvider, warehouse: Warehouse, symbols: Sequence[str], start: date, end: date) -> int`（返回写入行数；按年分区，与既有数据按 `(symbol, dt)` 合并去重，单个 symbol 抓取失败不中断其余）

- [ ] **Step 1: 写失败测试**

Create `tests/data/pipeline/test_bars.py`:

```python
from datetime import date

import polars as pl

from qsys.data.pipeline.bars import ingest_daily_bars
from qsys.data.pipeline.ingest_log import IngestLog
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

BAR_SCHEMA = {
    "dt": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Int64,
    "amount": pl.Float64,
}


class FakeProvider:
    name = "fake"

    def __init__(self, data: dict[str, pl.DataFrame], failing: set[str] | None = None):
        self._data = data
        self._failing = failing or set()

    def fetch_daily_bars(self, symbol, start, end):
        if symbol in self._failing:
            raise RuntimeError("connection reset")
        frame = self._data.get(symbol, pl.DataFrame(schema=BAR_SCHEMA))
        return frame.filter(pl.col("dt").is_between(start, end))


def _bars(dates: list[date], close: float) -> pl.DataFrame:
    n = len(dates)
    return pl.DataFrame(
        {
            "dt": dates,
            "open": [close] * n,
            "high": [close + 1] * n,
            "low": [close - 1] * n,
            "close": [close] * n,
            "volume": [1000] * n,
            "amount": [close * 1000] * n,
        },
        schema=BAR_SCHEMA,
    )


def test_ingest_writes_bars(warehouse: Warehouse):
    provider = FakeProvider({"600036.SH": _bars([date(2024, 1, 2), date(2024, 1, 3)], 30.0)})
    written = ingest_daily_bars(
        provider, warehouse, ["600036.SH"], date(2024, 1, 1), date(2024, 1, 31)
    )
    assert written == 2
    df = WarehouseQuery(warehouse).daily_bars(None, date(2024, 1, 1), date(2024, 1, 31))
    assert df.height == 2
    assert df["symbol"].to_list() == ["600036.SH", "600036.SH"]


def test_ingest_is_idempotent(warehouse: Warehouse):
    provider = FakeProvider({"600036.SH": _bars([date(2024, 1, 2)], 30.0)})
    ingest_daily_bars(provider, warehouse, ["600036.SH"], date(2024, 1, 1), date(2024, 1, 31))
    ingest_daily_bars(provider, warehouse, ["600036.SH"], date(2024, 1, 1), date(2024, 1, 31))
    assert WarehouseQuery(warehouse).daily_bars(
        None, date(2024, 1, 1), date(2024, 1, 31)
    ).height == 1


def test_ingest_merges_with_existing_data(warehouse: Warehouse):
    ingest_daily_bars(
        FakeProvider({"600036.SH": _bars([date(2024, 1, 2)], 30.0)}),
        warehouse, ["600036.SH"], date(2024, 1, 1), date(2024, 1, 31),
    )
    ingest_daily_bars(
        FakeProvider({"000001.SZ": _bars([date(2024, 1, 2)], 10.0)}),
        warehouse, ["000001.SZ"], date(2024, 1, 1), date(2024, 1, 31),
    )
    df = WarehouseQuery(warehouse).daily_bars(None, date(2024, 1, 1), date(2024, 1, 31))
    assert sorted(df["symbol"].to_list()) == ["000001.SZ", "600036.SH"]


def test_ingest_partitions_by_year(warehouse: Warehouse):
    provider = FakeProvider(
        {"600036.SH": _bars([date(2023, 12, 29), date(2024, 1, 2)], 30.0)}
    )
    ingest_daily_bars(provider, warehouse, ["600036.SH"], date(2023, 1, 1), date(2024, 12, 31))
    assert warehouse.path_for("bars_daily", 2023).exists()
    assert warehouse.path_for("bars_daily", 2024).exists()


def test_ingest_records_success_in_log(warehouse: Warehouse):
    provider = FakeProvider({"600036.SH": _bars([date(2024, 1, 2)], 30.0)})
    ingest_daily_bars(provider, warehouse, ["600036.SH"], date(2024, 1, 1), date(2024, 1, 31))
    entries = IngestLog(warehouse).entries(2024)
    assert entries.filter(pl.col("ok")).height >= 1


def test_failing_symbol_does_not_abort_others(warehouse: Warehouse):
    provider = FakeProvider(
        {"000001.SZ": _bars([date(2024, 1, 2)], 10.0)}, failing={"600036.SH"}
    )
    written = ingest_daily_bars(
        provider, warehouse, ["600036.SH", "000001.SZ"], date(2024, 1, 1), date(2024, 1, 31)
    )
    assert written == 1
    df = WarehouseQuery(warehouse).daily_bars(None, date(2024, 1, 1), date(2024, 1, 31))
    assert df["symbol"].to_list() == ["000001.SZ"]


def test_failure_is_recorded_in_log(warehouse: Warehouse):
    provider = FakeProvider({}, failing={"600036.SH"})
    ingest_daily_bars(provider, warehouse, ["600036.SH"], date(2024, 1, 1), date(2024, 1, 31))
    entries = IngestLog(warehouse).entries(2024)
    failed = entries.filter(~pl.col("ok"))
    assert failed.height == 1
    assert "connection reset" in failed["message"].to_list()[0]


def test_ingest_with_no_data_writes_nothing(warehouse: Warehouse):
    written = ingest_daily_bars(
        FakeProvider({}), warehouse, ["600036.SH"], date(2024, 1, 1), date(2024, 1, 31)
    )
    assert written == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/pipeline/test_bars.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.pipeline.bars'`

- [ ] **Step 3: 实现 `src/qsys/data/pipeline/bars.py`**

```python
"""日线采集流水线：增量、幂等、单点失败不中断。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.pipeline.ingest_log import IngestLog
from qsys.data.providers.base import BarsProvider
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

BAR_COLUMNS = ["symbol", "dt", "open", "high", "low", "close", "volume", "amount"]


def ingest_daily_bars(
    provider: BarsProvider,
    warehouse: Warehouse,
    symbols: Sequence[str],
    start: date,
    end: date,
) -> int:
    ingest_log = IngestLog(warehouse)
    collected: list[pl.DataFrame] = []

    for symbol in symbols:
        try:
            frame = provider.fetch_daily_bars(symbol, start, end)
        except Exception as exc:  # 单个标的失败不影响其余
            log.warning("bars.fetch_failed", symbol=symbol, error=str(exc))
            ingest_log.record("bars_daily", end, provider.name, 0, False, str(exc))
            continue

        if frame.height == 0:
            continue
        collected.append(frame.with_columns(pl.lit(symbol).alias("symbol")).select(BAR_COLUMNS))

    if not collected:
        return 0

    fresh = pl.concat(collected, how="vertical")
    written = 0
    query = WarehouseQuery(warehouse)

    for year in sorted({d.year for d in fresh["dt"].to_list()}):
        year_start, year_end = date(year, 1, 1), date(year, 12, 31)
        incoming = fresh.filter(pl.col("dt").is_between(year_start, year_end))
        existing = query.daily_bars(None, year_start, year_end)
        merged = (
            pl.concat([existing, incoming], how="vertical")
            .unique(subset=["symbol", "dt"], keep="last")
            .sort(["symbol", "dt"])
            .select(BAR_COLUMNS)
        )
        warehouse.write("bars_daily", merged.to_arrow().cast(schema.BARS_DAILY), year=year)
        written += incoming.height
        ingest_log.record("bars_daily", year_end, provider.name, incoming.height, True)

    log.info("bars.ingested", provider=provider.name, rows=written, symbols=len(symbols))
    return written
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/data/pipeline/test_bars.py -v`
Expected: PASS —— 8 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/pipeline/bars.py tests/data/pipeline/test_bars.py
git commit -m "feat(data): 日线采集流水线（增量、幂等、失败隔离）"
```

---

## Task 11: 复权因子计算

后复权累计因子。除权日单日因子 = 前收盘价 ÷ 除权除息参考价，其中

```
除权参考价 = (前收盘价 − 每股现金红利 + 配股价 × 每股配股数) ÷ (1 + 每股送转股数 + 每股配股数)
cum_factor(t) = ∏ 单日因子(d)，d 为所有 ex_date ≤ t 的除权日
后复权价(t) = 不复权价(t) × cum_factor(t)
```

**Files:**
- Create: `src/qsys/data/pipeline/adjust.py`
- Test: `tests/data/pipeline/test_adjust.py`

**Interfaces:**
- Consumes: `Warehouse`（Task 3）、`WarehouseQuery`（Task 4）、`schema.ADJ_FACTORS`（Task 3）
- Produces: `daily_factor(prev_close: float, cash: float, stock: float, rights: float, rights_price: float) -> float`、`build_adj_factors(warehouse: Warehouse) -> int`（读 `bars_daily` + `corporate_actions`，写 `adj_factors`）

- [ ] **Step 1: 写失败测试**

Create `tests/data/pipeline/test_adjust.py`:

```python
from datetime import date

import polars as pl
import pyarrow as pa
import pytest

from qsys.data import schema
from qsys.data.pipeline.adjust import build_adj_factors, daily_factor
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse


def test_no_action_factor_is_one():
    assert daily_factor(10.0, 0.0, 0.0, 0.0, 0.0) == pytest.approx(1.0)


def test_ten_for_ten_bonus_doubles_factor():
    # 10 送 10：每股送 1 股，除权参考价 = 10 / 2 = 5，因子 = 10 / 5 = 2
    assert daily_factor(10.0, 0.0, 1.0, 0.0, 0.0) == pytest.approx(2.0)


def test_cash_dividend_factor():
    # 前收 10 元，每股派 0.5 元：参考价 9.5，因子 = 10 / 9.5
    assert daily_factor(10.0, 0.5, 0.0, 0.0, 0.0) == pytest.approx(10.0 / 9.5)


def test_rights_issue_factor():
    # 前收 10，每股配 0.5 股，配股价 6：参考价 = (10 + 3) / 1.5 = 8.6666...
    assert daily_factor(10.0, 0.0, 0.0, 0.5, 6.0) == pytest.approx(10.0 / (13.0 / 1.5))


def test_combined_cash_and_bonus():
    # 前收 10，派 0.5，送 1 股：参考价 = (10 - 0.5) / 2 = 4.75，因子 = 10 / 4.75
    assert daily_factor(10.0, 0.5, 1.0, 0.0, 0.0) == pytest.approx(10.0 / 4.75)


def test_zero_reference_price_raises():
    with pytest.raises(ValueError, match="non-positive reference price"):
        daily_factor(1.0, 1.0, 0.0, 0.0, 0.0)


def _seed(warehouse: Warehouse) -> None:
    """600036 在 2024-01-04 除权：10 送 10。除权前收 20，除权后 10。"""
    warehouse.write(
        "bars_daily",
        pa.table(
            {
                "symbol": ["600036.SH"] * 4,
                "dt": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)],
                "open": [19.0, 19.5, 10.0, 10.1],
                "high": [20.0, 20.5, 10.5, 10.6],
                "low": [18.5, 19.0, 9.8, 9.9],
                "close": [19.5, 20.0, 10.0, 10.2],
                "volume": [1000, 1000, 2000, 2000],
                "amount": [19500.0, 20000.0, 20000.0, 20400.0],
            },
            schema=schema.BARS_DAILY,
        ),
        year=2024,
    )
    warehouse.write(
        "corporate_actions",
        pa.table(
            {
                "symbol": ["600036.SH"],
                "ex_date": [date(2024, 1, 4)],
                "cash_per_share": [0.0],
                "stock_per_share": [1.0],
                "rights_per_share": [0.0],
                "rights_price": [0.0],
            },
            schema=schema.CORPORATE_ACTIONS,
        ),
    )


def test_build_writes_one_row_per_bar(warehouse: Warehouse):
    _seed(warehouse)
    assert build_adj_factors(warehouse) == 4
    assert WarehouseQuery(warehouse).adj_factors().height == 4


def test_factor_is_one_before_ex_date(warehouse: Warehouse):
    _seed(warehouse)
    build_adj_factors(warehouse)
    df = WarehouseQuery(warehouse).adj_factors().sort("dt")
    assert df["cum_factor"].to_list()[:2] == [pytest.approx(1.0), pytest.approx(1.0)]


def test_factor_doubles_from_ex_date(warehouse: Warehouse):
    _seed(warehouse)
    build_adj_factors(warehouse)
    df = WarehouseQuery(warehouse).adj_factors().sort("dt")
    assert df["cum_factor"].to_list()[2:] == [pytest.approx(2.0), pytest.approx(2.0)]


def test_adjusted_close_is_continuous_across_ex_date(warehouse: Warehouse):
    """除权前后后复权收盘价不应跳变：20.0 → 10.0 × 2 = 20.0。"""
    _seed(warehouse)
    build_adj_factors(warehouse)
    q = WarehouseQuery(warehouse)
    bars = q.daily_bars(None, date(2024, 1, 1), date(2024, 12, 31))
    factors = q.adj_factors()
    joined = bars.join(factors, on=["symbol", "dt"]).sort("dt")
    adjusted = (joined["close"] * joined["cum_factor"]).to_list()
    assert adjusted[1] == pytest.approx(20.0)
    assert adjusted[2] == pytest.approx(20.0)


def test_build_is_idempotent(warehouse: Warehouse):
    _seed(warehouse)
    build_adj_factors(warehouse)
    build_adj_factors(warehouse)
    assert WarehouseQuery(warehouse).adj_factors().height == 4


def test_build_on_empty_warehouse_returns_zero(warehouse: Warehouse):
    assert build_adj_factors(warehouse) == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/pipeline/test_adjust.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.pipeline.adjust'`

- [ ] **Step 3: 实现 `src/qsys/data/pipeline/adjust.py`**

```python
"""复权因子计算。

行情事实表存不复权价格永不重写，复权因子单独存储并可随时重算——
这样任意历史时点的复权价都能精确重现，避免前复权序列落盘后腐烂。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

_EPOCH = date(1990, 1, 1)
_HORIZON = date(2100, 1, 1)


def daily_factor(
    prev_close: float,
    cash: float,
    stock: float,
    rights: float,
    rights_price: float,
) -> float:
    """除权日单日因子 = 前收盘价 / 除权除息参考价。"""
    reference = (prev_close - cash + rights_price * rights) / (1.0 + stock + rights)
    if reference <= 0:
        raise ValueError(f"non-positive reference price: {reference}")
    return prev_close / reference


def build_adj_factors(warehouse: Warehouse) -> int:
    query = WarehouseQuery(warehouse)
    bars = query.daily_bars(None, _EPOCH, _HORIZON)
    if bars.height == 0:
        return 0

    actions = query.corporate_actions()
    bars = bars.sort(["symbol", "dt"]).with_columns(
        pl.col("close").shift(1).over("symbol").alias("prev_close")
    )

    if actions.height == 0:
        result = bars.select(
            "symbol", "dt", pl.lit(1.0).alias("cum_factor")
        )
    else:
        joined = bars.join(
            actions, left_on=["symbol", "dt"], right_on=["symbol", "ex_date"], how="left"
        ).fill_null(0.0)

        ratios: list[float] = []
        for row in joined.iter_rows(named=True):
            has_action = (
                row["cash_per_share"] or row["stock_per_share"] or row["rights_per_share"]
            )
            if not has_action or row["prev_close"] is None:
                ratios.append(1.0)
                continue
            ratios.append(
                daily_factor(
                    row["prev_close"],
                    row["cash_per_share"],
                    row["stock_per_share"],
                    row["rights_per_share"],
                    row["rights_price"],
                )
            )

        result = (
            joined.with_columns(pl.Series("ratio", ratios))
            .with_columns(pl.col("ratio").cum_prod().over("symbol").alias("cum_factor"))
            .select("symbol", "dt", "cum_factor")
        )

    result = result.sort(["symbol", "dt"])
    warehouse.write("adj_factors", result.to_arrow().cast(schema.ADJ_FACTORS))
    log.info("adj_factors.built", rows=result.height)
    return result.height
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/data/pipeline/test_adjust.py -v`
Expected: PASS —— 12 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/pipeline/adjust.py tests/data/pipeline/test_adjust.py
git commit -m "feat(data): 复权因子计算（后复权累计因子）"
```

---

## Task 12: 质量校验规则 1–3

**Files:**
- Create: `src/qsys/data/quality/__init__.py`
- Create: `src/qsys/data/quality/rules.py`
- Test: `tests/data/quality/test_rules.py`

**Interfaces:**
- Consumes: `TradingCalendar`（Task 2）
- Produces: `Severity` (StrEnum: `BLOCKING`/`WARNING`)、`Violation` (frozen dataclass: `rule: str`, `severity: Severity`, `symbol: str`, `dt: date`, `detail: str`)、`check_completeness(bars, calendar, expected_symbols, start, end) -> list[Violation]`、`check_price_limits(bars, boards) -> list[Violation]`、`check_internal_consistency(bars) -> list[Violation]`

涨跌停阈值：主板/中小板 10%，创业板(`gem`)与科创板(`star`) 20%，北交所(`bse`) 30%。ST 判定在一期不可得，故越界规则一律为 `WARNING` 而非 `BLOCKING`（ST 股 5% 限制会产生误报）。

- [ ] **Step 1: 写失败测试**

Create `tests/data/quality/test_rules.py`:

```python
from datetime import date

import polars as pl

from qsys.core.calendar import AShareCalendar
from qsys.data.quality.rules import (
    Severity,
    check_completeness,
    check_internal_consistency,
    check_price_limits,
)

SESSIONS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
CAL = AShareCalendar(SESSIONS)

BAR_SCHEMA = {
    "symbol": pl.Utf8,
    "dt": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Int64,
    "amount": pl.Float64,
}


def _bars(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=BAR_SCHEMA)


def _row(dt, close, *, symbol="600036.SH", open_=None, high=None, low=None, volume=1000):
    o = close if open_ is None else open_
    return {
        "symbol": symbol,
        "dt": dt,
        "open": o,
        "high": close if high is None else high,
        "low": close if low is None else low,
        "close": close,
        "volume": volume,
        "amount": close * volume,
    }


def test_completeness_flags_missing_session():
    bars = _bars([_row(date(2024, 1, 2), 10.0), _row(date(2024, 1, 4), 10.0)])
    v = check_completeness(bars, CAL, ["600036.SH"], date(2024, 1, 2), date(2024, 1, 4))
    assert [x.dt for x in v] == [date(2024, 1, 3)]
    assert v[0].severity is Severity.BLOCKING
    assert v[0].rule == "completeness"


def test_completeness_passes_when_all_sessions_present():
    bars = _bars([_row(d, 10.0) for d in SESSIONS])
    assert check_completeness(bars, CAL, ["600036.SH"], date(2024, 1, 2), date(2024, 1, 4)) == []


def test_completeness_checks_each_expected_symbol():
    bars = _bars([_row(d, 10.0) for d in SESSIONS])
    v = check_completeness(
        bars, CAL, ["600036.SH", "000001.SZ"], date(2024, 1, 2), date(2024, 1, 4)
    )
    assert {x.symbol for x in v} == {"000001.SZ"}
    assert len(v) == 3


def test_price_limit_flags_main_board_over_ten_percent():
    bars = _bars([_row(date(2024, 1, 2), 10.0), _row(date(2024, 1, 3), 11.5)])
    v = check_price_limits(bars, {"600036.SH": "main"})
    assert [x.dt for x in v] == [date(2024, 1, 3)]
    assert v[0].severity is Severity.WARNING
    assert v[0].rule == "price_limit"


def test_price_limit_allows_main_board_exactly_ten_percent():
    bars = _bars([_row(date(2024, 1, 2), 10.0), _row(date(2024, 1, 3), 11.0)])
    assert check_price_limits(bars, {"600036.SH": "main"}) == []


def test_price_limit_allows_twenty_percent_on_gem():
    bars = _bars(
        [
            _row(date(2024, 1, 2), 10.0, symbol="300750.SZ"),
            _row(date(2024, 1, 3), 11.5, symbol="300750.SZ"),
        ]
    )
    assert check_price_limits(bars, {"300750.SZ": "gem"}) == []


def test_price_limit_allows_thirty_percent_on_bse():
    bars = _bars(
        [
            _row(date(2024, 1, 2), 10.0, symbol="830799.BJ"),
            _row(date(2024, 1, 3), 12.9, symbol="830799.BJ"),
        ]
    )
    assert check_price_limits(bars, {"830799.BJ": "bse"}) == []


def test_price_limit_skips_first_bar_of_each_symbol():
    bars = _bars([_row(date(2024, 1, 2), 10.0)])
    assert check_price_limits(bars, {"600036.SH": "main"}) == []


def test_consistency_flags_high_below_close():
    bars = _bars([_row(date(2024, 1, 2), 10.0, high=9.0, low=8.0)])
    v = check_internal_consistency(bars)
    assert [x.rule for x in v] == ["ohlc_order"]
    assert v[0].severity is Severity.BLOCKING


def test_consistency_flags_low_above_open():
    bars = _bars([_row(date(2024, 1, 2), 10.0, open_=9.0, high=11.0, low=9.5)])
    assert [x.rule for x in check_internal_consistency(bars)] == ["ohlc_order"]


def test_consistency_flags_nonpositive_price():
    bars = _bars([_row(date(2024, 1, 2), 0.0, high=0.0, low=0.0)])
    assert "non_positive_price" in {x.rule for x in check_internal_consistency(bars)}


def test_consistency_flags_negative_volume():
    bars = _bars([_row(date(2024, 1, 2), 10.0, volume=-1)])
    assert "negative_volume" in {x.rule for x in check_internal_consistency(bars)}


def test_consistency_passes_on_clean_bar():
    bars = _bars([_row(date(2024, 1, 2), 10.0, open_=9.8, high=10.2, low=9.7)])
    assert check_internal_consistency(bars) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/quality/test_rules.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.quality'`

- [ ] **Step 3: 实现 `src/qsys/data/quality/rules.py`**

```python
"""数据质量校验规则 1–3。

规则只做判定，不修数据。规则 4–6（交叉源比对、复权自洽、财务勾稽）属二期范围。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

import polars as pl

from qsys.core.calendar import TradingCalendar

# 各板块日涨跌幅上限。ST 股的 5% 限制在一期不可得，故越界规则仅告警不阻断。
LIMIT_BY_BOARD = {"main": 0.10, "gem": 0.20, "star": 0.20, "bse": 0.30}
_TOLERANCE = 1e-6


class Severity(StrEnum):
    BLOCKING = "blocking"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Violation:
    rule: str
    severity: Severity
    symbol: str
    dt: date
    detail: str


def check_completeness(
    bars: pl.DataFrame,
    calendar: TradingCalendar,
    expected_symbols: Sequence[str],
    start: date,
    end: date,
) -> list[Violation]:
    """规则 1：区间内每个交易日、每只预期标的都应有 bar。"""
    sessions = calendar.sessions_in_range(start, end)
    violations: list[Violation] = []
    for symbol in expected_symbols:
        present = set(bars.filter(pl.col("symbol") == symbol)["dt"].to_list())
        for session in sessions:
            if session not in present:
                violations.append(
                    Violation(
                        rule="completeness",
                        severity=Severity.BLOCKING,
                        symbol=symbol,
                        dt=session,
                        detail="missing bar (needs a suspension record to explain)",
                    )
                )
    return violations


def check_price_limits(bars: pl.DataFrame, boards: dict[str, str]) -> list[Violation]:
    """规则 2：日涨跌幅不得超过所属板块上限。"""
    if bars.height == 0:
        return []
    frame = bars.sort(["symbol", "dt"]).with_columns(
        pl.col("close").shift(1).over("symbol").alias("prev_close")
    )
    violations: list[Violation] = []
    for row in frame.iter_rows(named=True):
        prev = row["prev_close"]
        if prev is None or prev <= 0:
            continue
        change = abs(row["close"] / prev - 1.0)
        limit = LIMIT_BY_BOARD.get(boards.get(row["symbol"], "main"), 0.10)
        if change > limit + _TOLERANCE:
            violations.append(
                Violation(
                    rule="price_limit",
                    severity=Severity.WARNING,
                    symbol=row["symbol"],
                    dt=row["dt"],
                    detail=f"change {change:.4f} exceeds board limit {limit:.2f}",
                )
            )
    return violations


def check_internal_consistency(bars: pl.DataFrame) -> list[Violation]:
    """规则 3：OHLC 大小关系、价格为正、成交量非负。"""
    violations: list[Violation] = []
    for row in bars.iter_rows(named=True):
        symbol, dt = row["symbol"], row["dt"]
        o, h, l, c = row["open"], row["high"], row["low"], row["close"]

        if min(o, h, l, c) <= 0:
            violations.append(
                Violation("non_positive_price", Severity.BLOCKING, symbol, dt,
                          f"ohlc=({o}, {h}, {l}, {c})")
            )
        elif not (l <= min(o, c) and max(o, c) <= h and l <= h):
            violations.append(
                Violation("ohlc_order", Severity.BLOCKING, symbol, dt,
                          f"ohlc=({o}, {h}, {l}, {c})")
            )

        if row["volume"] < 0:
            violations.append(
                Violation("negative_volume", Severity.BLOCKING, symbol, dt,
                          f"volume={row['volume']}")
            )
    return violations
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/data/quality/test_rules.py -v`
Expected: PASS —— 13 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/quality tests/data/quality/test_rules.py
git commit -m "feat(data): 质量校验规则 1-3（完整性/越界/内部一致性）"
```

---

## Task 13: 质量报告与 blocking 闸门

**Files:**
- Create: `src/qsys/data/quality/report.py`
- Test: `tests/data/quality/test_report.py`

**Interfaces:**
- Consumes: `Violation`、`Severity`、三条 check 函数（Task 12）、`TradingCalendar`（Task 2）、`WarehouseQuery`（Task 4）
- Produces: `QualityReport` (frozen dataclass: `violations: tuple[Violation, ...]`, 属性 `is_blocked: bool`、`blocking: tuple[Violation, ...]`、`warnings: tuple[Violation, ...]`、方法 `summary() -> str`)、`run_quality_checks(query, calendar, expected_symbols, start, end) -> QualityReport`、`assert_usable(report: QualityReport) -> None`（有 blocking 时抛 `DataQualityError`）、`DataQualityError`

- [ ] **Step 1: 写失败测试**

Create `tests/data/quality/test_report.py`:

```python
from datetime import date

import pyarrow as pa
import pytest

from qsys.core.calendar import AShareCalendar
from qsys.data import schema
from qsys.data.quality.report import (
    DataQualityError,
    QualityReport,
    assert_usable,
    run_quality_checks,
)
from qsys.data.quality.rules import Severity, Violation
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

SESSIONS = [date(2024, 1, 2), date(2024, 1, 3)]
CAL = AShareCalendar(SESSIONS)


def _seed(warehouse: Warehouse, *, complete: bool = True) -> None:
    days = SESSIONS if complete else SESSIONS[:1]
    n = len(days)
    warehouse.write(
        "bars_daily",
        pa.table(
            {
                "symbol": ["600036.SH"] * n,
                "dt": days,
                "open": [10.0] * n,
                "high": [10.5] * n,
                "low": [9.8] * n,
                "close": [10.2] * n,
                "volume": [1000] * n,
                "amount": [10200.0] * n,
            },
            schema=schema.BARS_DAILY,
        ),
        year=2024,
    )
    warehouse.write(
        "instruments",
        pa.table(
            {
                "symbol": ["600036.SH"],
                "name": ["招商银行"],
                "market": ["SH"],
                "list_date": [date(2002, 4, 9)],
                "delist_date": [None],
                "board": ["main"],
            },
            schema=schema.INSTRUMENTS,
        ),
    )


def test_clean_data_produces_no_violations(warehouse: Warehouse):
    _seed(warehouse)
    report = run_quality_checks(
        WarehouseQuery(warehouse), CAL, ["600036.SH"], SESSIONS[0], SESSIONS[-1]
    )
    assert report.violations == ()
    assert report.is_blocked is False


def test_missing_session_blocks(warehouse: Warehouse):
    _seed(warehouse, complete=False)
    report = run_quality_checks(
        WarehouseQuery(warehouse), CAL, ["600036.SH"], SESSIONS[0], SESSIONS[-1]
    )
    assert report.is_blocked is True
    assert [v.rule for v in report.blocking] == ["completeness"]


def test_report_separates_warnings_from_blocking():
    report = QualityReport(
        violations=(
            Violation("completeness", Severity.BLOCKING, "600036.SH", SESSIONS[0], "x"),
            Violation("price_limit", Severity.WARNING, "600036.SH", SESSIONS[1], "y"),
        )
    )
    assert len(report.blocking) == 1
    assert len(report.warnings) == 1
    assert report.is_blocked is True


def test_summary_lists_counts_by_rule():
    report = QualityReport(
        violations=(
            Violation("completeness", Severity.BLOCKING, "600036.SH", SESSIONS[0], "x"),
            Violation("completeness", Severity.BLOCKING, "000001.SZ", SESSIONS[0], "x"),
            Violation("price_limit", Severity.WARNING, "600036.SH", SESSIONS[1], "y"),
        )
    )
    text = report.summary()
    assert "completeness" in text
    assert "2" in text
    assert "price_limit" in text


def test_assert_usable_raises_on_blocking():
    report = QualityReport(
        violations=(Violation("completeness", Severity.BLOCKING, "600036.SH", SESSIONS[0], "x"),)
    )
    with pytest.raises(DataQualityError, match="completeness"):
        assert_usable(report)


def test_assert_usable_passes_on_warnings_only():
    report = QualityReport(
        violations=(Violation("price_limit", Severity.WARNING, "600036.SH", SESSIONS[0], "y"),)
    )
    assert_usable(report)  # 不抛异常


def test_assert_usable_passes_on_clean_report():
    assert_usable(QualityReport(violations=()))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/quality/test_report.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.quality.report'`

- [ ] **Step 3: 实现 `src/qsys/data/quality/report.py`**

```python
"""质量报告与 blocking 闸门。

校验不过的数据不许进回测——`assert_usable()` 是这条纪律的执行点。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import structlog

from qsys.core.calendar import TradingCalendar
from qsys.data.quality.rules import (
    Severity,
    Violation,
    check_completeness,
    check_internal_consistency,
    check_price_limits,
)
from qsys.data.query import WarehouseQuery

log = structlog.get_logger(__name__)


class DataQualityError(RuntimeError):
    """存在 blocking 级问题，数据不得进入回测可用集。"""


@dataclass(frozen=True, slots=True)
class QualityReport:
    violations: tuple[Violation, ...]

    @property
    def blocking(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.severity is Severity.BLOCKING)

    @property
    def warnings(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.severity is Severity.WARNING)

    @property
    def is_blocked(self) -> bool:
        return len(self.blocking) > 0

    def summary(self) -> str:
        if not self.violations:
            return "quality: clean (0 violations)"
        counts = Counter((v.rule, v.severity.value) for v in self.violations)
        lines = [
            f"quality: {len(self.blocking)} blocking, {len(self.warnings)} warning"
        ]
        for (rule, severity), n in sorted(counts.items()):
            lines.append(f"  {rule} [{severity}]: {n}")
        return "\n".join(lines)


def run_quality_checks(
    query: WarehouseQuery,
    calendar: TradingCalendar,
    expected_symbols: Sequence[str],
    start: date,
    end: date,
) -> QualityReport:
    bars = query.daily_bars(list(expected_symbols), start, end)
    instruments = query.instruments()
    boards = dict(zip(instruments["symbol"].to_list(), instruments["board"].to_list()))

    violations = [
        *check_completeness(bars, calendar, expected_symbols, start, end),
        *check_price_limits(bars, boards),
        *check_internal_consistency(bars),
    ]
    report = QualityReport(violations=tuple(violations))
    log.info(
        "quality.checked",
        start=str(start),
        end=str(end),
        blocking=len(report.blocking),
        warnings=len(report.warnings),
    )
    return report


def assert_usable(report: QualityReport) -> None:
    if report.is_blocked:
        rules = sorted({v.rule for v in report.blocking})
        raise DataQualityError(
            f"{len(report.blocking)} blocking violations: {', '.join(rules)}"
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/data/quality/test_report.py -v`
Expected: PASS —— 7 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/quality/report.py tests/data/quality/test_report.py
git commit -m "feat(data): 质量报告与 blocking 闸门"
```

---

## Task 14: `as_of` PIT 契约（HistoricalFeed）

本任务是全系统地基。日线 bar 的可见时刻 = 该交易日的收盘时刻（`calendar.close_time()`）；`feed.get()` 只返回可见时刻 ≤ `as_of` 的数据。数据层不得再暴露任何无 `as_of` 的历史数据读取函数。

**Files:**
- Create: `src/qsys/data/feed.py`
- Test: `tests/data/test_feed.py`

**Interfaces:**
- Consumes: `WarehouseQuery`（Task 4）、`TradingCalendar`（Task 2）
- Produces: `Adjustment` (StrEnum: `NONE`/`BACKWARD`/`FORWARD`)、`DataFeed` (ABC: `get(...)`)、`HistoricalFeed(query, calendar)` 的 `get(symbols: Sequence[str], start: date, end: date, *, as_of: datetime, adjustment: Adjustment = Adjustment.NONE) -> pl.DataFrame`

- [ ] **Step 1: 写失败测试**

Create `tests/data/test_feed.py`:

```python
from datetime import date, datetime

import pyarrow as pa
import pytest

from qsys.core.calendar import AShareCalendar
from qsys.data import schema
from qsys.data.feed import Adjustment, HistoricalFeed
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

SESSIONS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]
CAL = AShareCalendar(SESSIONS)


@pytest.fixture
def feed(warehouse: Warehouse) -> HistoricalFeed:
    """600036 在 2024-01-04 除权 10 送 10：不复权收盘 19.5 / 20.0 / 10.0 / 10.2。"""
    warehouse.write(
        "bars_daily",
        pa.table(
            {
                "symbol": ["600036.SH"] * 4,
                "dt": SESSIONS,
                "open": [19.0, 19.5, 10.0, 10.1],
                "high": [20.0, 20.5, 10.5, 10.6],
                "low": [18.5, 19.0, 9.8, 9.9],
                "close": [19.5, 20.0, 10.0, 10.2],
                "volume": [1000, 1000, 2000, 2000],
                "amount": [19500.0, 20000.0, 20000.0, 20400.0],
            },
            schema=schema.BARS_DAILY,
        ),
        year=2024,
    )
    warehouse.write(
        "adj_factors",
        pa.table(
            {
                "symbol": ["600036.SH"] * 4,
                "dt": SESSIONS,
                "cum_factor": [1.0, 1.0, 2.0, 2.0],
            },
            schema=schema.ADJ_FACTORS,
        ),
    )
    return HistoricalFeed(WarehouseQuery(warehouse), CAL)


def test_as_of_after_close_includes_that_day(feed):
    df = feed.get(
        ["600036.SH"], SESSIONS[0], SESSIONS[-1], as_of=datetime(2024, 1, 3, 15, 0)
    )
    assert df["dt"].to_list() == [date(2024, 1, 2), date(2024, 1, 3)]


def test_as_of_before_close_excludes_that_day(feed):
    """PIT 反向测试：盘中不得看到当日日线。"""
    df = feed.get(
        ["600036.SH"], SESSIONS[0], SESSIONS[-1], as_of=datetime(2024, 1, 3, 9, 30)
    )
    assert df["dt"].to_list() == [date(2024, 1, 2)]


def test_as_of_cannot_see_the_future(feed):
    """偷看未来的策略必须拿不到数据。"""
    df = feed.get(
        ["600036.SH"], SESSIONS[0], SESSIONS[-1], as_of=datetime(2024, 1, 2, 15, 0)
    )
    assert date(2024, 1, 3) not in df["dt"].to_list()
    assert df.height == 1


def test_as_of_before_all_data_returns_empty(feed):
    df = feed.get(
        ["600036.SH"], SESSIONS[0], SESSIONS[-1], as_of=datetime(2024, 1, 1, 15, 0)
    )
    assert df.height == 0
    assert "close" in df.columns


def test_end_beyond_as_of_is_clipped_not_an_error(feed):
    df = feed.get(
        ["600036.SH"], SESSIONS[0], date(2030, 1, 1), as_of=datetime(2024, 1, 3, 15, 0)
    )
    assert df["dt"].to_list()[-1] == date(2024, 1, 3)


def test_no_adjustment_returns_raw_prices(feed):
    df = feed.get(["600036.SH"], SESSIONS[0], SESSIONS[-1], as_of=datetime(2024, 1, 5, 15, 0))
    assert df["close"].to_list() == [19.5, 20.0, 10.0, 10.2]


def test_backward_adjustment_is_continuous_across_ex_date(feed):
    df = feed.get(
        ["600036.SH"], SESSIONS[0], SESSIONS[-1],
        as_of=datetime(2024, 1, 5, 15, 0), adjustment=Adjustment.BACKWARD,
    )
    closes = df["close"].to_list()
    assert closes[1] == pytest.approx(20.0)
    assert closes[2] == pytest.approx(20.0)


def test_forward_adjustment_anchors_on_last_visible_bar(feed):
    """前复权以 as_of 时可见的最后一根 bar 为基准，其价格保持不变。"""
    df = feed.get(
        ["600036.SH"], SESSIONS[0], SESSIONS[-1],
        as_of=datetime(2024, 1, 5, 15, 0), adjustment=Adjustment.FORWARD,
    )
    closes = df["close"].to_list()
    assert closes[-1] == pytest.approx(10.2)
    assert closes[1] == pytest.approx(10.0)


def test_adjustment_scales_ohlc_but_not_volume(feed):
    df = feed.get(
        ["600036.SH"], SESSIONS[0], SESSIONS[-1],
        as_of=datetime(2024, 1, 5, 15, 0), adjustment=Adjustment.BACKWARD,
    )
    assert df["open"].to_list()[0] == pytest.approx(19.0)
    assert df["volume"].to_list() == [1000, 1000, 2000, 2000]


def test_get_requires_as_of_keyword(feed):
    with pytest.raises(TypeError):
        feed.get(["600036.SH"], SESSIONS[0], SESSIONS[-1])


def test_unknown_symbol_returns_empty_frame(feed):
    df = feed.get(["999999.SH"], SESSIONS[0], SESSIONS[-1], as_of=datetime(2024, 1, 5, 15, 0))
    assert df.height == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/test_feed.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.feed'`

- [ ] **Step 3: 实现 `src/qsys/data/feed.py`**

```python
"""PIT 数据访问契约。

日线 bar 的可见时刻 = 该交易日的收盘时刻。`get()` 只返回可见时刻 <= as_of
的数据，使未来函数在 API 层面不可能发生，而非依赖策略作者自觉。

数据层不得再暴露任何无 as_of 的历史数据读取函数。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date, datetime
from enum import StrEnum

import polars as pl

from qsys.core.calendar import TradingCalendar
from qsys.data.query import WarehouseQuery

PRICE_COLUMNS = ("open", "high", "low", "close")


class Adjustment(StrEnum):
    NONE = "none"
    BACKWARD = "backward"  # 后复权
    FORWARD = "forward"  # 前复权，以 as_of 时可见的最后一根 bar 为基准


class DataFeed(ABC):
    @abstractmethod
    def get(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
        *,
        as_of: datetime,
        adjustment: Adjustment = Adjustment.NONE,
    ) -> pl.DataFrame: ...


class HistoricalFeed(DataFeed):
    def __init__(self, query: WarehouseQuery, calendar: TradingCalendar) -> None:
        self._query = query
        self._calendar = calendar

    def _last_visible_session(self, as_of: datetime) -> date | None:
        """as_of 时刻已收盘的最后一个交易日。"""
        day = as_of.date()
        if self._calendar.is_trading_day(day) and as_of >= self._calendar.close_time(day):
            return day
        try:
            return self._calendar.prev_session(day)
        except ValueError:
            return None

    def get(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
        *,
        as_of: datetime,
        adjustment: Adjustment = Adjustment.NONE,
    ) -> pl.DataFrame:
        visible_through = self._last_visible_session(as_of)
        if visible_through is None:
            return self._query.daily_bars(symbols, start, start).clear()

        effective_end = min(end, visible_through)
        if effective_end < start:
            return self._query.daily_bars(symbols, start, start).clear()

        bars = self._query.daily_bars(symbols, start, effective_end)
        if adjustment is Adjustment.NONE or bars.height == 0:
            return bars

        factors = self._query.adj_factors(symbols)
        if factors.height == 0:
            return bars

        joined = bars.join(factors, on=["symbol", "dt"], how="left").with_columns(
            pl.col("cum_factor").fill_null(1.0)
        )

        if adjustment is Adjustment.FORWARD:
            anchor = (
                joined.group_by("symbol")
                .agg(pl.col("cum_factor").sort_by("dt").last().alias("anchor"))
            )
            joined = joined.join(anchor, on="symbol", how="left").with_columns(
                (pl.col("cum_factor") / pl.col("anchor")).alias("cum_factor")
            )

        return (
            joined.with_columns(
                [(pl.col(c) * pl.col("cum_factor")).alias(c) for c in PRICE_COLUMNS]
            )
            .select(bars.columns)
            .sort(["symbol", "dt"])
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/data/test_feed.py -v`
Expected: PASS —— 11 passed

- [ ] **Step 5: 运行全部测试**

Run: `uv run pytest -v`
Expected: PASS —— 全部测试通过（约 110 项）

- [ ] **Step 6: 提交**

```bash
git add src/qsys/data/feed.py tests/data/test_feed.py
git commit -m "feat(data): as_of PIT 访问契约（HistoricalFeed）"
```

---

## Task 15: 端到端冒烟：真实数据落库并通过质量闸门

前十四个任务全部跑 fixture，本任务用真实免费数据源验证整条链路。这是本计划的验收。

**Files:**
- Create: `scripts/bootstrap_warehouse.py`
- Test: `tests/test_smoke_e2e.py`

**Interfaces:**
- Consumes: 前十四个任务的全部产出
- Produces: `bootstrap_warehouse(root: Path, symbols: Sequence[str], start: date, end: date) -> QualityReport`

- [ ] **Step 1: 写失败测试**

Create `tests/test_smoke_e2e.py`:

```python
"""端到端冒烟：用假 provider 走通完整链路（不触网）。

真实数据源的验证由 scripts/bootstrap_warehouse.py 手动执行。
"""

from datetime import date, datetime

import polars as pl

from qsys.core.calendar import AShareCalendar
from qsys.data.feed import Adjustment, HistoricalFeed
from qsys.data.pipeline.adjust import build_adj_factors
from qsys.data.pipeline.bars import ingest_daily_bars
from qsys.data.pipeline.instruments import build_instruments
from qsys.data.quality.report import assert_usable, run_quality_checks
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

SESSIONS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]

BAR_SCHEMA = {
    "dt": pl.Date, "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
    "close": pl.Float64, "volume": pl.Int64, "amount": pl.Float64,
}


class FakeBars:
    name = "fake-bars"

    def fetch_daily_bars(self, symbol, start, end):
        return pl.DataFrame(
            {
                "dt": SESSIONS,
                "open": [10.0, 10.1, 10.2],
                "high": [10.3, 10.4, 10.5],
                "low": [9.9, 10.0, 10.1],
                "close": [10.2, 10.3, 10.4],
                "volume": [1000, 1100, 1200],
                "amount": [10200.0, 11330.0, 12480.0],
            },
            schema=BAR_SCHEMA,
        ).filter(pl.col("dt").is_between(start, end))


class FakeReference:
    name = "fake-ref"

    def fetch_instruments(self):
        return pl.DataFrame(
            {
                "symbol": ["600036.SH"], "name": ["招商银行"], "market": ["SH"],
                "list_date": [date(2002, 4, 9)], "delist_date": [None], "board": ["main"],
            },
            schema={
                "symbol": pl.Utf8, "name": pl.Utf8, "market": pl.Utf8,
                "list_date": pl.Date, "delist_date": pl.Date, "board": pl.Utf8,
            },
        )

    def fetch_sessions(self, start, end):
        return [d for d in SESSIONS if start <= d <= end]

    def fetch_corporate_actions(self, symbol):
        return pl.DataFrame(
            schema={
                "ex_date": pl.Date, "cash_per_share": pl.Float64,
                "stock_per_share": pl.Float64, "rights_per_share": pl.Float64,
                "rights_price": pl.Float64,
            }
        )


def test_full_pipeline_produces_pit_queryable_warehouse(tmp_path):
    warehouse = Warehouse(tmp_path / "warehouse")
    calendar = AShareCalendar(SESSIONS)

    build_instruments(FakeReference(), warehouse)
    written = ingest_daily_bars(
        FakeBars(), warehouse, ["600036.SH"], SESSIONS[0], SESSIONS[-1]
    )
    assert written == 3
    assert build_adj_factors(warehouse) == 3

    query = WarehouseQuery(warehouse)
    report = run_quality_checks(query, calendar, ["600036.SH"], SESSIONS[0], SESSIONS[-1])
    assert_usable(report)
    assert report.is_blocked is False

    feed = HistoricalFeed(query, calendar)
    df = feed.get(
        ["600036.SH"], SESSIONS[0], SESSIONS[-1],
        as_of=datetime(2024, 1, 3, 15, 0), adjustment=Adjustment.BACKWARD,
    )
    assert df["dt"].to_list() == [date(2024, 1, 2), date(2024, 1, 3)]
    assert date(2024, 1, 4) not in df["dt"].to_list()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_smoke_e2e.py -v`
Expected: FAIL —— `ModuleNotFoundError` 或断言失败（视前序任务完成度）

- [ ] **Step 3: 实现 `scripts/bootstrap_warehouse.py`**

```python
"""用真实免费数据源初始化仓库并跑质量闸门。

用法：uv run python scripts/bootstrap_warehouse.py
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import structlog

from qsys.core.calendar import AShareCalendar
from qsys.data.pipeline.adjust import build_adj_factors
from qsys.data.pipeline.bars import ingest_daily_bars
from qsys.data.pipeline.instruments import build_instruments
from qsys.data.providers.akshare_provider import AkshareProvider
from qsys.data.providers.mootdx_provider import MootdxProvider
from qsys.data.quality.report import QualityReport, run_quality_checks
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

SYMBOLS = ["600036.SH", "000001.SZ", "300750.SZ"]


def bootstrap_warehouse(
    root: Path, symbols: Sequence[str], start: date, end: date
) -> QualityReport:
    warehouse = Warehouse(root)
    reference = AkshareProvider()
    bars_provider = MootdxProvider()

    calendar = AShareCalendar(reference.fetch_sessions(start, end))
    log.info("bootstrap.calendar", sessions=len(calendar.sessions_in_range(start, end)))

    build_instruments(reference, warehouse)
    ingest_daily_bars(bars_provider, warehouse, symbols, start, end)
    build_adj_factors(warehouse)

    query = WarehouseQuery(warehouse)
    report = run_quality_checks(query, calendar, symbols, start, end)
    print(report.summary())
    return report


def main() -> int:
    report = bootstrap_warehouse(
        Path("warehouse"), SYMBOLS, date(2024, 1, 1), date(2024, 6, 30)
    )
    return 1 if report.is_blocked else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_smoke_e2e.py -v`
Expected: PASS —— 1 passed

- [ ] **Step 5: 用真实数据源跑一次 bootstrap**

Run: `uv run python scripts/bootstrap_warehouse.py`
Expected: 打印质量摘要。三只标的、2024 上半年约 120 个交易日。

**预期会出现的问题与处理**：`completeness` 为 BLOCKING，若某只标的当期有停牌，会在此报出缺失 bar。一期尚无停牌记录表可解释这类缺失，因此**若违规数量少且集中于个别日期，属预期行为**——记录下来，停牌表在二期补齐。若违规覆盖整段区间，说明该标的采集失败，检查 `ingest_log` 定位原因。

- [ ] **Step 6: 检查仓库产物**

Run: `uv run python -c "
from datetime import date, datetime
from qsys.core.calendar import AShareCalendar
from qsys.data.feed import Adjustment, HistoricalFeed
from qsys.data.providers.akshare_provider import AkshareProvider
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from pathlib import Path

wh = Warehouse(Path('warehouse'))
q = WarehouseQuery(wh)
cal = AShareCalendar(AkshareProvider().fetch_sessions(date(2024,1,1), date(2024,6,30)))
feed = HistoricalFeed(q, cal)
df = feed.get(['600036.SH'], date(2024,1,1), date(2024,6,30),
              as_of=datetime(2024,3,1,15,0), adjustment=Adjustment.BACKWARD)
print(df.tail(5))
print('rows:', df.height, 'max dt:', df['dt'].max())
"`
Expected: 最后一行日期不超过 2024-03-01，证明 `as_of` 契约在真实数据上生效。

- [ ] **Step 7: 忽略仓库目录并提交**

```bash
cat > .gitignore <<'EOF'
.venv/
__pycache__/
*.pyc
.pytest_cache/
warehouse/
EOF
git add .gitignore scripts/bootstrap_warehouse.py tests/test_smoke_e2e.py
git commit -m "feat(data): 端到端冒烟与真实数据源初始化脚本"
```

---

## 完成标准

本计划完成时应满足：

1. `uv run pytest` 全绿
2. `uv run python scripts/bootstrap_warehouse.py` 能用真实免费源建出仓库并产出质量报告
3. `HistoricalFeed.get(..., as_of=T)` 在真实数据上验证有效——请求未来数据取不到
4. **策略层不得触达 `WarehouseQuery`** —— 策略只能通过 `ctx.data`（`DataFeed`）取数。
   `WarehouseQuery` 保持公开，它是 `feed` 与 `pipeline` 的基础设施，不是策略接口。
   Python 无真正的访问控制，靠命名约定是自欺欺人；有效隔离是「策略拿不到那个对象」，
   该约束由**计划 1-2** 的 `ctx` 构造强制并在那里验收。
5. `instruments` 含已退市标的，`alive_on()` 能还原任意历史日的在市股票集合
6. `bars_daily` 存不复权价，`adj_factors` 可重算，后复权价在除权日连续

## 下一步

进入 **计划 1-2：策略层与回测引擎** —— `Portfolio` 与五条交易约束、EventLoop、Strategy/ctx、SimBroker 保守撮合、合成数据断言套件、聚宽交叉验证。


---

# 执行记录（2026-09-07）

**结果：15 个任务全部完成，146 个测试通过。** 分支 `feat/phase1-1-data-foundation`。

## 完成标准核对

| # | 标准 | 结果 |
|---|---|---|
| 1 | `uv run pytest` 全绿 | ✅ 146 passed |
| 2 | bootstrap 建出仓库并产出质量报告 | ✅ 117 交易日 / 351 日线 / 63 除权事件 / clean 0 violations |
| 3 | `as_of` 在真实数据上有效 | ✅ 全区间请求 + `as_of=2024-03-01` 只返回至 03-01；盘中 09:30 只到 02-29 |
| 4 | 策略层不得触达 `WarehouseQuery` | ⏭ 顺延至计划 1-2 验收（见上文改写说明） |
| 5 | `instruments` 含退市股，`alive_on` 可还原历史在市集合 | ✅ 5919 只（含 367 退市） |
| 6 | 不复权存储 + 因子可重算 + 除权日后复权连续 | ✅ 平安银行 2024-06-14：不复权 −5.74% → 后复权 +0.98% |

## 六处偏离及原因

1. **uv 安装方式改变** —— 计划设想用包管理器。实际这台是 Intel Mac，Homebrew 已于 2026-09 停止支持 Intel x86_64，且 `brew install uv` 会拉取 `rust` + `llvm@22` 等五个依赖（数 GB）。改为从 GitHub Releases 直取预编译二进制（19MB）至 `~/.local/bin`。
2. **`.gitignore` 从 Task 15 提前到 Task 1** —— 否则 `.venv/` 会在首次提交时被误收入。
3. **akshare provider 四处修正**（由 Task 5 探针在真实数据上发现）：
   - **NaN 归零**：pandas 的 NaN 进入 polars 后仍是 NaN，polars 严格区分 NaN 与 null，`fill_null` 填不掉它。原计划只写了 `fill_null`，会让 `stock_per_share` 变成 NaN 并一路污染复权因子——**不报错，只是让回测悄悄失真**。改用 `fill_nan(0.0).fill_null(0.0)`。
   - **只取「实施分配」**：分红表混有「预披露」等未实施方案，不能产生复权因子。
   - **补 B 股号段**：真实退市表含沪 B `900xxx`（7 只）与深 B `200xxx`（21 只），原 `normalize_symbol` 会抛异常中断整条采集。同时新增 `board="bshare"`，因其计价货币与交易规则均与 A 股不同。未知号段改为跳过并告警，避免交易所新开号段导致管线整体崩溃。
   - **补 `list_date` 与 `name`**：退市表含 `上市日期` 与 `简称`，原计划漏用，会导致 `list_date` 全为 null。
4. **mootdx 增加历史截断告警** —— `offset` 语义是「最近 N 根 bar」而非起始位置，单次上限约回溯 3.2 年。静默截断会让长周期回测在不知情的情况下少掉早期数据。新增 `MootdxProvider.truncated` 记录并告警。
5. **三处 `unique()` 补 `maintain_order=True`** —— polars 默认不保证顺序，`keep="last"` 在无序前提下语义未定义，幂等性会退化为偶然行为。
6. **新增 `build_corporate_actions`（计划缺陷）** —— 原计划的 `bootstrap_warehouse` 调用链是 `build_instruments → ingest_daily_bars → build_adj_factors`，**从未采集除权事件**。照原计划执行，`adj_factors` 会全部等于 1.0，复权功能完全失效且不报任何错。已补齐流水线并加入回归测试 `test_factors_are_all_one_when_corporate_actions_missing`。

## 遗留缺口（转入二期）

- **配股数据缺失** —— `stock_fhps_detail_em` 只含分红送转，不含配股。做过配股的标的复权因子会偏差。见 spec §12。
- **在市股票的 `list_date` 为 null** —— 免费源的在市股票列表不提供上市日期，二期的「上市不满 N 日」过滤需先补此字段。
- **mootdx 单次回溯上限约 3.2 年** —— 10 年回测需实现分页拉取。
