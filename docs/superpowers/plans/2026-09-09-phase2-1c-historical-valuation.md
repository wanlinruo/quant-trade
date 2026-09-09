# 二期 2-1c：历史估值 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补上历史市值，解开 `check_high_open` 的阻塞——策略要求 `market_cap >= 70亿` 且 `circulating_market_cap <= 520亿`，而仓库当前有 0 行历史估值。

**Architecture:** 估值不去找现成的历史市值源（免费源要么只有一年，要么逐只查询对全市场不可行），而是**从股本反推**：`市值 = 收盘价 × 股本`。收盘价已有全部 894 天且经过 QMT 分钟线交叉验证；股本取自 QMT 财务表 `Capital`，实测覆盖 2023 年至今、事件级粒度、0.11 秒/只。

**Tech Stack:** Python 3.11+ · uv · polars · pyarrow · duckdb · xtquant(仅 Windows) · pytest

**Spec:** `docs/superpowers/specs/2026-09-06-personal-quant-system-design.md`（§4 数据层、§12 风险表「历史估值数据缺口」）

## 实测依据（2026-09-09 探针）

`xtdata.get_financial_data(codes, table_list=["Capital"])`，需先 `download_financial_data`：

| 字段 | 含义 |
|---|---|
| `m_timetag` | 股本**生效日** |
| `m_anntime` | **公告日** |
| `total_capital` | 总股本（股） |
| `circulating_capital` | 流通股本（股） |

- 覆盖 2023Q1 至今，**事件级粒度**（增发/解禁/回购注销都有独立记录，不只季末）
- 抽样 15 只，**15 只都有股本变动**——季度快照远不足以描述，必须按记录逐条落盘
- **0.11 秒/只**，全市场 5,217 只约 10 分钟，磁盘约 4 MB

## 生效日 vs 公告日

`m_anntime` 常晚于 `m_timetag`（如生效 2026-02-06、公告 2026-02-26），偶尔早于
（预告解禁）。**本计划用 `m_timetag` 作为生效基准**，理由：

- 股本是**公开可即时观察**的事实，交易所每日据此发布总市值，不存在「事后才知道」
- 聚宽 `get_valuation` 给的就是当日真实市值，用生效日才能对得上

`m_anntime` 一并落盘，将来若要更严格的 PIT 口径可直接切换，不必重采。

## Global Constraints

- Python **3.11+**
- **单位**：`Capital` 的股本单位是**股**；仓库日线 `volume` 单位是**手**（已实测：
  606,266 × 100 × 37.58 ≈ 2.2553e9 = 当日 amount）。换手率计算必须显式乘 100
- **市值单位为亿元**，与聚宽 `get_valuation` 一致——策略阈值 70 / 520 直接可比
- 估值是**派生数据**，可从 `bars_daily` + `share_capital` 完全重算
- 单元测试不得触网、不得依赖 xtquant
- 环境隔离：所有命令前置
  `unset VIRTUAL_ENV; export PATH="$HOME/.local/bin:$(echo "$PATH" | tr ':' '\n' | grep -v 'project-002/.venv' | paste -sd: -)"`

---

## File Structure

```
src/qsys/data/
  schema.py                   ← 追加 SHARE_CAPITAL
  query.py                    ← 追加 share_capital()
  pipeline/capital.py         股本导入（传输无关、幂等）
  pipeline/valuation.py       ← 追加 build_valuation()：从股本与收盘价派生
scripts/
  win_download_capital.py     Windows：下载股本并导出 parquet
  daily_archive.py            ← 接入股本增量与估值重算
tests/data/
  test_capital.py test_valuation_derived.py
```

---

## Task 1: SHARE_CAPITAL 表与查询

**Files:**
- Modify: `src/qsys/data/schema.py`、`src/qsys/data/query.py`
- Test: `tests/data/test_query.py`（追加）

**Interfaces:**
- Produces: `schema.SHARE_CAPITAL`；`WarehouseQuery.share_capital(symbols=None) -> pl.DataFrame`

