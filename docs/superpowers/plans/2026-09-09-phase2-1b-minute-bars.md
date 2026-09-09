# 二期 2-1b：分钟线（QMT 下载 + 跨机同步） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把策略精确版所需的分钟线接进来——集合竞价成交量与 11:25 / 14:25 / 14:50 的日内价格——并让它像日线一样受 PIT 契约保护。

**Architecture:** 分钟线只覆盖**候选股在候选日**，不是全市场全历史（实测：只下候选可省 20–60 倍数据量，见下）。Windows 侧用 QMT `xtdata` 下载并导出 parquet，Linux 侧按目录导入，传输方式与导入逻辑解耦。存储统一用**收盘时刻**标注每根 bar，PIT 可见性因此退化为 `dt <= as_of`，没有差一分钟的歧义。

**Tech Stack:** Python 3.11+ · uv · polars · pyarrow · duckdb · xtquant(仅 Windows) · pytest

**Spec:** `docs/superpowers/specs/2026-09-06-personal-quant-system-design.md`（§2.1 数据粒度修订、§4 数据层、§8 测试）

## 为什么是「候选股-日」而不是全市场

2-1a 采完全市场日线后，用真实数据算出来的（894 个交易日，2023-01 ~ 2026-09）：

| 口径 | 规模 |
|---|---|
| 首板事件 | 41,620 次（日均 46.6） |
| **涉及的不同标的** | **4,741 / 5,217 = 91%** |
| 首板后 1 个交易日：股-日组合 | 41,592 组 ≈ **1000 万根分钟线** |
| 首板后 3 个交易日 | 122,936 组 ≈ 2950 万根 |
| 首板后 6 个交易日 | 232,162 组 ≈ 5570 万根 |
| 全市场全历史（对照） | 4741 × 894 × 240 ≈ **10.2 亿根** |

**结论**：91% 的标的都至少首板过一次，所以「只下候选股」在标的维度几乎不省；省的全在日期维度。下载粒度必须是**（标的，日期）**，按标的下全历史等于下全市场。

## Global Constraints

- Python **3.11+**
- **bar 时间戳一律为该分钟的收盘时刻**（09:30–09:31 这根记 `09:31`）。源数据若给开盘时刻，由导入侧转换。PIT 可见性即 `dt <= as_of`
- 分钟线**只覆盖候选股-日**，不追求全市场完整性。缺失是常态，不是错误
- **单元测试不得触网、不得依赖 xtquant**。xtquant 只在 Windows 上存在，Linux 上 import 就会失败——所有涉及它的代码必须放在独立脚本里，不得被库代码 import
- 导入必须**幂等**：同一批文件重复导入结果一致
- 沿用既有纪律：不复权存储、写入幂等、质量校验不过不进回测
- 环境隔离：所有命令前置
  `unset VIRTUAL_ENV; export PATH="$HOME/.local/bin:$(echo "$PATH" | tr ':' '\n' | grep -v 'project-002/.venv' | paste -sd: -)"`

---

## File Structure

```
src/qsys/data/
  schema.py                    ← 追加 MINUTE_BARS
  query.py                     ← 追加 minute_bars()
  pipeline/candidates.py       候选股-日选择器与清单导出
  pipeline/minutes.py          分钟线导入（传输无关、幂等）
  minute_feed.py               分钟线 PIT 契约
  quality/minute_rules.py      分钟线质量校验
scripts/
  export_minute_manifest.py    Linux：导出待下载清单
  win_download_minutes.py      Windows：按清单下载并导出 parquet（不被库代码 import）
tests/data/
  test_candidates.py test_minutes_import.py test_minute_feed.py
  test_minute_quality.py
tests/
  test_minute_e2e.py
```

---

## Task 1: MINUTE_BARS 表与查询

**Files:**
- Modify: `src/qsys/data/schema.py`、`src/qsys/data/query.py`
- Test: `tests/data/test_query.py`（追加）

**Interfaces:**
- Produces: `schema.MINUTE_BARS`；`WarehouseQuery.minute_bars(symbols, start: datetime, end: datetime) -> pl.DataFrame`

- [ ] **Step 1: 写失败测试**

追加到 `tests/data/test_query.py`：

```python
def _seed_minutes(warehouse: Warehouse) -> None:
    import pyarrow as pa
    from datetime import datetime

    warehouse.write("minute_bars", pa.table({
        "symbol": ["600036.SH"] * 3 + ["000001.SZ"],
        "dt": [datetime(2024, 1, 2, 9, 31), datetime(2024, 1, 2, 9, 32),
               datetime(2024, 1, 2, 15, 0), datetime(2024, 1, 2, 9, 31)],
        "open": [10.0] * 4, "high": [10.5] * 4, "low": [9.5] * 4,
        "close": [10.2] * 4, "volume": [100] * 4, "amount": [1020.0] * 4,
    }, schema=schema.MINUTE_BARS), year=2024)


def test_minute_bars_filter_by_symbol_and_time(warehouse: Warehouse):
    from datetime import datetime

    _seed_minutes(warehouse)
    q = WarehouseQuery(warehouse)
    df = q.minute_bars(["600036.SH"], datetime(2024, 1, 2, 9, 0), datetime(2024, 1, 2, 10, 0))
    assert df.height == 2
    assert df["symbol"].unique().to_list() == ["600036.SH"]


def test_minute_bars_all_symbols(warehouse: Warehouse):
    from datetime import datetime

    _seed_minutes(warehouse)
    df = WarehouseQuery(warehouse).minute_bars(
        None, datetime(2024, 1, 2, 0, 0), datetime(2024, 1, 2, 23, 59))
    assert df.height == 4


def test_minute_bars_on_empty_warehouse(warehouse: Warehouse):
    from datetime import datetime

    df = WarehouseQuery(warehouse).minute_bars(
        None, datetime(2024, 1, 1, 0, 0), datetime(2024, 1, 2, 0, 0))
    assert df.height == 0
    assert df.columns == list(schema.MINUTE_BARS.names)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/test_query.py -q`
Expected: FAIL —— `AttributeError: module 'qsys.data.schema' has no attribute 'MINUTE_BARS'`

- [ ] **Step 3: 追加 schema**

`src/qsys/data/schema.py`，放在 `BARS_DAILY` 之后：

```python
MINUTE_BARS = pa.schema(
    [
        ("symbol", pa.string()),
        # 该分钟的**收盘时刻**：09:30–09:31 这根记 09:31。
        # 如此 PIT 可见性就是 dt <= as_of，没有差一分钟的歧义。
        ("dt", pa.timestamp("us")),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.int64()),
        ("amount", pa.float64()),
    ]
)
```

注册进 `PARTITIONED`：`"minute_bars": MINUTE_BARS,`

- [ ] **Step 4: 追加查询方法**