- [ ] **Step 1: 写失败测试**

追加到 `tests/data/test_query.py`：

```python
def test_share_capital_round_trips(warehouse: Warehouse):
    import pyarrow as pa

    warehouse.write("share_capital", pa.table({
        "symbol": ["600036.SH", "600036.SH"],
        "dt": [date(2023, 3, 31), date(2023, 6, 30)],
        "ann_date": [date(2023, 3, 31), date(2023, 6, 30)],
        "total": [2.521985e10, 2.521985e10],
        "circulating": [2.062894e10, 2.062894e10],
    }, schema=schema.SHARE_CAPITAL))
    df = WarehouseQuery(warehouse).share_capital()
    assert df.height == 2
    assert df.columns == list(schema.SHARE_CAPITAL.names)


def test_share_capital_filters_by_symbol(warehouse: Warehouse):
    import pyarrow as pa

    warehouse.write("share_capital", pa.table({
        "symbol": ["600036.SH", "000001.SZ"],
        "dt": [date(2023, 3, 31)] * 2,
        "ann_date": [date(2023, 3, 31)] * 2,
        "total": [1.0, 2.0], "circulating": [1.0, 2.0],
    }, schema=schema.SHARE_CAPITAL))
    df = WarehouseQuery(warehouse).share_capital(["600036.SH"])
    assert df["symbol"].to_list() == ["600036.SH"]


def test_share_capital_on_empty_warehouse(warehouse: Warehouse):
    df = WarehouseQuery(warehouse).share_capital()
    assert df.height == 0
    assert df.columns == list(schema.SHARE_CAPITAL.names)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/test_query.py -q`
Expected: FAIL —— `AttributeError: module 'qsys.data.schema' has no attribute 'SHARE_CAPITAL'`

- [ ] **Step 3: 追加 schema**

`src/qsys/data/schema.py`，放在 `ADJ_FACTORS` 之后：

```python
SHARE_CAPITAL = pa.schema(
    [
        ("symbol", pa.string()),
        # 股本生效日（QMT m_timetag）。股本是公开可即时观察的事实，
        # 交易所每日据此发布总市值，故以生效日而非公告日为准。
        ("dt", pa.date32()),
        ("ann_date", pa.date32()),  # 公告日，留作更严格 PIT 口径的口子
        ("total", pa.float64()),  # 总股本（股）
        ("circulating", pa.float64()),  # 流通股本（股）
    ]
)
```

注册进 `UNPARTITIONED`（全市场约 15 万行，不必分区）：
`"share_capital": SHARE_CAPITAL,`

- [ ] **Step 4: 追加查询方法**

`WarehouseQuery`，紧邻 `adj_factors`：

```python
    def share_capital(self, symbols: Sequence[str] | None = None) -> pl.DataFrame:
        return self._read_unpartitioned(
            "share_capital", schema.SHARE_CAPITAL, symbols
        )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/data/test_query.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/qsys/data/schema.py src/qsys/data/query.py tests/data/test_query.py
git commit -m "feat(data): SHARE_CAPITAL 表与查询"
```

---

## Task 2: 股本导入

与分钟线同构：Windows 导出 parquet，Linux 按目录导入，传输方式不进代码。

**Files:**
- Create: `src/qsys/data/pipeline/capital.py`
- Test: `tests/data/test_capital.py`

**Interfaces:**
- Produces: `import_share_capital(warehouse, incoming_dir, *, archive_dir=None) -> ImportResult`

- [ ] **Step 1: 写失败测试**

Create `tests/data/test_capital.py`:

```python
from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq

from qsys.data import schema
from qsys.data.pipeline.capital import import_share_capital
from qsys.data.query import WarehouseQuery


def write_incoming(directory, name, symbol="600036.SH", rows=2):
    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table({
            "symbol": [symbol] * rows,
            "dt": [date(2023, 3, 31), date(2023, 6, 30)][:rows],
            "ann_date": [date(2023, 4, 2), date(2023, 7, 2)][:rows],
            "total": [1.0e10, 1.2e10][:rows],
            "circulating": [0.8e10, 0.9e10][:rows],
        }, schema=schema.SHARE_CAPITAL),
        directory / name,
    )


def test_imports_a_file(warehouse, tmp_path):
    write_incoming(tmp_path / "in", "a.parquet")
    result = import_share_capital(warehouse, tmp_path / "in")
    assert result.files == 1
    assert result.rows == 2
    assert WarehouseQuery(warehouse).share_capital().height == 2


def test_reimport_is_idempotent(warehouse, tmp_path):
    write_incoming(tmp_path / "in", "a.parquet")
    import_share_capital(warehouse, tmp_path / "in")
    write_incoming(tmp_path / "in", "a.parquet")
    import_share_capital(warehouse, tmp_path / "in")
    assert WarehouseQuery(warehouse).share_capital().height == 2


def test_a_second_batch_appends(warehouse, tmp_path):
    write_incoming(tmp_path / "in1", "a.parquet", symbol="600036.SH")
    import_share_capital(warehouse, tmp_path / "in1")
    write_incoming(tmp_path / "in2", "b.parquet", symbol="000001.SZ")
    import_share_capital(warehouse, tmp_path / "in2")
    df = WarehouseQuery(warehouse).share_capital()
    assert df["symbol"].n_unique() == 2
    assert df.height == 4


def test_a_bad_file_does_not_abort_the_batch(warehouse, tmp_path):
    incoming = tmp_path / "in"
    write_incoming(incoming, "good.parquet")
    (incoming / "bad.parquet").write_bytes(b"not parquet")
    result = import_share_capital(warehouse, incoming)
    assert result.rows == 2
    assert result.errors == 1


def test_missing_directory_is_a_no_op(warehouse, tmp_path):
    result = import_share_capital(warehouse, tmp_path / "nope")
    assert result.files == 0
    assert result.rows == 0


def test_archived_files_avoid_name_collisions(warehouse, tmp_path):
    incoming, archive = tmp_path / "in", tmp_path / "done"
    write_incoming(incoming, "capital_00000.parquet", symbol="600036.SH")
    import_share_capital(warehouse, incoming, archive_dir=archive)
    write_incoming(incoming, "capital_00000.parquet", symbol="000001.SZ")
    import_share_capital(warehouse, incoming, archive_dir=archive)
    assert len(list(archive.glob("*.parquet"))) == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/test_capital.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.pipeline.capital'`

- [ ] **Step 3: 实现 `src/qsys/data/pipeline/capital.py`**

```python
"""股本导入。

与分钟线导入同构：Windows 侧把 parquet 丢进目录，这里按目录导入，
传输方式不进代码。单个坏文件不中断整批。

股本是全市场约 15 万行的小表，不分区。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.pipeline.minutes import ImportResult, _free_name
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

_DATASET = "share_capital"
_COLUMNS = list(schema.SHARE_CAPITAL.names)


def import_share_capital(
    warehouse: Warehouse,
    incoming_dir: Path,
    *,
    archive_dir: Path | None = None,
) -> ImportResult:
    incoming = Path(incoming_dir)
    if not incoming.is_dir():
        return ImportResult(0, 0, 0)

    frames: list[pl.DataFrame] = []
    good: list[Path] = []
    errors = 0

    for path in sorted(incoming.glob("*.parquet")):
        try:
            frame = pl.read_parquet(path)
            missing = set(_COLUMNS) - set(frame.columns)
            if missing:
                raise ValueError(f"missing columns: {sorted(missing)}")
            frames.append(frame.select(_COLUMNS))
            good.append(path)
        except Exception as exc:  # 单个坏文件不中断整批
            errors += 1
            log.warning("capital.bad_file", path=str(path), error=str(exc))

    rows = 0
    if frames:
        fresh = pl.concat(frames, how="vertical")
        merged = (
            pl.concat(
                [WarehouseQuery(warehouse).share_capital(), fresh], how="vertical"
            )
            .unique(subset=["symbol", "dt"], keep="last", maintain_order=True)
            .sort(["symbol", "dt"])
            .select(_COLUMNS)
        )
        warehouse.write(
            _DATASET, merged.to_arrow().cast(schema.SHARE_CAPITAL)
        )
        rows = fresh.height

    if archive_dir is not None:
        archive = Path(archive_dir)
        archive.mkdir(parents=True, exist_ok=True)
        for path in good:
            shutil.move(str(path), str(_free_name(archive, path.name)))

    log.info("capital.imported", files=len(good), rows=rows, errors=errors)
    return ImportResult(len(good), rows, errors)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/data/test_capital.py -q`