`WarehouseQuery`，紧邻 `daily_bars`：

```python
    def minute_bars(
        self,
        symbols: Sequence[str] | None,
        start: datetime,
        end: datetime,
    ) -> pl.DataFrame:
        if symbols is not None and len(symbols) == 0:
            return _empty(schema.MINUTE_BARS)
        glob = self._glob("minute_bars", partitioned=True)
        if glob is None:
            return _empty(schema.MINUTE_BARS)

        columns = ", ".join(schema.MINUTE_BARS.names)
        sql = (
            f"SELECT {columns} FROM read_parquet('{glob}') WHERE dt BETWEEN ? AND ?"
        )
        params: list[object] = [start, end]
        if symbols is not None:
            placeholders = ", ".join("?" for _ in symbols)
            sql += f" AND symbol IN ({placeholders})"
            params.extend(symbols)
        return self._con.execute(sql + " ORDER BY symbol, dt", params).pl()
```

文件顶部 import 补 `datetime`：`from datetime import date, datetime`

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/data/test_query.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/qsys/data/schema.py src/qsys/data/query.py tests/data/test_query.py
git commit -m "feat(data): MINUTE_BARS 表与分钟线查询"
```

---

## Task 2: 候选股-日选择器

决定「要下载哪些股在哪些日的分钟线」。这是 Windows 下载脚本的输入，也是唯一决定数据量的地方。

**Files:**
- Create: `src/qsys/data/pipeline/candidates.py`
- Create: `scripts/export_minute_manifest.py`
- Test: `tests/data/test_candidates.py`

**Interfaces:**
- Consumes: `daily_flags`（2-1a Task 3）
- Produces: `candidate_stock_days(warehouse, start, end, *, window=1) -> pl.DataFrame[symbol, dt]`；`write_manifest(frame, path) -> int`

- [ ] **Step 1: 写失败测试**

Create `tests/data/test_candidates.py`:

```python
from datetime import date

import polars as pl
import pyarrow as pa

from qsys.data import schema
from qsys.data.pipeline.candidates import candidate_stock_days, write_manifest
from qsys.data.storage import Warehouse

D = [date(2024, 1, i) for i in (2, 3, 4, 5, 8)]


def seed_flags(warehouse: Warehouse, rows) -> None:
    """rows: [(symbol, dt, is_first_board)]"""
    n = len(rows)
    warehouse.write("daily_flags", pa.table({
        "dt": [r[1] for r in rows], "symbol": [r[0] for r in rows],
        "prev_close": [10.0] * n, "limit_up": [11.0] * n, "limit_down": [9.0] * n,
        "is_limit_up": [r[2] for r in rows], "is_limit_down": [False] * n,
        "touched_limit_up": [r[2] for r in rows], "is_broken_board": [False] * n,
        "is_first_board": [r[2] for r in rows],
    }, schema=schema.DAILY_FLAGS), year=2024)


def test_candidate_is_the_session_after_a_first_board(warehouse):
    seed_flags(warehouse, [
        ("600001.SH", D[0], False), ("600001.SH", D[1], True),
        ("600001.SH", D[2], False),
    ])
    out = candidate_stock_days(warehouse, D[0], D[-1])
    assert out.rows() == [("600001.SH", D[2])]


def test_window_extends_over_following_sessions(warehouse):
    seed_flags(warehouse, [
        ("600001.SH", D[0], True), ("600001.SH", D[1], False),
        ("600001.SH", D[2], False), ("600001.SH", D[3], False),
    ])
    out = candidate_stock_days(warehouse, D[0], D[-1], window=3)
    assert out["dt"].to_list() == [D[1], D[2], D[3]]


def test_uses_trading_sessions_not_calendar_days(warehouse):
    """周末不是交易日。首板在周五，候选日是下周一。"""
    seed_flags(warehouse, [
        ("600001.SH", D[3], True),   # 1/5 周五
        ("600001.SH", D[4], False),  # 1/8 周一
    ])
    out = candidate_stock_days(warehouse, D[0], D[-1])
    assert out.rows() == [("600001.SH", D[4])]


def test_last_first_board_has_no_following_session(warehouse):
    """区间最后一天首板，没有次日可给——不该造出不存在的日期。"""
    seed_flags(warehouse, [("600001.SH", D[-1], True)])
    assert candidate_stock_days(warehouse, D[0], D[-1]).height == 0


def test_non_first_boards_are_excluded(warehouse):
    seed_flags(warehouse, [
        ("600001.SH", D[0], False), ("600001.SH", D[1], False),
    ])
    assert candidate_stock_days(warehouse, D[0], D[-1]).height == 0


def test_result_is_deduplicated_and_sorted(warehouse):
    """两次首板的窗口可能重叠，同一股-日只该出现一次。"""
    seed_flags(warehouse, [
        ("600001.SH", D[0], True), ("600001.SH", D[1], True),
        ("600001.SH", D[2], False), ("600001.SH", D[3], False),
    ])
    out = candidate_stock_days(warehouse, D[0], D[-1], window=2)
    assert out.height == out.unique().height
    assert out["dt"].to_list() == sorted(out["dt"].to_list())


def test_empty_warehouse_yields_nothing(warehouse):
    assert candidate_stock_days(warehouse, D[0], D[-1]).height == 0


def test_manifest_is_written_as_csv(warehouse, tmp_path):
    seed_flags(warehouse, [
        ("600001.SH", D[0], True), ("600001.SH", D[1], False),
    ])
    out = candidate_stock_days(warehouse, D[0], D[-1])
    path = tmp_path / "manifest.csv"
    assert write_manifest(out, path) == 1
    text = path.read_text()
    assert "symbol,dt" in text
    assert "600001.SH,2024-01-03" in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/test_candidates.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.pipeline.candidates'`

- [ ] **Step 3: 实现 `src/qsys/data/pipeline/candidates.py`**

```python
"""候选股-日选择器：决定要下载哪些股在哪些日的分钟线。

这是唯一决定分钟线数据量的地方。实测（894 个交易日）：
  window=1  41,592 组 ≈ 1000 万根
  window=3 122,936 组 ≈ 2950 万根
  window=6 232,162 组 ≈ 5570 万根
对照：全市场全历史约 10.2 亿根。

**交易日推进，不是日历日推进**：首板在周五，候选日是下周一。
交易日序列直接取自 daily_flags 里实际出现过的日期——那本就是从真实
日线派生的，比另配一份日历更不容易走样。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import structlog

from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

MANIFEST_COLUMNS = ["symbol", "dt"]


def candidate_stock_days(
    warehouse: Warehouse,
    start: date,
    end: date,
    *,
    window: int = 1,
) -> pl.DataFrame:
    """首板之后 `window` 个交易日的（标的, 日期）组合。"""
    if window < 1:
        raise ValueError(f"window must be >= 1: {window}")

    flags = WarehouseQuery(warehouse).daily_flags(start, end)
    empty = pl.DataFrame(schema={"symbol": pl.Utf8, "dt": pl.Date})
    if flags.height == 0:
        return empty

    sessions = sorted(flags["dt"].unique().to_list())
    position = {d: i for i, d in enumerate(sessions)}

    boards = flags.filter(pl.col("is_first_board")).select(["symbol", "dt"])
    if boards.height == 0:
        return empty

    pairs: set[tuple[str, date]] = set()
    for symbol, day in zip(boards["symbol"].to_list(), boards["dt"].to_list()):
        origin = position[day]
        for step in range(1, window + 1):
            nxt = origin + step
            if nxt < len(sessions):
                pairs.add((symbol, sessions[nxt]))

    if not pairs:
        return empty

    return (
        pl.DataFrame(
            {"symbol": [p[0] for p in pairs], "dt": [p[1] for p in pairs]},
            schema={"symbol": pl.Utf8, "dt": pl.Date},
        )
        .unique()
        .sort(["dt", "symbol"])
    )


def write_manifest(frame: pl.DataFrame, path: Path) -> int:
    """写出 Windows 下载脚本的输入清单。返回行数。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.select(MANIFEST_COLUMNS).write_csv(path)
    log.info("manifest.written", path=str(path), rows=frame.height)
    return frame.height
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/data/test_candidates.py -q`
Expected: PASS —— 8 passed

- [ ] **Step 5: 写导出脚本**

Create `scripts/export_minute_manifest.py`:

```python
"""导出待下载的分钟线清单，供 Windows 侧的下载脚本消费。

用法：
  uv run python scripts/export_minute_manifest.py \
      --start 2023-01-01 --end 2026-09-09 --out var/minute_manifest.csv
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from qsys.data.pipeline.candidates import candidate_stock_days, write_manifest
from qsys.data.storage import Warehouse


def main() -> int:
    parser = argparse.ArgumentParser(description="导出分钟线下载清单")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--out", default="var/minute_manifest.csv")
    parser.add_argument("--window", type=int, default=1,
                        help="首板后取几个交易日；策略移植后如需更长可调大")
    args = parser.parse_args()

    frame = candidate_stock_days(
        Warehouse(Path(args.warehouse)),
        date.fromisoformat(args.start),
        date.fromisoformat(args.end),
        window=args.window,
    )
    rows = write_manifest(frame, Path(args.out))
    print(f"候选股-日 {rows:,} 组  ≈ {rows * 240 / 1e6:.1f}M 根分钟线")
    print(f"已写入 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: 提交**

```bash
git add src/qsys/data/pipeline/candidates.py scripts/export_minute_manifest.py \
        tests/data/test_candidates.py
git commit -m "feat(data): 候选股-日选择器与下载清单导出"
```

---

## Task 3: 分钟线导入（传输无关、幂等）

Windows 把 parquet 丢进一个目录，Linux 从目录导入。**传输方式（rsync/scp/共享盘）不进代码**——换传输不该改导入逻辑。

**Files:**
- Create: `src/qsys/data/pipeline/minutes.py`
- Test: `tests/data/test_minutes_import.py`

**Interfaces:**
- Produces: `import_minute_bars(warehouse, incoming_dir, *, archive_dir=None) -> ImportResult`；`ImportResult(files, rows, skipped, errors)`

- [ ] **Step 1: 写失败测试**

Create `tests/data/test_minutes_import.py`:

```python
from datetime import datetime

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from qsys.data import schema
from qsys.data.pipeline.minutes import import_minute_bars
from qsys.data.query import WarehouseQuery

T = datetime(2024, 1, 2, 9, 31)


def write_incoming(directory, name, symbol="600036.SH", n=3, start=T):
    directory.mkdir(parents=True, exist_ok=True)
    times = [start.replace(minute=start.minute + i) for i in range(n)]
    table = pa.table({
        "symbol": [symbol] * n, "dt": times,
        "open": [10.0] * n, "high": [10.5] * n, "low": [9.5] * n,
        "close": [10.2] * n, "volume": [100] * n, "amount": [1020.0] * n,
    }, schema=schema.MINUTE_BARS)
    pq.write_table(table, directory / name)
    return directory / name


def test_imports_a_single_file(warehouse, tmp_path):
    write_incoming(tmp_path / "in", "a.parquet")
    result = import_minute_bars(warehouse, tmp_path / "in")
    assert result.files == 1
    assert result.rows == 3
    df = WarehouseQuery(warehouse).minute_bars(
        None, datetime(2024, 1, 1), datetime(2024, 1, 3))
    assert df.height == 3


def test_imports_multiple_files(warehouse, tmp_path):
    write_incoming(tmp_path / "in", "a.parquet", symbol="600036.SH")
    write_incoming(tmp_path / "in", "b.parquet", symbol="000001.SZ")
    result = import_minute_bars(warehouse, tmp_path / "in")
    assert result.files == 2
    assert result.rows == 6


def test_reimporting_the_same_data_is_idempotent(warehouse, tmp_path):
    write_incoming(tmp_path / "in", "a.parquet")
    import_minute_bars(warehouse, tmp_path / "in")
    write_incoming(tmp_path / "in", "a.parquet")
    import_minute_bars(warehouse, tmp_path / "in")
    df = WarehouseQuery(warehouse).minute_bars(
        None, datetime(2024, 1, 1), datetime(2024, 1, 3))
    assert df.height == 3


def test_a_bad_file_does_not_abort_the_batch(warehouse, tmp_path):
    incoming = tmp_path / "in"
    write_incoming(incoming, "good.parquet")
    (incoming / "bad.parquet").write_bytes(b"not a parquet file")
    result = import_minute_bars(warehouse, incoming)
    assert result.rows == 3
    assert result.errors == 1


def test_wrong_schema_is_rejected_not_written(warehouse, tmp_path):
    incoming = tmp_path / "in"
    incoming.mkdir(parents=True)
    pq.write_table(pa.table({"symbol": ["x"], "oops": [1]}), incoming / "wrong.parquet")
    result = import_minute_bars(warehouse, incoming)
    assert result.errors == 1
    assert result.rows == 0


def test_archived_files_are_moved_out_of_incoming(warehouse, tmp_path):
    incoming, archive = tmp_path / "in", tmp_path / "done"
    write_incoming(incoming, "a.parquet")
    import_minute_bars(warehouse, incoming, archive_dir=archive)
    assert list(incoming.glob("*.parquet")) == []
    assert len(list(archive.glob("*.parquet"))) == 1


def test_files_stay_put_when_no_archive_given(warehouse, tmp_path):
    incoming = tmp_path / "in"
    write_incoming(incoming, "a.parquet")
    import_minute_bars(warehouse, incoming)
    assert len(list(incoming.glob("*.parquet"))) == 1