Expected: PASS —— 7 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/pipeline/capital.py tests/data/test_capital.py
git commit -m "feat(data): 股本导入"
```

---

## Task 3: 从股本派生历史估值

**Files:**
- Modify: `src/qsys/data/pipeline/valuation.py`
- Test: `tests/data/test_valuation_derived.py`

**Interfaces:**
- Consumes: `bars_daily`、`share_capital`（Task 2）
- Produces: `build_valuation(warehouse, start, end) -> int`

关键是**按生效日向前找最近一条股本**（as-of join）：某日的股本 = 生效日
不晚于该日的最后一条记录。

- [ ] **Step 1: 写失败测试**

Create `tests/data/test_valuation_derived.py`:

```python
from datetime import date

import polars as pl
import pyarrow as pa
import pytest

from qsys.data import schema
from qsys.data.pipeline.valuation import build_valuation
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

D = [date(2024, 1, d) for d in (2, 3, 4, 5)]


def seed(warehouse: Warehouse, bars, capital):
    """bars: [(symbol, dt, close, volume)]；capital: [(symbol, dt, total, circ)]"""
    warehouse.write("bars_daily", pa.table({
        "symbol": [b[0] for b in bars], "dt": [b[1] for b in bars],
        "open": [b[2] for b in bars], "high": [b[2] for b in bars],
        "low": [b[2] for b in bars], "close": [b[2] for b in bars],
        "volume": [b[3] for b in bars], "amount": [0.0] * len(bars),
    }, schema=schema.BARS_DAILY), year=2024)
    warehouse.write("share_capital", pa.table({
        "symbol": [c[0] for c in capital], "dt": [c[1] for c in capital],
        "ann_date": [c[1] for c in capital],
        "total": [c[2] for c in capital], "circulating": [c[3] for c in capital],
    }, schema=schema.SHARE_CAPITAL))


def value_of(warehouse, symbol, dt):
    df = WarehouseQuery(warehouse).valuation(dt, dt)
    return df.filter(df["symbol"] == symbol).row(0, named=True)


def test_market_cap_is_close_times_total_shares_in_yi(warehouse):
    """市值单位为亿元，与聚宽 get_valuation 一致。"""
    seed(warehouse,
         [("600036.SH", D[0], 10.0, 1000)],
         [("600036.SH", date(2023, 12, 31), 1.0e10, 0.8e10)])
    build_valuation(warehouse, D[0], D[0])
    row = value_of(warehouse, "600036.SH", D[0])
    assert row["market_cap"] == pytest.approx(1000.0)          # 10 x 1e10 / 1e8
    assert row["circulating_market_cap"] == pytest.approx(800.0)


def test_uses_the_latest_capital_effective_on_or_before_the_day(warehouse):
    """股本按生效日向前找最近一条，不能用未来的股本。"""
    seed(warehouse,
         [("600036.SH", D[0], 10.0, 1000), ("600036.SH", D[3], 10.0, 1000)],
         [("600036.SH", date(2023, 12, 31), 1.0e10, 0.8e10),
          ("600036.SH", D[2], 2.0e10, 1.6e10)])
    build_valuation(warehouse, D[0], D[3])
    assert value_of(warehouse, "600036.SH", D[0])["market_cap"] == pytest.approx(1000.0)
    assert value_of(warehouse, "600036.SH", D[3])["market_cap"] == pytest.approx(2000.0)