def test_missing_directory_is_a_no_op(warehouse, tmp_path):
    result = import_minute_bars(warehouse, tmp_path / "nope")
    assert result.files == 0
    assert result.rows == 0


def test_data_spanning_two_years_lands_in_both_partitions(warehouse, tmp_path):
    incoming = tmp_path / "in"
    write_incoming(incoming, "a.parquet", start=datetime(2023, 12, 29, 14, 58))
    write_incoming(incoming, "b.parquet", start=datetime(2024, 1, 2, 9, 31))
    import_minute_bars(warehouse, incoming)
    q = WarehouseQuery(warehouse)
    assert q.minute_bars(None, datetime(2023, 1, 1), datetime(2023, 12, 31)).height == 3
    assert q.minute_bars(None, datetime(2024, 1, 1), datetime(2024, 12, 31)).height == 3
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/test_minutes_import.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.pipeline.minutes'`

- [ ] **Step 3: 实现 `src/qsys/data/pipeline/minutes.py`**

```python
"""分钟线导入。

Windows 侧把 parquet 丢进一个目录，这里从目录导入。**传输方式不进代码**
——rsync 也好、共享盘也好，换传输不该改导入逻辑。

单个坏文件不中断整批：跨机传输中断出半个文件是常态，一个坏文件让整批
白导才是问题。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

_DATASET = "minute_bars"
_COLUMNS = list(schema.MINUTE_BARS.names)


@dataclass(frozen=True, slots=True)
class ImportResult:
    files: int
    rows: int
    errors: int


def import_minute_bars(
    warehouse: Warehouse,
    incoming_dir: Path,
    *,
    archive_dir: Path | None = None,
) -> ImportResult:
    """导入目录下所有 parquet。返回统计。"""
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
            log.warning("minutes.bad_file", path=str(path), error=str(exc))

    rows = 0
    if frames:
        fresh = pl.concat(frames, how="vertical")
        rows = _write_by_year(warehouse, fresh)

    if archive_dir is not None:
        archive = Path(archive_dir)
        archive.mkdir(parents=True, exist_ok=True)
        for path in good:
            shutil.move(str(path), str(archive / path.name))

    log.info("minutes.imported", files=len(good), rows=rows, errors=errors)
    return ImportResult(len(good), rows, errors)


def _write_by_year(warehouse: Warehouse, fresh: pl.DataFrame) -> int:
    query = WarehouseQuery(warehouse)
    written = 0
    for year in sorted({t.year for t in fresh["dt"].to_list()}):
        lo = datetime(year, 1, 1)
        hi = datetime(year, 12, 31, 23, 59, 59)
        incoming = fresh.filter(pl.col("dt").is_between(lo, hi))
        merged = (
            pl.concat([query.minute_bars(None, lo, hi), incoming], how="vertical")
            # 重复导入覆盖旧值；maintain_order 让「保留最后一条」有确定语义
            .unique(subset=["symbol", "dt"], keep="last", maintain_order=True)
            .sort(["symbol", "dt"])
            .select(_COLUMNS)
        )
        warehouse.write(
            _DATASET, merged.to_arrow().cast(schema.MINUTE_BARS), year=year
        )
        written += incoming.height
    return written
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/data/test_minutes_import.py -q`
Expected: PASS —— 9 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/pipeline/minutes.py tests/data/test_minutes_import.py
git commit -m "feat(data): 分钟线导入（传输无关、幂等）"
```

---

## Task 4: 分钟线 PIT 契约

**这是本计划里最要紧的一块。** 策略要在 09:31 看竞价量、在 14:50 看当时价格——只要 API 能返回 as_of 之后的 bar，未来函数就会悄悄混进策略。

**Files:**
- Create: `src/qsys/data/minute_feed.py`
- Test: `tests/data/test_minute_feed.py`

**Interfaces:**
- Consumes: `WarehouseQuery.minute_bars`（Task 1）
- Produces: `MinuteFeed(query)`；`get(symbols, start, end, *, as_of) -> pl.DataFrame`；`session_bars(symbols, day, *, as_of)`；`first_bar(symbols, day, *, as_of)`

- [ ] **Step 1: 写失败测试**

Create `tests/data/test_minute_feed.py`:

```python
from datetime import date, datetime

import pyarrow as pa
import pytest

from qsys.data import schema
from qsys.data.minute_feed import MinuteFeed
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

DAY = date(2024, 1, 2)


def seed(warehouse: Warehouse, symbol="600036.SH") -> None:
    """09:31 ~ 09:35 与 14:57 ~ 15:00 各若干根，价格递增便于断言。"""
    times = [datetime(2024, 1, 2, 9, 30 + i) for i in range(1, 6)]
    times += [datetime(2024, 1, 2, 14, 57), datetime(2024, 1, 2, 14, 58),
              datetime(2024, 1, 2, 14, 59), datetime(2024, 1, 2, 15, 0)]
    n = len(times)
    warehouse.write("minute_bars", pa.table({
        "symbol": [symbol] * n, "dt": times,
        "open": [float(i) for i in range(n)], "high": [float(i) for i in range(n)],
        "low": [float(i) for i in range(n)], "close": [float(i) for i in range(n)],
        "volume": [100 + i for i in range(n)], "amount": [0.0] * n,
    }, schema=schema.MINUTE_BARS), year=2024)


@pytest.fixture
def feed(warehouse):
    seed(warehouse)
    return MinuteFeed(WarehouseQuery(warehouse))


def test_returns_bars_up_to_as_of(feed):
    df = feed.get(["600036.SH"], datetime(2024, 1, 2, 9, 0),
                  datetime(2024, 1, 2, 15, 0), as_of=datetime(2024, 1, 2, 9, 33))
    assert df["dt"].to_list() == [
        datetime(2024, 1, 2, 9, 31), datetime(2024, 1, 2, 9, 32),
        datetime(2024, 1, 2, 9, 33),
    ]


def test_a_bar_is_visible_exactly_at_its_close_time(feed):
    """09:30–09:31 这根记 09:31，在 09:31 整可见，09:30:59 不可见。"""
    at_close = feed.get(["600036.SH"], datetime(2024, 1, 2, 9, 0),
                        datetime(2024, 1, 2, 15, 0), as_of=datetime(2024, 1, 2, 9, 31))
    assert at_close.height == 1
    before = feed.get(["600036.SH"], datetime(2024, 1, 2, 9, 0),
                      datetime(2024, 1, 2, 15, 0),
                      as_of=datetime(2024, 1, 2, 9, 30, 59))
    assert before.height == 0


def test_peeking_at_the_close_from_the_morning_returns_nothing(feed):
    """反向测试：早盘想看尾盘价，必须什么都拿不到。"""
    df = feed.get(["600036.SH"], datetime(2024, 1, 2, 14, 50),
                  datetime(2024, 1, 2, 15, 0), as_of=datetime(2024, 1, 2, 9, 35))
    assert df.height == 0


def test_as_of_before_all_data_returns_nothing(feed):
    df = feed.get(["600036.SH"], datetime(2024, 1, 2, 0, 0),
                  datetime(2024, 1, 2, 23, 59), as_of=datetime(2024, 1, 1, 15, 0))
    assert df.height == 0


def test_requested_end_still_caps_the_result(feed):
    """as_of 很晚也不该越过请求的 end。"""
    df = feed.get(["600036.SH"], datetime(2024, 1, 2, 9, 0),
                  datetime(2024, 1, 2, 9, 32), as_of=datetime(2024, 1, 3, 15, 0))
    assert df["dt"].to_list() == [
        datetime(2024, 1, 2, 9, 31), datetime(2024, 1, 2, 9, 32)]


def test_session_bars_covers_one_trading_day(feed):
    df = feed.session_bars(["600036.SH"], DAY, as_of=datetime(2024, 1, 2, 15, 0))
    assert df.height == 9


def test_first_bar_is_the_auction_bar(feed):
    """首根分钟线含集合竞价成交量——策略判高开/低开的依据。"""
    row = feed.first_bar(["600036.SH"], DAY, as_of=datetime(2024, 1, 2, 9, 35))
    assert row.height == 1
    assert row["dt"].to_list() == [datetime(2024, 1, 2, 9, 31)]


def test_first_bar_is_invisible_before_it_closes(feed):
    row = feed.first_bar(["600036.SH"], DAY, as_of=datetime(2024, 1, 2, 9, 30))
    assert row.height == 0


def test_empty_symbol_list_returns_nothing(feed):
    df = feed.get([], datetime(2024, 1, 2, 9, 0), datetime(2024, 1, 2, 15, 0),
                  as_of=datetime(2024, 1, 2, 15, 0))
    assert df.height == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/test_minute_feed.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.minute_feed'`

- [ ] **Step 3: 实现 `src/qsys/data/minute_feed.py`**

```python
"""分钟线的 PIT 契约。

日线的可见时刻是收盘；分钟线的可见时刻就是**这根 bar 自己的收盘时刻**——
存储约定 dt 即该分钟的收盘时刻（09:30–09:31 这根记 09:31），所以可见性
退化成一句 `dt <= as_of`，没有差一分钟的歧义。

策略要在 09:31 看竞价量、在 14:50 看当时价格。只要这里能返回 as_of
之后的 bar，未来函数就会悄悄混进策略——反向测试守着这条。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time

import polars as pl

from qsys.data.query import WarehouseQuery

# A 股交易时段：09:30–11:30、13:00–15:00。以收盘时刻标注，
# 首根记 09:31，末根记 15:00。
SESSION_OPEN = time(9, 31)
SESSION_CLOSE = time(15, 0)


class MinuteFeed:
    def __init__(self, query: WarehouseQuery) -> None:
        self._query = query

    def get(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
        *,
        as_of: datetime,
    ) -> pl.DataFrame:
        """返回 [start, min(end, as_of)] 区间内的分钟线。"""
        effective_end = min(end, as_of)
        if effective_end < start:
            return self._query.minute_bars(symbols, start, start).clear()
        return self._query.minute_bars(symbols, start, effective_end)

    def session_bars(
        self, symbols: Sequence[str], day: date, *, as_of: datetime
    ) -> pl.DataFrame:
        """某个交易日当天的全部可见分钟线。"""
        return self.get(
            symbols,
            datetime.combine(day, SESSION_OPEN),
            datetime.combine(day, SESSION_CLOSE),
            as_of=as_of,
        )

    def first_bar(
        self, symbols: Sequence[str], day: date, *, as_of: datetime
    ) -> pl.DataFrame:
        """当日首根分钟线，每个标的一行。

        首根含集合竞价成交量——策略判高开/低开的依据。2-1a 已实测
        首根 open 与日线 open 相等（竞价成交价即开盘价）。
        """
        bars = self.session_bars(symbols, day, as_of=as_of)
        if bars.height == 0:
            return bars
        return (
            bars.sort(["symbol", "dt"])
            .group_by("symbol", maintain_order=True)
            .first()
            .select(bars.columns)
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/data/test_minute_feed.py -q`
Expected: PASS —— 9 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/minute_feed.py tests/data/test_minute_feed.py
git commit -m "feat(data): 分钟线 PIT 契约"
```

---

## Task 5: 分钟线质量校验

分钟线来自 QMT，日线来自 mootdx——**两个独立数据源**。它们必须对得上，对不上说明至少一个错了。这是本项目能拿到的最强的数据交叉验证。

**Files:**
- Create: `src/qsys/data/quality/minute_rules.py`
- Test: `tests/data/test_minute_quality.py`

**Interfaces:**
- Produces: `check_minute_bars(warehouse, day) -> list[MinuteIssue]`；`MinuteIssue(symbol, dt, rule, detail)`

- [ ] **Step 1: 写失败测试**

Create `tests/data/test_minute_quality.py`:

```python
from datetime import date, datetime

import pyarrow as pa
import pytest

from qsys.data import schema
from qsys.data.quality.minute_rules import check_minute_bars
from qsys.data.storage import Warehouse

DAY = date(2024, 1, 2)


def seed(warehouse: Warehouse, minutes, daily=None, symbol="600036.SH"):
    """minutes: [(hh, mm, o, h, l, c, vol)]；daily: (o,h,l,c,vol) 或 None"""
    n = len(minutes)
    warehouse.write("minute_bars", pa.table({
        "symbol": [symbol] * n,
        "dt": [datetime(2024, 1, 2, m[0], m[1]) for m in minutes],
        "open": [float(m[2]) for m in minutes], "high": [float(m[3]) for m in minutes],
        "low": [float(m[4]) for m in minutes], "close": [float(m[5]) for m in minutes],
        "volume": [int(m[6]) for m in minutes], "amount": [0.0] * n,
    }, schema=schema.MINUTE_BARS), year=2024)
    if daily is not None:
        warehouse.write("bars_daily", pa.table({
            "symbol": [symbol], "dt": [DAY],
            "open": [float(daily[0])], "high": [float(daily[1])],
            "low": [float(daily[2])], "close": [float(daily[3])],
            "volume": [int(daily[4])], "amount": [0.0],
        }, schema=schema.BARS_DAILY), year=2024)


def rules_of(issues):
    return {i.rule for i in issues}


def test_clean_data_has_no_issues(warehouse):
    seed(warehouse,
         [(9, 31, 10.0, 10.2, 9.9, 10.1, 600), (15, 0, 10.1, 10.3, 10.0, 10.2, 400)],
         daily=(10.0, 10.3, 9.9, 10.2, 1000))
    assert check_minute_bars(warehouse, DAY) == []