def test_a_day_before_any_capital_record_is_skipped(warehouse):
    """没有可用股本就不该编造市值。"""
    seed(warehouse,
         [("600036.SH", D[0], 10.0, 1000)],
         [("600036.SH", D[3], 1.0e10, 0.8e10)])
    assert build_valuation(warehouse, D[0], D[0]) == 0


def test_turnover_ratio_accounts_for_the_lot_unit(warehouse):
    """日线 volume 单位是手，换手率要乘 100 换成股。"""
    seed(warehouse,
         [("600036.SH", D[0], 10.0, 1_000_000)],
         [("600036.SH", date(2023, 12, 31), 1.0e10, 1.0e10)])
    build_valuation(warehouse, D[0], D[0])
    row = value_of(warehouse, "600036.SH", D[0])
    # 100 万手 = 1 亿股；流通 100 亿股 -> 换手率 1%
    assert row["turnover_ratio"] == pytest.approx(1.0)


def test_close_is_carried_through(warehouse):
    seed(warehouse,
         [("600036.SH", D[0], 12.5, 1000)],
         [("600036.SH", date(2023, 12, 31), 1.0e10, 0.8e10)])
    build_valuation(warehouse, D[0], D[0])
    assert value_of(warehouse, "600036.SH", D[0])["close"] == pytest.approx(12.5)


def test_symbols_are_independent(warehouse):
    seed(warehouse,
         [("600036.SH", D[0], 10.0, 1000), ("000001.SZ", D[0], 20.0, 1000)],
         [("600036.SH", date(2023, 12, 31), 1.0e10, 0.8e10),
          ("000001.SZ", date(2023, 12, 31), 2.0e10, 2.0e10)])
    build_valuation(warehouse, D[0], D[0])
    assert value_of(warehouse, "600036.SH", D[0])["market_cap"] == pytest.approx(1000.0)
    assert value_of(warehouse, "000001.SZ", D[0])["market_cap"] == pytest.approx(4000.0)


def test_rebuild_is_idempotent(warehouse):
    seed(warehouse,
         [("600036.SH", D[0], 10.0, 1000), ("600036.SH", D[1], 11.0, 1000)],
         [("600036.SH", date(2023, 12, 31), 1.0e10, 0.8e10)])
    assert build_valuation(warehouse, D[0], D[1]) == 2
    assert build_valuation(warehouse, D[0], D[1]) == 2
    assert WarehouseQuery(warehouse).valuation().height == 2


def test_partial_rebuild_keeps_the_rest_of_the_year(warehouse):
    """与 flags/regime 同一个坑：分区整年覆盖写，只重算一段不能抹掉其余。"""
    seed(warehouse,
         [("600036.SH", d, 10.0, 1000) for d in D],
         [("600036.SH", date(2023, 12, 31), 1.0e10, 0.8e10)])
    assert build_valuation(warehouse, D[0], D[-1]) == 4
    build_valuation(warehouse, D[-1], D[-1])
    assert WarehouseQuery(warehouse).valuation().height == 4


def test_empty_warehouse_yields_zero(warehouse):
    assert build_valuation(warehouse, D[0], D[-1]) == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/test_valuation_derived.py -q`
Expected: FAIL —— `ImportError: cannot import name 'build_valuation'`

- [ ] **Step 3: 在 `src/qsys/data/pipeline/valuation.py` 追加**

顶部 import 补 `timedelta`、`build` 所需的 `WarehouseQuery` 已有。追加：

```python
_LOT = 100  # 日线 volume 单位是手，换算成股要乘 100


def build_valuation(warehouse: Warehouse, start: date, end: date) -> int:
    """从收盘价与股本派生历史估值。返回写入行数。

    市值 = 收盘价 × 股本 / 1e8，单位**亿元**，与聚宽 get_valuation 一致，
    策略阈值 70 / 520 可直接比较。

    股本按**生效日**向前找最近一条（as-of join）：某日的股本是生效日不晚于
    该日的最后一条记录。生效日之前没有任何记录的交易日不产出行——
    宁可缺，不可编。
    """
    query = WarehouseQuery(warehouse)
    capital = query.share_capital()
    if capital.height == 0:
        return 0

    written = 0
    for year in range(start.year, end.year + 1):
        lo_year, hi_year = date(year, 1, 1), date(year, 12, 31)
        lo, hi = max(start, lo_year), min(end, hi_year)
        if lo > hi:
            continue

        bars = query.daily_bars(None, lo, hi)
        if bars.height == 0:
            continue

        # as-of join 要求两边都按连接键排序
        left = bars.select(["symbol", "dt", "close", "volume"]).sort(["dt", "symbol"])
        right = capital.select(["symbol", "dt", "total", "circulating"]).sort(
            ["dt", "symbol"]
        )
        joined = (
            left.join_asof(right, on="dt", by="symbol", strategy="backward")
            .drop_nulls(["total", "circulating"])
            .filter((pl.col("total") > 0) & (pl.col("circulating") > 0))
        )
        if joined.height == 0:
            continue

        fresh = joined.select(
            pl.col("dt"),
            pl.col("symbol"),
            pl.col("close"),
            (pl.col("close") * pl.col("total") / 1e8).alias("market_cap"),
            (pl.col("close") * pl.col("circulating") / 1e8).alias(
                "circulating_market_cap"
            ),
            (pl.col("volume") * _LOT / pl.col("circulating") * 100.0).alias(
                "turnover_ratio"
            ),
        ).select(_COLUMNS)

        merged = (
            pl.concat([query.valuation(lo_year, hi_year), fresh], how="vertical")
            .unique(subset=["dt", "symbol"], keep="last", maintain_order=True)
            .sort(["dt", "symbol"])
            .select(_COLUMNS)
        )
        warehouse.write(
            "valuation_snapshot",
            merged.to_arrow().cast(schema.VALUATION_SNAPSHOT),
            year=year,
        )
        written += fresh.height

    log.info("valuation.built", rows=written, start=str(start), end=str(end))
    return written
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/data/test_valuation_derived.py -q`
Expected: PASS —— 9 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/pipeline/valuation.py tests/data/test_valuation_derived.py
git commit -m "feat(data): 从股本与收盘价派生历史估值"
```

---

## Task 4: Windows 股本下载脚本

**Files:**
- Create: `scripts/win_download_capital.py`

- [ ] **Step 1: 写脚本**