def test_ohlc_inconsistency_is_flagged(warehouse):
    seed(warehouse, [(9, 31, 10.0, 9.0, 9.9, 10.1, 600)])  # high < open
    assert "ohlc" in rules_of(check_minute_bars(warehouse, DAY))


def test_duplicate_timestamps_are_flagged(warehouse):
    seed(warehouse,
         [(9, 31, 10.0, 10.2, 9.9, 10.1, 600), (9, 31, 10.0, 10.2, 9.9, 10.1, 600)])
    assert "duplicate_dt" in rules_of(check_minute_bars(warehouse, DAY))


def test_volume_mismatch_against_the_daily_bar_is_flagged(warehouse):
    """分钟线来自 QMT、日线来自 mootdx——两个独立源对不上就是有问题。"""
    seed(warehouse, [(9, 31, 10.0, 10.2, 9.9, 10.1, 600)],
         daily=(10.0, 10.3, 9.9, 10.2, 5000))
    assert "volume_vs_daily" in rules_of(check_minute_bars(warehouse, DAY))


def test_high_mismatch_against_the_daily_bar_is_flagged(warehouse):
    seed(warehouse, [(9, 31, 10.0, 10.2, 9.9, 10.1, 1000)],
         daily=(10.0, 99.0, 9.9, 10.1, 1000))
    assert "high_vs_daily" in rules_of(check_minute_bars(warehouse, DAY))


def test_first_bar_open_must_equal_the_daily_open(warehouse):
    """竞价成交价即开盘价，2-1a 已实测。对不上说明时间戳约定错了。"""
    seed(warehouse, [(9, 31, 88.0, 88.0, 88.0, 88.0, 1000)],
         daily=(10.0, 88.0, 9.9, 88.0, 1000))
    assert "first_bar_open" in rules_of(check_minute_bars(warehouse, DAY))


def test_timestamps_outside_trading_hours_are_flagged(warehouse):
    seed(warehouse, [(3, 15, 10.0, 10.2, 9.9, 10.1, 600)])
    assert "outside_session" in rules_of(check_minute_bars(warehouse, DAY))


def test_missing_daily_bar_skips_the_cross_checks(warehouse):
    """没有日线可比不是分钟线的错，不该报。"""
    seed(warehouse, [(9, 31, 10.0, 10.2, 9.9, 10.1, 600)], daily=None)
    assert rules_of(check_minute_bars(warehouse, DAY)) == set()


def test_no_minute_data_yields_no_issues(warehouse):
    assert check_minute_bars(warehouse, DAY) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/test_minute_quality.py -q`
Expected: FAIL —— `ModuleNotFoundError`

- [ ] **Step 3: 实现 `src/qsys/data/quality/minute_rules.py`**

```python
"""分钟线质量校验。

分钟线来自 QMT、日线来自 mootdx，**两个独立数据源**。它们必须对得上：
  · 分钟成交量之和 ≈ 日线成交量
  · 分钟最高价之最大 == 日线最高价
  · 首根分钟线的 open == 日线 open（竞价成交价即开盘价，2-1a 已实测）
对不上说明至少一个源错了，或时间戳约定弄反了。这是本项目能拿到的最强的
数据交叉验证——用两次都错得一模一样的概率很低。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

import polars as pl
import structlog

from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

# 成交量允许 1% 偏差：两源对停牌集合竞价、盘后固定价的归属口径可能不同
_VOLUME_TOLERANCE = 0.01
_PRICE_TOLERANCE = 0.005  # 分

_MORNING = (time(9, 31), time(11, 30))
_AFTERNOON = (time(13, 1), time(15, 0))


@dataclass(frozen=True, slots=True)
class MinuteIssue:
    symbol: str
    dt: date
    rule: str
    detail: str


def _in_session(t: time) -> bool:
    return (_MORNING[0] <= t <= _MORNING[1]) or (_AFTERNOON[0] <= t <= _AFTERNOON[1])


def check_minute_bars(warehouse: Warehouse, day: date) -> list[MinuteIssue]:
    """校验某个交易日的分钟线。返回问题列表，空表示干净。"""
    query = WarehouseQuery(warehouse)
    minutes = query.minute_bars(
        None, datetime.combine(day, time(0, 0)), datetime.combine(day, time(23, 59, 59))
    )
    if minutes.height == 0:
        return []

    daily = query.daily_bars(None, day, day)
    daily_by_symbol = {
        row["symbol"]: row for row in daily.iter_rows(named=True)
    } if daily.height else {}

    issues: list[MinuteIssue] = []

    for symbol, group in minutes.sort(["symbol", "dt"]).group_by(
        "symbol", maintain_order=True
    ):
        name = symbol[0] if isinstance(symbol, tuple) else symbol

        if group["dt"].n_unique() != group.height:
            issues.append(MinuteIssue(name, day, "duplicate_dt", "同一时刻多根 bar"))

        bad_ohlc = group.filter(
            (pl.col("high") < pl.col("low"))
            | (pl.col("high") < pl.col("open"))
            | (pl.col("high") < pl.col("close"))
            | (pl.col("low") > pl.col("open"))
            | (pl.col("low") > pl.col("close"))
        )
        if bad_ohlc.height:
            issues.append(
                MinuteIssue(name, day, "ohlc", f"{bad_ohlc.height} 根 OHLC 不自洽")
            )

        outside = [t for t in group["dt"].to_list() if not _in_session(t.time())]
        if outside:
            issues.append(
                MinuteIssue(name, day, "outside_session", f"{len(outside)} 根在交易时段外")
            )

        reference = daily_by_symbol.get(name)
        if reference is None:
            continue  # 没有日线可比不是分钟线的错

        minute_volume = int(group["volume"].sum())
        if reference["volume"] > 0:
            drift = abs(minute_volume - reference["volume"]) / reference["volume"]
            if drift > _VOLUME_TOLERANCE:
                issues.append(
                    MinuteIssue(
                        name, day, "volume_vs_daily",
                        f"分钟合计 {minute_volume} vs 日线 {reference['volume']}",
                    )
                )

        minute_high = float(group["high"].max())
        if abs(minute_high - reference["high"]) > _PRICE_TOLERANCE:
            issues.append(
                MinuteIssue(
                    name, day, "high_vs_daily",
                    f"分钟最高 {minute_high} vs 日线 {reference['high']}",
                )
            )

        first_open = float(group["open"].to_list()[0])
        if abs(first_open - reference["open"]) > _PRICE_TOLERANCE:
            issues.append(
                MinuteIssue(
                    name, day, "first_bar_open",
                    f"首根 open {first_open} vs 日线 open {reference['open']}",
                )
            )

    if issues:
        log.warning("minutes.quality_issues", day=str(day), count=len(issues))
    return issues
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/data/test_minute_quality.py -q`
Expected: PASS —— 9 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/quality/minute_rules.py tests/data/test_minute_quality.py
git commit -m "feat(data): 分钟线质量校验（与日线交叉验证）"
```

---

## Task 6: 端到端（Linux 侧全链路）

**Files:**
- Create: `tests/test_minute_e2e.py`

- [ ] **Step 1: 写端到端测试**

Create `tests/test_minute_e2e.py`:

```python
"""分钟线链路端到端：候选选择 -> 导入 -> PIT 读取 -> 质量校验。

Windows 下载那一段不在此覆盖——它依赖 xtquant，Linux 上装不了。
这里从「parquet 已经落到 incoming 目录」开始。
"""

from datetime import date, datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from qsys.data import schema
from qsys.data.minute_feed import MinuteFeed
from qsys.data.pipeline.candidates import candidate_stock_days, write_manifest
from qsys.data.pipeline.minutes import import_minute_bars
from qsys.data.quality.minute_rules import check_minute_bars
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

D = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
SYMBOL = "600001.SH"


def seed_daily_and_flags(warehouse: Warehouse) -> None:
    """D0 平走、D1 首板涨停、D2 次日——D2 就是候选日。"""
    rows = [(D[0], 10.0, 10.0, 10.0, 10.0), (D[1], 11.0, 11.0, 11.0, 11.0),
            (D[2], 11.5, 11.9, 11.4, 11.8)]
    warehouse.write("bars_daily", pa.table({
        "symbol": [SYMBOL] * 3, "dt": [r[0] for r in rows],
        "open": [r[1] for r in rows], "high": [r[2] for r in rows],
        "low": [r[3] for r in rows], "close": [r[4] for r in rows],
        "volume": [1000] * 3, "amount": [0.0] * 3,
    }, schema=schema.BARS_DAILY), year=2024)
    warehouse.write("instruments", pa.table({
        "symbol": [SYMBOL], "name": ["x"], "market": ["SH"],
        "list_date": [None], "delist_date": [None], "board": ["main"],
    }, schema=schema.INSTRUMENTS))
    from qsys.data.pipeline.flags import build_daily_flags

    build_daily_flags(warehouse, D[0], D[2])


def write_minutes_for_candidate(directory, volumes=(600, 400)):
    """候选日 D2 的两根分钟线，合计与日线成交量一致。"""
    directory.mkdir(parents=True, exist_ok=True)
    n = 2
    pq.write_table(pa.table({
        "symbol": [SYMBOL] * n,
        "dt": [datetime(2024, 1, 4, 9, 31), datetime(2024, 1, 4, 15, 0)],
        "open": [11.5, 11.7], "high": [11.6, 11.9],
        "low": [11.4, 11.6], "close": [11.6, 11.8],
        "volume": list(volumes), "amount": [0.0] * n,
    }, schema=schema.MINUTE_BARS), directory / "d2.parquet")


@pytest.fixture
def rig(warehouse, tmp_path):
    seed_daily_and_flags(warehouse)
    return warehouse, tmp_path


def test_candidate_day_is_the_session_after_the_first_board(rig):
    warehouse, _ = rig
    assert candidate_stock_days(warehouse, D[0], D[2]).rows() == [(SYMBOL, D[2])]


def test_manifest_feeds_the_download_list(rig):
    warehouse, tmp_path = rig
    frame = candidate_stock_days(warehouse, D[0], D[2])
    assert write_manifest(frame, tmp_path / "m.csv") == 1


def test_imported_minutes_become_readable(rig):
    warehouse, tmp_path = rig
    write_minutes_for_candidate(tmp_path / "in")
    assert import_minute_bars(warehouse, tmp_path / "in").rows == 2
    df = WarehouseQuery(warehouse).minute_bars(
        None, datetime(2024, 1, 4), datetime(2024, 1, 5))
    assert df.height == 2


def test_pit_hides_the_afternoon_bar_from_the_morning(rig):
    """全链路的关键：早盘读不到尾盘。"""
    warehouse, tmp_path = rig
    write_minutes_for_candidate(tmp_path / "in")
    import_minute_bars(warehouse, tmp_path / "in")
    feed = MinuteFeed(WarehouseQuery(warehouse))
    morning = feed.session_bars([SYMBOL], D[2], as_of=datetime(2024, 1, 4, 9, 31))
    assert morning.height == 1
    full = feed.session_bars([SYMBOL], D[2], as_of=datetime(2024, 1, 4, 15, 0))
    assert full.height == 2


def test_first_bar_open_matches_the_daily_open(rig):
    """跨源一致：分钟首根 open 必须等于日线 open。"""
    warehouse, tmp_path = rig
    write_minutes_for_candidate(tmp_path / "in")
    import_minute_bars(warehouse, tmp_path / "in")
    feed = MinuteFeed(WarehouseQuery(warehouse))
    row = feed.first_bar([SYMBOL], D[2], as_of=datetime(2024, 1, 4, 15, 0))
    assert row["open"].to_list() == [11.5]


def test_quality_check_passes_on_consistent_data(rig):
    warehouse, tmp_path = rig
    write_minutes_for_candidate(tmp_path / "in")
    import_minute_bars(warehouse, tmp_path / "in")
    assert check_minute_bars(warehouse, D[2]) == []


def test_quality_check_catches_a_volume_mismatch(rig):
    """分钟合计与日线对不上必须被抓到——这是两源交叉验证的意义。"""
    warehouse, tmp_path = rig
    write_minutes_for_candidate(tmp_path / "in", volumes=(60, 40))
    import_minute_bars(warehouse, tmp_path / "in")
    rules = {i.rule for i in check_minute_bars(warehouse, D[2])}
    assert "volume_vs_daily" in rules


def test_reimport_is_idempotent(rig):
    warehouse, tmp_path = rig
    write_minutes_for_candidate(tmp_path / "in")
    import_minute_bars(warehouse, tmp_path / "in")
    write_minutes_for_candidate(tmp_path / "in")
    import_minute_bars(warehouse, tmp_path / "in")
    df = WarehouseQuery(warehouse).minute_bars(
        None, datetime(2024, 1, 4), datetime(2024, 1, 5))
    assert df.height == 2
```

- [ ] **Step 2: 运行测试确认通过**

Run: `uv run pytest tests/test_minute_e2e.py -q`
Expected: PASS —— 8 passed

- [ ] **Step 3: 运行全部测试**

Run: `uv run pytest -q`
Expected: PASS —— 2-1a 的 719 项无回归 + 本计划新增项

- [ ] **Step 4: 提交**

```bash
git add tests/test_minute_e2e.py
git commit -m "feat(data): 分钟线链路端到端"
```

---

## Task 7: Windows 下载脚本（需先探针）

> **本任务需要 Windows 机器访问权，尚未取得。前 6 个任务不依赖它，可先行完成。**

**Files:**
- Create: `scripts/win_download_minutes.py`
- Create: `scripts/win_probe_minutes.py`

**为什么先探针**：计划 1-1 的「先录真实接口结构再写解析器」抓到过 4 个真问题，
包括 polars 里 NaN 不等于 null 那个会静默污染复权因子的坑。xtquant 的三件事
必须实测，不能猜：

1. **时间戳是开盘时刻还是收盘时刻** —— 直接决定 PIT 是否差一分钟。若 QMT 给
   09:30 表示 09:30–09:31 这根，导出时必须 +1 分钟
2. **集合竞价成交量归在哪根** —— 折进首根，还是单列一根 09:25
3. **单次下载耗时** —— 决定 41,592 组要跑多久，以及是否需要改按标的下全历史

- [ ] **Step 1: 写探针脚本**

Create `scripts/win_probe_minutes.py`:

```python
"""Windows 侧探针：实测 xtdata 分钟线的真实形状与吞吐。