```python
"""Windows 侧：下载全市场股本历史并导出 parquet。

**只在 Windows 上运行**（依赖 xtquant）。可中断可续传。

实测：download_financial_data 0.11 秒/只，全市场约 10 分钟，磁盘约 4 MB。

`m_timetag` 是股本**生效日**，`m_anntime` 是公告日。两者都落盘——
市值按生效日算（股本是公开可即时观察的事实），公告日留作更严格 PIT 的口子。

用法：
  C:\\qsys\\.venv\\Scripts\\python.exe scripts\\win_download_capital.py ^
      --symbols C:\\qsys\\symbols.csv --out C:\\qsys\\capital
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime

COLUMNS = ["symbol", "dt", "ann_date", "total", "circulating"]


def _arrow_schema():
    import pyarrow as pa

    return pa.schema([
        ("symbol", pa.string()),
        ("dt", pa.date32()),
        ("ann_date", pa.date32()),
        ("total", pa.float64()),
        ("circulating", pa.float64()),
    ])


def _as_date(stamp):
    text = str(stamp).strip()
    if len(text) < 8 or not text[:8].isdigit():
        return None
    try:
        return datetime.strptime(text[:8], "%Y%m%d").date()
    except ValueError:
        return None


def read_symbols(path: str) -> list[str]:
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [row["symbol"] for row in reader if row.get("symbol")]


def read_done(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as handle:
        return {line.strip() for line in handle if line.strip()}


def flush(rows, out_dir, index):
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table(
        {name: [r[i] for r in rows] for i, name in enumerate(COLUMNS)},
        schema=_arrow_schema(),
    )
    path = os.path.join(out_dir, f"capital_{index:05d}.parquet")
    pq.write_table(table, path)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="下载全市场股本历史")
    parser.add_argument("--symbols", required=True, help="含 symbol 列的 csv")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch", type=int, default=200, help="每批下载多少只")
    parser.add_argument("--flush-rows", type=int, default=100_000)
    args = parser.parse_args()

    from xtquant import xtdata

    os.makedirs(args.out, exist_ok=True)
    done_path = os.path.join(args.out, "done.csv")
    done = read_done(done_path)
    todo = [s for s in read_symbols(args.symbols) if s not in done]
    if args.limit:
        todo = todo[: args.limit]

    print(f"待下载 {len(todo)} 只（已完成 {len(done)}）")
    if not todo:
        return 0

    buffer, file_index = [], len(
        [f for f in os.listdir(args.out) if f.endswith(".parquet")]
    )
    started = time.time()
    ok = empty = 0

    for offset in range(0, len(todo), args.batch):
        chunk = todo[offset : offset + args.batch]
        try:
            xtdata.download_financial_data(chunk, table_list=["Capital"])
            data = xtdata.get_financial_data(
                chunk, table_list=["Capital"],
                start_time="20200101", end_time="20991231",
                report_type="report_time",
            )
        except Exception as exc:  # 整批失败也不中断，下批继续
            print(f"  [失败] 批 {offset}: {type(exc).__name__}: {exc}", flush=True)
            continue

        for symbol in chunk:
            frame = (data.get(symbol) or {}).get("Capital")
            if frame is None or len(frame) == 0:
                empty += 1
            else:
                for _, row in frame.iterrows():
                    effective = _as_date(row.get("m_timetag"))
                    if effective is None:
                        continue
                    buffer.append((
                        symbol, effective, _as_date(row.get("m_anntime")),
                        float(row.get("total_capital") or 0.0),
                        float(row.get("circulating_capital") or 0.0),
                    ))
                ok += 1
            with open(done_path, "a", encoding="utf-8") as handle:
                handle.write(symbol + "\n")

        if len(buffer) >= args.flush_rows:
            print(f"  写出 {os.path.basename(flush(buffer, args.out, file_index))}"
                  f"  {len(buffer):,} 行", flush=True)
            file_index += 1
            buffer = []

        elapsed = time.time() - started
        seen = offset + len(chunk)
        rate = seen / elapsed if elapsed else 0
        print(f"  {seen}/{len(todo)}  有数据 {ok} 空 {empty}  "
              f"{rate:.0f} 只/秒  剩余约 {(len(todo)-seen)/rate/60 if rate else 0:.0f} 分钟",
              flush=True)

    if buffer:
        print(f"  写出 {os.path.basename(flush(buffer, args.out, file_index))}"
              f"  {len(buffer):,} 行")
    print(f"完成：有数据 {ok} 只 / 空 {empty} 只 / 耗时 {(time.time()-started)/60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 语法检查**

Run: `python3 -c "import ast; ast.parse(open('scripts/win_download_capital.py').read())"`
Expected: 无输出即通过

- [ ] **Step 3: 小批试跑（真实数据）**

在 Windows 上先跑 50 只，确认产出格式与速率，再放全量。

- [ ] **Step 4: 提交**

```bash
git add scripts/win_download_capital.py
git commit -m "feat(data): Windows 股本下载脚本"
```

---

## Task 5: 全量采集与交叉验证

- [ ] **Step 1: 导出全市场标的清单并上传**

- [ ] **Step 2: Windows 全量下载股本**（约 10 分钟）

- [ ] **Step 3: 回传并导入 Linux**

- [ ] **Step 4: 派生全量估值**

`build_valuation(warehouse, date(2023,1,1), 最后交易日)`

- [ ] **Step 5: 交叉验证**

三项都必须过：

1. **恒等式**：`total >= circulating` 对每一条记录成立
2. **量级**：全市场市值分布合理——中位数在数十亿量级，无负值、无零
3. **与送转对账**：`corporate_actions.stock_per_share > 0` 的除权日附近，
   `total_capital` 应按对应比例上升。这是**唯一的独立交叉验证**
   （股本来自 QMT，送转来自 akshare）

   **注意**：仓库当前 `corporate_actions` 只覆盖 3 只（1-1 的 bootstrap 样本），
   全市场送转数据尚未采集。若样本不足，本项降级为「抽样人工核对」并记为缺口。

- [ ] **Step 6: 提交**

---

## Task 6: 接入每日归档

**Files:**
- Modify: `scripts/daily_archive.py`

- [ ] **Step 1: 在派生表重算之后、候选计算之前插入股本增量与估值重算**

股本变动是事件驱动的，每日跑一次全市场下载只要 10 分钟，直接全量重下最省事；
估值按 `--lookback-days` 的窗口重算即可（合并写，不会抹掉同年其余数据）。

- [ ] **Step 2: 实跑一次确认整链路仍通**

- [ ] **Step 3: 提交**

---

## 完成标准

1. `uv run pytest` 全绿，既有 790 项无回归
2. **估值覆盖全区间** —— `valuation_snapshot` 覆盖 2023-01 至最后交易日
3. **单位正确** —— 市值为亿元，与聚宽 `get_valuation` 可直接比较
4. **股本按生效日取用** —— 不使用生效日晚于当日的记录
5. **缺股本的日子不产出行** —— 宁可缺，不可编
6. **派生可完全重算**，且部分区间重算不抹掉同年其余数据
7. 每日归档接入股本与估值

## 执行实测（2026-09-09）

| 项 | 结果 |
|---|---|
| Windows 下载股本 | **5,799 只有数据 / 122 空 / 4.9 分钟** |
| 导入 | 219,154 条 / 5,799 只 / 0 错误 |
| 股本记录密度 | 中位 **36 条/只**，最多 190 条——事件级粒度确认 |
| 生效日区间 | 2020-01-01 ~ 2026-09-10（含预告解禁的未来记录） |
| **派生估值** | **4,504,869 行 / 6.2 秒 / 峰值 1.2 GB** |
| **覆盖率** | **100.00%**（每一根日线都有估值） |

**校验全部通过**：

| 校验 | 结果 |
|---|---|
| `total >= circulating` | **0 违反** / 219,154 条 |
| 流通市值 > 总市值 | **0 行** |
| 换手率 > 100% | **0 行** |
| 总市值非正 | **0 行** |
| 总市值分布（亿元） | 中位 65.9 / P10 26.4 / P90 379.6 / max 39,638 |
| 换手率分布（%） | 中位 1.86 / P90 6.80 / max 54.0 |
| 招商银行抽查 | 总市值 10,315 亿、流通 8,437 亿、换手 0.239% |
| 策略阈值筛出率 | 41.5%（既非 0 也非全部） |

**每日归档接入后实跑**：9.1 分钟，峰值 617.8 MB，swap 0，跨源校验 0 问题，
`Result=success`。

## 已知缺口（转入后续）

- **送转对账样本不足** —— 全市场 `corporate_actions` 尚未采集，独立交叉验证受限
- **`adj_factors` 只覆盖 3 只** —— 复权回测需要全市场复权因子，本计划未覆盖
- **指数因子** —— `index_ma20_slope_5d` 需沪深 300 指数日线，仍未采集
- **竞价价语义分歧** —— 原策略取 09:15 首条竞价快照，我们只有 09:25 成交价

## 下一步

估值到位后即可移植 **HIGH + LOW 两个分支**（WEAK 在 V1.2 中已被整体移除），
再与聚宽对账「哪天买了哪些票」。