**只在 Windows 上运行**，结果记录到 docs/deploy/windows-node.md。
不要凭猜测写导出逻辑——时间戳约定差一分钟，整个 PIT 契约就是错的。

用法（Windows PowerShell）：
  python scripts\\win_probe_minutes.py --symbols 600036.SH,000001.SZ --day 2024-01-04
"""

from __future__ import annotations

import argparse
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(description="xtdata 分钟线探针")
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--day", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()

    from xtquant import xtdata

    codes = [s.strip() for s in args.symbols.split(",") if s.strip()]
    stamp = args.day.replace("-", "")

    for code in codes:
        print(f"\n=== {code} {args.day} ===")
        started = time.time()
        xtdata.download_history_data(code, period="1m", start_time=stamp, end_time=stamp)
        elapsed = time.time() - started
        print(f"download_history_data 耗时 {elapsed:.2f}s")

        data = xtdata.get_market_data(
            field_list=[], stock_list=[code], period="1m",
            start_time=stamp, end_time=stamp, dividend_type="none",
        )
        print("返回类型:", type(data))
        if isinstance(data, dict):
            print("字段:", list(data.keys()))
            for field, frame in data.items():
                print(f"\n--- {field} ---")
                print("index:", list(frame.index)[:3])
                cols = list(frame.columns)
                print(f"列数 {len(cols)}  前 5 列: {cols[:5]}  后 3 列: {cols[-3:]}")
                print(frame.iloc[:, :5].head(3))
                break
    print("\n关注三点：")
    print("  1. 时间戳是 09:30 还是 09:31 —— 决定导出时是否要 +1 分钟")
    print("  2. 有没有单独的 09:25 竞价 bar")
    print("  3. 单次 download 耗时 × 41,592 组 = 全量估时")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 在 Windows 上运行探针，把结果记进 `docs/deploy/windows-node.md`**

必须回答：时间戳约定、竞价 bar 归属、单次耗时。

- [ ] **Step 3: 依据探针结果实现 `scripts/win_download_minutes.py`**

> 具体的时间戳换算写法取决于 Step 2 的实测结果，不在此预先写死——猜错了
> 整个 PIT 契约就差一分钟。骨架如下，标注 `<< 探针确定 >>` 处按实测填写。

```python
"""Windows 侧：按清单下载分钟线并导出 parquet。

可中断可续传：已导出的股-日记入 done.csv，重跑跳过。
导出的 parquet 直接符合 schema.MINUTE_BARS，Linux 侧 import_minute_bars
不做任何格式转换。

**dt 一律为该分钟的收盘时刻**（09:30–09:31 这根记 09:31）。
xtdata 若给的是开盘时刻，在此 +1 分钟 —— << 探针确定 >>
"""
```

要点（实现时逐条落实）：
- 读 manifest.csv，按 `symbol` 分组以复用连接
- 每组调 `download_history_data` 后 `get_market_data`
- 转成 MINUTE_BARS 的列与 dtype，`dt` 归一到收盘时刻
- 每 N 个股-日写一个 parquet 到 `outgoing/`，文件名含日期便于排查
- 已完成的股-日追加进 `done.csv`，重跑时跳过
- 单只失败不中断整轮，失败的记进 `failed.csv` 下次重试

- [ ] **Step 4: 小批试跑**

先跑 100 组，确认耗时与产出格式，再放全量。

- [ ] **Step 5: 同步到 Linux 并导入**

传输方式不进代码。先用最简单的：

```bash
# Windows -> Linux
rsync -az -e "ssh -i ~/.ssh/qmt_bridge_ed25519" \
      outgoing/ ubuntu@<linux>:/home/ubuntu/qsys/var/minutes-incoming/
# Linux 侧导入
uv run python -c "
from pathlib import Path
from qsys.data.pipeline.minutes import import_minute_bars
from qsys.data.storage import Warehouse
print(import_minute_bars(Warehouse(Path('warehouse')),
                         Path('var/minutes-incoming'),
                         archive_dir=Path('var/minutes-done')))
"
```

- [ ] **Step 6: 跨源校验**

对已导入的日子逐日跑 `check_minute_bars`。**成交量、最高价、首根 open
三项都对得上，才算分钟线可用。**

- [ ] **Step 7: 提交**

```bash
git add scripts/win_probe_minutes.py scripts/win_download_minutes.py \
        docs/deploy/windows-node.md
git commit -m "feat(data): Windows 分钟线下载脚本"
```

---

## 完成标准

1. `uv run pytest` 全绿，2-1a 的 719 项无回归
2. **PIT 不可穿越** —— 反向测试证明早盘读不到尾盘；bar 在其收盘时刻整可见，前一秒不可见
3. **导入幂等** —— 同一批文件重复导入结果一致
4. **单个坏文件不中断整批** —— 跨机传输出半个文件是常态
5. **跨源交叉验证通过** —— 分钟成交量之和、最高价、首根 open 三项与日线一致
6. **候选选择用交易日推进** —— 首板在周五，候选日是下周一
7. xtquant 不被任何库代码 import（Linux 上装不了，import 即挂）

## 已知缺口（转入后续）

- **历史分钟线深度未知** —— QMT 能回溯多久要探针实测；若不足 2023-01，早期区间只能用日线近似版
- **历史估值** —— 2-1a 遗留。东财接口在开发机与服务器上均不可用，需另寻源
- **ST 历史** —— 2-1a 遗留，涨跌停一律按板块判
- **指数因子** —— `index_ma20_slope_5d` 需沪深 300 指数日线，仍未覆盖

## 下一步

**策略移植**：日线近似版先跑通并与聚宽对账（比对「哪天买了哪些票」），
分钟线到位后再上精确版。之后是 **Web 回测平台**。
