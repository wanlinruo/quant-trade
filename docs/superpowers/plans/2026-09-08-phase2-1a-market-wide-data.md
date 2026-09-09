# 二期 2-1a：全市场日线与派生数据 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把数据层从「3 只票 / 半年」扩到「全市场 / 多年」，并补上真实策略所需的派生数据——涨跌停判定、市场生态因子、估值快照。产出：足以支撑首板高开/低开策略**日线近似版**回测的数据底座。

**Architecture:** mootdx 的 `start` 参数支持分页（已实测可回溯至 2018 年），据此修掉计划 1-1 遗留的 800 根上限。全市场采集必须**分批落盘**——Linux 只有 1.9 GB 内存，1000 万行日线全放内存会 OOM。派生数据（涨跌停、生态因子）从日线算出后独立成表，避免每次回测重算全市场。

**Tech Stack:** Python 3.11+ · uv · polars · pyarrow · duckdb · mootdx · akshare · structlog · pytest

**Spec:** `docs/superpowers/specs/2026-09-06-personal-quant-system-design.md`（§2.1 数据粒度修订、§4 数据层、§12 风险表）

## 为什么是这些数据

移植目标策略（`Structural Strategy V1.2` 首板高开/低开）的每一项需求：

| 策略中的用法 | 需要的数据 | 本计划对应任务 |
|---|---|---|
| `prepare_stock_list` 扫全市场找昨日首板 | 全市场日线 + 涨停判定 | Task 1、2、3 |
| `get_hl_stock` / `get_ever_hl_stock2` | `close == high_limit`、`high == high_limit` | Task 3 |
| `check_low_open` 的 `auction['current']/close` | 日线 `open`（竞价成交价即开盘价，已实测相等） | Task 2 |
| `check_high_open` 的市值/流通市值过滤 | 估值 | Task 4 |
| `check_left_pressure` 前高与放量 | 日线 high/volume | Task 2 |
| S7 Gate 的 `_s6_classify` | 市场生态因子 | Task 5 |
| Web 上看「有哪些数据」 | 覆盖查询 | Task 6 |

**本计划不覆盖**：集合竞价成交量、11:25/14:25/14:50 日内价格。二者需分钟线，归入计划 2-1b（QMT 下载 + 同步）。

## Global Constraints

- Python **3.11+**
- **内存是硬约束**：Linux 可用约 870 MB。全市场采集**必须分批落盘**，任何一次性把全量装进 DataFrame 的写法都不可接受
- 采集必须**可中断可续传**：5400 只标的的采集会跑很久，进程被杀后重启要能接着跑
- 沿用计划 1-1 的既有纪律：`bars_daily` 只追加永不重写、复权因子独立可重算、写入幂等、质量校验不过不进回测
- 派生数据（涨跌停、生态因子）**可从日线完全重算**，不是独立事实——重算必须得到相同结果
- 单元测试不得触网，一律用构造数据；真实采集由脚本手动执行
- 环境隔离：所有命令前置
  `unset VIRTUAL_ENV; export PATH="$HOME/.local/bin:$(echo "$PATH" | tr ':' '\n' | grep -v 'project-002/.venv' | paste -sd: -)"`

---

## File Structure

```
src/qsys/data/
  schema.py                   ← 追加 DAILY_FLAGS / MARKET_REGIME / VALUATION_SNAPSHOT
  providers/mootdx_provider.py ← 分页
  providers/akshare_provider.py ← 追加全市场快照
  pipeline/universe.py        全市场批量采集（分批落盘 + 断点续传）
  pipeline/flags.py           涨跌停与首板判定
  pipeline/regime.py          市场生态因子
  pipeline/valuation.py       估值快照
  coverage.py                 数据覆盖查询
scripts/
  ingest_universe.py          全市场采集入口
tests/data/
  test_mootdx_pagination.py test_universe.py test_flags.py
  test_regime.py test_valuation.py test_coverage.py
tests/
  test_market_data_e2e.py
```

---

## Task 1: mootdx 日线分页

计划 1-1 的 `_MAX_OFFSET = 800` 把历史截断在约 3.2 年。实测 `bars(symbol, frequency, start, offset)` 的 `start` 参数生效、分页间无重叠、可回溯至 2018 年。

**Files:**
- Modify: `src/qsys/data/providers/mootdx_provider.py`
- Test: `tests/data/test_mootdx_pagination.py`

**Interfaces:**
- Consumes: `BarsProvider`（1-1）
- Produces: `MootdxProvider(client=None, *, page_size=800, max_pages=20)`；`fetch_daily_bars` 自动翻页直到覆盖 `start` 或数据耗尽；`truncated` 仅在**翻满 max_pages 仍未覆盖**时记录

- [ ] **Step 1: 写失败测试**

Create `tests/data/test_mootdx_pagination.py`:

```python
from datetime import date

import pandas as pd
import pytest

from qsys.data.providers.mootdx_provider import MootdxProvider


class PagedClient:
    """模拟 mootdx：start 为「距最新第几根」，返回该页。"""

    def __init__(self, total_days: int = 2000, page_size: int = 800):
        self.total = total_days
        self.page_size = page_size
        self.calls: list[dict] = []
        base = pd.Timestamp("2026-09-08")
        # 最新在前：index 0 是最新一天
        self._days = [base - pd.Timedelta(days=i) for i in range(total_days)]

    def bars(self, symbol, frequency, start=0, offset=800):
        self.calls.append({"symbol": symbol, "start": start, "offset": offset})
        window = self._days[start : start + offset]
        if not window:
            return pd.DataFrame()
        window = list(reversed(window))  # mootdx 返回按时间升序
        n = len(window)
        return pd.DataFrame({
            "datetime": [d.strftime("%Y-%m-%d %H:%M") for d in window],
            "open": [10.0] * n, "high": [10.5] * n, "low": [9.5] * n,
            "close": [10.2] * n, "vol": [1000.0] * n, "amount": [10200.0] * n,
        })


def test_single_page_when_range_is_short():
    client = PagedClient()
    p = MootdxProvider(client=client, page_size=800)
    p.fetch_daily_bars("600036.SH", date(2026, 9, 1), date(2026, 9, 8))
    assert len(client.calls) == 1


def test_pages_until_the_start_date_is_covered():
    client = PagedClient(total_days=2000)
    p = MootdxProvider(client=client, page_size=800)
    p.fetch_daily_bars("600036.SH", date(2023, 1, 1), date(2026, 9, 8))
    assert len(client.calls) >= 2
    assert [c["start"] for c in client.calls[:2]] == [0, 800]


def test_pagination_produces_no_duplicate_dates():
    client = PagedClient(total_days=2000)
    p = MootdxProvider(client=client, page_size=800)
    df = p.fetch_daily_bars("600036.SH", date(2023, 1, 1), date(2026, 9, 8))
    assert df["dt"].n_unique() == df.height


def test_result_is_sorted_ascending():
    client = PagedClient(total_days=2000)
    df = MootdxProvider(client=client).fetch_daily_bars(
        "600036.SH", date(2023, 1, 1), date(2026, 9, 8))
    assert df["dt"].to_list() == sorted(df["dt"].to_list())


def test_result_is_clipped_to_the_requested_range():
    client = PagedClient(total_days=2000)
    df = MootdxProvider(client=client).fetch_daily_bars(
        "600036.SH", date(2025, 1, 1), date(2025, 6, 30))
    assert df["dt"].min() >= date(2025, 1, 1)
    assert df["dt"].max() <= date(2025, 6, 30)


def test_stops_when_data_runs_out():
    """标的上市不足请求区间时，翻到空页即停，不空转。"""
    client = PagedClient(total_days=300)
    p = MootdxProvider(client=client, page_size=800, max_pages=20)
    p.fetch_daily_bars("600036.SH", date(2015, 1, 1), date(2026, 9, 8))
    assert len(client.calls) <= 2


def test_max_pages_caps_the_work_and_records_truncation():
    client = PagedClient(total_days=100_000)
    p = MootdxProvider(client=client, page_size=800, max_pages=2)
    p.fetch_daily_bars("600036.SH", date(1995, 1, 1), date(2026, 9, 8))
    assert len(client.calls) == 2
    assert p.truncated, "翻满上限仍未覆盖时必须记录截断"


def test_no_truncation_recorded_when_range_is_covered():
    client = PagedClient(total_days=2000)
    p = MootdxProvider(client=client, page_size=800)
    p.fetch_daily_bars("600036.SH", date(2023, 1, 1), date(2026, 9, 8))
    assert p.truncated == []


def test_empty_first_page_returns_empty_frame():
    class Empty:
        def bars(self, **kw):
            return pd.DataFrame()

    df = MootdxProvider(client=Empty()).fetch_daily_bars(
        "600036.SH", date(2024, 1, 1), date(2024, 6, 30))
    assert df.height == 0
    assert df.columns == ["dt", "open", "high", "low", "close", "volume", "amount"]


def test_page_size_is_configurable():
    client = PagedClient(total_days=1000)
    MootdxProvider(client=client, page_size=200).fetch_daily_bars(
        "600036.SH", date(2023, 1, 1), date(2026, 9, 8))
    assert all(c["offset"] == 200 for c in client.calls)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/test_mootdx_pagination.py -v`
Expected: FAIL —— 现有实现只调一次，`test_pages_until_the_start_date_is_covered` 断言失败

- [ ] **Step 3: 修改 `src/qsys/data/providers/mootdx_provider.py`**

把 `_MAX_OFFSET = 800` 常量改为构造参数，`fetch_daily_bars` 改为翻页：

```python
    def __init__(self, client=None, *, page_size: int = 800, max_pages: int = 20) -> None:
        if client is None:
            from mootdx.quotes import Quotes

            client = Quotes.factory(market="std")
        self._client = client
        self._page_size = page_size
        self._max_pages = max_pages
        # 翻满 max_pages 仍未覆盖请求起点时记录：(symbol, 请求起点, 实际最早)
        self.truncated: list[tuple[str, date, date]] = []

    def fetch_daily_bars(self, symbol: str, start: date, end: date) -> pl.DataFrame:
        """按需翻页直到覆盖 start。

        mootdx 的 start 参数是「距最新第几根」，分页间无重叠（已实测）。
        计划 1-1 的单次 800 根上限把历史截断在约 3.2 年，这里修掉。
        """
        code = symbol.split(".")[0]
        pages: list[pl.DataFrame] = []
        earliest: date | None = None

        for page in range(self._max_pages):
            raw = self._client.bars(
                symbol=code, frequency=_DAILY_FREQUENCY,
                start=page * self._page_size, offset=self._page_size,
            )
            if raw is None or len(raw) == 0:
                break  # 数据耗尽（标的上市不足请求区间）

            parsed = self._parse(raw)
            if parsed.height == 0:
                break
            pages.append(parsed)
            earliest = parsed["dt"].min()
            if earliest <= start:
                break
        else:
            if earliest is not None and earliest > start:
                self.truncated.append((symbol, start, earliest))
                log.warning(
                    "mootdx.history_truncated", symbol=symbol,
                    requested_start=str(start), earliest_available=str(earliest),
                    pages=self._max_pages,
                )

        if not pages:
            return _empty()

        return (
            pl.concat(pages, how="vertical")
            .unique(subset=["dt"], keep="first", maintain_order=True)
            .sort("dt")
            .filter(pl.col("dt").is_between(start, end))
            .select(COLUMNS)
        )

    def _parse(self, raw) -> pl.DataFrame:
        df = pl.from_pandas(raw.reset_index(drop=True))
        return df.select(
            pl.col("datetime").cast(pl.Utf8).str.slice(0, 10).str.to_date().alias("dt"),
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("vol").cast(pl.Int64).alias("volume"),
            pl.col("amount").cast(pl.Float64),
        ).sort("dt")
```

顶部 import 补上 `from datetime import date`。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/data/test_mootdx_pagination.py tests/data/providers/ -v`
Expected: PASS —— 10 passed，且计划 1-1 的既有 provider 测试无回归

若既有的 `test_truncated_history_is_reported` 因语义变化而失败（现在会翻页而非直接截断），把它改为断言「翻满 max_pages 才记录截断」——那是本任务有意的行为变更。

- [ ] **Step 5: 真实数据验证**

Run:
```bash
uv run python -c "
from datetime import date
from qsys.data.providers.mootdx_provider import MootdxProvider
p = MootdxProvider()
df = p.fetch_daily_bars('600036.SH', date(2023,1,1), date(2026,9,8))
print(f'行数 {df.height}  区间 {df[\"dt\"].min()} ~ {df[\"dt\"].max()}')
print('截断:', p.truncated)
"
```
Expected: 约 900 行，起点接近 2023-01，`截断: []`

- [ ] **Step 6: 提交**

```bash
git add src/qsys/data/providers/mootdx_provider.py tests/data/test_mootdx_pagination.py
git commit -m "feat(data): mootdx 日线分页（修掉 3.2 年回溯上限）"
```

---

## Task 2: 全市场批量采集

5400 只标的 × 多年 ≈ 千万行。**Linux 可用内存约 870 MB，全量装进内存必 OOM。**必须分批落盘，且可中断可续传。

**Files:**
- Create: `src/qsys/data/pipeline/universe.py`
- Test: `tests/data/test_universe.py`

**Interfaces:**
- Consumes: `BarsProvider`（Task 1）、`Warehouse` / `IngestLog`（1-1）
- Produces: `IngestProgress(total, done, failed, current)`；`ingest_universe(provider, warehouse, symbols, start, end, *, batch_size=200, on_progress=None, skip_done=True) -> IngestProgress`；`completed_symbols(warehouse, start, end) -> set[str]`

- [ ] **Step 1: 写失败测试**

Create `tests/data/test_universe.py`:

```python
from datetime import date

import polars as pl
import pytest

from qsys.data.pipeline.universe import completed_symbols, ingest_universe
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

BAR_SCHEMA = {
    "dt": pl.Date, "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
    "close": pl.Float64, "volume": pl.Int64, "amount": pl.Float64,
}
SESSIONS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]


class CountingProvider:
    name = "counting"

    def __init__(self, failing: set[str] | None = None, empty: set[str] | None = None):
        self.failing = failing or set()
        self.empty = empty or set()
        self.calls: list[str] = []

    def fetch_daily_bars(self, symbol, start, end):
        self.calls.append(symbol)
        if symbol in self.failing:
            raise RuntimeError("source unavailable")
        if symbol in self.empty:
            return pl.DataFrame(schema=BAR_SCHEMA)
        n = len(SESSIONS)
        return pl.DataFrame({
            "dt": SESSIONS, "open": [10.0] * n, "high": [10.5] * n,
            "low": [9.5] * n, "close": [10.2] * n, "volume": [1000] * n,
            "amount": [10200.0] * n,
        }, schema=BAR_SCHEMA)


def symbols(n: int) -> list[str]:
    return [f"{600000 + i}.SH" for i in range(n)]


def test_ingests_every_symbol(warehouse):
    p = CountingProvider()
    progress = ingest_universe(p, warehouse, symbols(5), SESSIONS[0], SESSIONS[-1])
    assert progress.done == 5
    assert progress.failed == 0
    df = WarehouseQuery(warehouse).daily_bars(None, SESSIONS[0], SESSIONS[-1])
    assert df["symbol"].n_unique() == 5


def test_writes_in_batches_rather_than_all_at_once(warehouse):
    """分批落盘是内存约束下的硬要求，不是优化。"""
    written = []

    class SpyWarehouse(Warehouse):
        def write(self, dataset, table, *, year=None):
            written.append(dataset)
            return super().write(dataset, table, year=year)

    spy = SpyWarehouse(warehouse.root)
    ingest_universe(CountingProvider(), spy, symbols(10), SESSIONS[0], SESSIONS[-1],
                    batch_size=3)
    assert written.count("bars_daily") >= 4, "10 只 / 每批 3 只应至少写 4 次"


def test_failures_do_not_abort_the_run(warehouse):
    p = CountingProvider(failing={"600002.SH"})
    progress = ingest_universe(p, warehouse, symbols(5), SESSIONS[0], SESSIONS[-1])
    assert progress.done == 4
    assert progress.failed == 1
    assert len(p.calls) == 5


def test_empty_results_count_as_done_not_failed(warehouse):
    """停牌整段或未上市返回空，不是失败。"""
    p = CountingProvider(empty={"600001.SH"})
    progress = ingest_universe(p, warehouse, symbols(3), SESSIONS[0], SESSIONS[-1])
    assert progress.failed == 0


def test_progress_callback_reports_each_batch(warehouse):
    seen = []
    ingest_universe(CountingProvider(), warehouse, symbols(10), SESSIONS[0],
                    SESSIONS[-1], batch_size=4, on_progress=seen.append)
    assert len(seen) >= 3
    assert seen[-1].done + seen[-1].failed == 10
    assert seen[-1].total == 10


def test_resume_skips_already_completed_symbols(warehouse):
    """进程被杀后重跑要能接着跑，不是从头再来。"""
    first = CountingProvider()
    ingest_universe(first, warehouse, symbols(5), SESSIONS[0], SESSIONS[-1])

    second = CountingProvider()
    ingest_universe(second, warehouse, symbols(8), SESSIONS[0], SESSIONS[-1])
    assert set(second.calls) == set(symbols(8)) - set(symbols(5))


def test_skip_done_false_forces_a_full_refetch(warehouse):
    first = CountingProvider()
    ingest_universe(first, warehouse, symbols(3), SESSIONS[0], SESSIONS[-1])
    second = CountingProvider()
    ingest_universe(second, warehouse, symbols(3), SESSIONS[0], SESSIONS[-1],
                    skip_done=False)
    assert len(second.calls) == 3


def test_completed_symbols_reflects_what_was_ingested(warehouse):
    ingest_universe(CountingProvider(), warehouse, symbols(4), SESSIONS[0], SESSIONS[-1])
    assert completed_symbols(warehouse, SESSIONS[0], SESSIONS[-1]) == set(symbols(4))


def test_failed_symbols_are_retried_on_the_next_run(warehouse):
    first = CountingProvider(failing={"600001.SH"})
    ingest_universe(first, warehouse, symbols(3), SESSIONS[0], SESSIONS[-1])
    second = CountingProvider()
    ingest_universe(second, warehouse, symbols(3), SESSIONS[0], SESSIONS[-1])
    assert "600001.SH" in second.calls


def test_empty_symbol_list_is_a_no_op(warehouse):
    progress = ingest_universe(CountingProvider(), warehouse, [], SESSIONS[0], SESSIONS[-1])
    assert progress.total == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/test_universe.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.pipeline.universe'`

- [ ] **Step 3: 实现 `src/qsys/data/pipeline/universe.py`**

```python
"""全市场批量采集。

内存是硬约束：Linux 核心节点可用约 870 MB，5400 只 × 多年 ≈ 千万行日线，
全量装进 DataFrame 必 OOM。因此**分批落盘**不是优化，是可行性前提。

采集会跑很久，进程可能被杀。已完成的标的记入 ingest_log，重跑时跳过——
失败的标的不记入，下次自动重试。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

import polars as pl
import structlog

from qsys.data.pipeline.bars import BAR_COLUMNS, ingest_daily_bars
from qsys.data.pipeline.ingest_log import IngestLog
from qsys.data.providers.base import BarsProvider
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

_DATASET = "bars_daily"


@dataclass(frozen=True, slots=True)
class IngestProgress:
    total: int
    done: int
    failed: int
    current: str = ""

    @property
    def remaining(self) -> int:
        return self.total - self.done - self.failed


def _log_key(start: date, end: date) -> str:
    return f"{_DATASET}:{start.isoformat()}:{end.isoformat()}"


def completed_symbols(warehouse: Warehouse, start: date, end: date) -> set[str]:
    """已成功采集过该区间的标的。"""
    key = _log_key(start, end)
    ingest_log = IngestLog(warehouse)
    done: set[str] = set()
    for year in range(start.year, end.year + 1):
        entries = ingest_log.entries(year)
        if entries.height == 0:
            continue
        hit = entries.filter((pl.col("dataset") == key) & pl.col("ok"))
        done.update(hit["provider"].to_list())
    return done


def ingest_universe(
    provider: BarsProvider,
    warehouse: Warehouse,
    symbols: Sequence[str],
    start: date,
    end: date,
    *,
    batch_size: int = 200,
    on_progress: Callable[[IngestProgress], None] | None = None,
    skip_done: bool = True,
) -> IngestProgress:
    """分批采集全市场日线。返回最终进度。"""
    targets = list(symbols)
    if skip_done:
        already = completed_symbols(warehouse, start, end)
        targets = [s for s in targets if s not in already]

    total = len(symbols)
    done = len(symbols) - len(targets)
    failed = 0
    ingest_log = IngestLog(warehouse)
    key = _log_key(start, end)

    for offset in range(0, len(targets), batch_size):
        batch = targets[offset : offset + batch_size]
        collected: list[pl.DataFrame] = []
        succeeded: list[str] = []

        for symbol in batch:
            try:
                frame = provider.fetch_daily_bars(symbol, start, end)
            except Exception as exc:  # 单只失败不中断整轮
                failed += 1
                log.warning("universe.fetch_failed", symbol=symbol, error=str(exc))
                continue
            succeeded.append(symbol)
            if frame.height:
                collected.append(
                    frame.with_columns(pl.lit(symbol).alias("symbol")).select(BAR_COLUMNS)
                )

        if collected:
            _write_batch(warehouse, pl.concat(collected, how="vertical"))
        # 空结果（停牌整段 / 未上市）算完成，不算失败
        for symbol in succeeded:
            ingest_log.record(key, end, symbol, 0, True)
        done += len(succeeded)

        progress = IngestProgress(total, done, failed, batch[-1] if batch else "")
        log.info("universe.progress", total=total, done=done, failed=failed)
        if on_progress is not None:
            on_progress(progress)

        del collected  # 尽早释放，内存紧张

    return IngestProgress(total, done, failed)


def _write_batch(warehouse: Warehouse, fresh: pl.DataFrame) -> None:
    """按年合并写入。每批单独走一次，避免全量驻留内存。"""
    from qsys.data import schema
    from qsys.data.query import WarehouseQuery

    query = WarehouseQuery(warehouse)
    for year in sorted({d.year for d in fresh["dt"].to_list()}):
        lo, hi = date(year, 1, 1), date(year, 12, 31)
        incoming = fresh.filter(pl.col("dt").is_between(lo, hi))
        merged = (
            pl.concat([query.daily_bars(None, lo, hi), incoming], how="vertical")
            .unique(subset=["symbol", "dt"], keep="last", maintain_order=True)
            .sort(["symbol", "dt"])
            .select(BAR_COLUMNS)
        )
        warehouse.write(_DATASET, merged.to_arrow().cast(schema.BARS_DAILY), year=year)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/data/test_universe.py -v`
Expected: PASS —— 10 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/pipeline/universe.py tests/data/test_universe.py
git commit -m "feat(data): 全市场批量采集（分批落盘 + 断点续传）"
```

---

## Task 3: 涨跌停与首板判定

策略的候选池完全建立在这上面：`get_hl_stock` 找 `close == high_limit`，`get_ever_hl_stock2` 找 `high == high_limit and close != high_limit`（炸板），首板 = 昨日涨停且前日未涨停。

**Files:**
- Modify: `src/qsys/data/schema.py`（追加 `DAILY_FLAGS`）
- Create: `src/qsys/data/pipeline/flags.py`
- Test: `tests/data/test_flags.py`

**Interfaces:**
- Consumes: `bars_daily` / `instruments`（1-1）、`PRICE_LIMITS`（1-2a `markets/ashare`）
- Produces: `DAILY_FLAGS` schema（dt, symbol, prev_close, limit_up, limit_down, is_limit_up, is_limit_down, touched_limit_up, is_broken_board, is_first_board）；`limit_prices(prev_close, board) -> tuple[float, float]`；`build_daily_flags(warehouse, start, end) -> int`

- [ ] **Step 1: 写失败测试**

Create `tests/data/test_flags.py`:

```python
from datetime import date

import pyarrow as pa
import pytest

from qsys.data import schema
from qsys.data.pipeline.flags import build_daily_flags, limit_prices
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

D = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]


def test_main_board_limits_are_ten_percent():
    up, down = limit_prices(10.00, "main")
    assert (up, down) == (11.00, 9.00)


def test_gem_limits_are_twenty_percent():
    assert limit_prices(10.00, "gem") == (12.00, 8.00)


def test_limits_are_rounded_to_cents():
    up, down = limit_prices(9.99, "main")
    assert (up, down) == (10.99, 8.99)


def test_unknown_board_falls_back_to_main():
    assert limit_prices(10.00, "unknown") == (11.00, 9.00)


def seed(warehouse: Warehouse, rows) -> None:
    """rows: [(symbol, dt, open, high, low, close)]"""
    warehouse.write("bars_daily", pa.table({
        "symbol": [r[0] for r in rows], "dt": [r[1] for r in rows],
        "open": [r[2] for r in rows], "high": [r[3] for r in rows],
        "low": [r[4] for r in rows], "close": [r[5] for r in rows],
        "volume": [1000] * len(rows), "amount": [0.0] * len(rows),
    }, schema=schema.BARS_DAILY), year=2024)
    warehouse.write("instruments", pa.table({
        "symbol": sorted({r[0] for r in rows}),
        "name": ["x"] * len({r[0] for r in rows}),
        "market": ["SH"] * len({r[0] for r in rows}),
        "list_date": [None] * len({r[0] for r in rows}),
        "delist_date": [None] * len({r[0] for r in rows}),
        "board": ["main"] * len({r[0] for r in rows}),
    }, schema=schema.INSTRUMENTS))


def flags_of(warehouse, symbol, dt):
    df = WarehouseQuery(warehouse).daily_flags(start=dt, end=dt)
    return df.filter(df["symbol"] == symbol).row(0, named=True)


def test_limit_up_is_detected(warehouse):
    seed(warehouse, [
        ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
        ("600036.SH", D[1], 11.0, 11.0, 11.0, 11.0),
    ])
    build_daily_flags(warehouse, D[0], D[1])
    row = flags_of(warehouse, "600036.SH", D[1])
    assert row["is_limit_up"] is True
    assert row["limit_up"] == 11.00


def test_broken_board_is_touched_but_not_closed_at_limit(warehouse):
    """炸板：盘中触及涨停但收盘没封住。"""
    seed(warehouse, [
        ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
        ("600036.SH", D[1], 10.5, 11.0, 10.2, 10.6),
    ])
    build_daily_flags(warehouse, D[0], D[1])
    row = flags_of(warehouse, "600036.SH", D[1])
    assert row["touched_limit_up"] is True
    assert row["is_limit_up"] is False
    assert row["is_broken_board"] is True


def test_limit_down_is_detected(warehouse):
    seed(warehouse, [
        ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
        ("600036.SH", D[1], 9.0, 9.0, 9.0, 9.0),
    ])
    build_daily_flags(warehouse, D[0], D[1])
    assert flags_of(warehouse, "600036.SH", D[1])["is_limit_down"] is True


def test_first_board_requires_no_limit_up_the_day_before(warehouse):
    """首板：今日涨停且昨日未涨停。连板不是首板。"""
    seed(warehouse, [
        ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
        ("600036.SH", D[1], 11.0, 11.0, 11.0, 11.0),
        ("600036.SH", D[2], 12.1, 12.1, 12.1, 12.1),
    ])
    build_daily_flags(warehouse, D[0], D[2])
    assert flags_of(warehouse, "600036.SH", D[1])["is_first_board"] is True
    assert flags_of(warehouse, "600036.SH", D[2])["is_first_board"] is False


def test_first_bar_has_no_prev_close_and_no_flags(warehouse):
    seed(warehouse, [("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0)])
    build_daily_flags(warehouse, D[0], D[0])
    row = flags_of(warehouse, "600036.SH", D[0])
    assert row["prev_close"] is None
    assert row["is_limit_up"] is False


def test_flags_are_computed_per_symbol(warehouse):
    seed(warehouse, [
        ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
        ("600036.SH", D[1], 11.0, 11.0, 11.0, 11.0),
        ("600519.SH", D[0], 10.0, 10.0, 10.0, 10.0),
        ("600519.SH", D[1], 10.1, 10.2, 10.0, 10.1),
    ])
    build_daily_flags(warehouse, D[0], D[1])
    assert flags_of(warehouse, "600036.SH", D[1])["is_limit_up"] is True
    assert flags_of(warehouse, "600519.SH", D[1])["is_limit_up"] is False


def test_rebuild_is_idempotent(warehouse):
    seed(warehouse, [
        ("600036.SH", D[0], 10.0, 10.0, 10.0, 10.0),
        ("600036.SH", D[1], 11.0, 11.0, 11.0, 11.0),
    ])
    assert build_daily_flags(warehouse, D[0], D[1]) == 2
    assert build_daily_flags(warehouse, D[0], D[1]) == 2
    assert WarehouseQuery(warehouse).daily_flags().height == 2


def test_build_on_empty_warehouse_returns_zero(warehouse):
    assert build_daily_flags(warehouse, D[0], D[1]) == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/test_flags.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.pipeline.flags'`

- [ ] **Step 3: 追加 schema 与查询方法**

在 `src/qsys/data/schema.py` 追加并注册进 `PARTITIONED`：

```python
DAILY_FLAGS = pa.schema(
    [
        ("dt", pa.date32()),
        ("symbol", pa.string()),
        ("prev_close", pa.float64()),
        ("limit_up", pa.float64()),
        ("limit_down", pa.float64()),
        ("is_limit_up", pa.bool_()),
        ("is_limit_down", pa.bool_()),
        ("touched_limit_up", pa.bool_()),
        ("is_broken_board", pa.bool_()),
        ("is_first_board", pa.bool_()),
    ]
)
```

在 `src/qsys/data/query.py` 的 `WarehouseQuery` 追加：

```python
    def daily_flags(
        self, start: date | None = None, end: date | None = None
    ) -> pl.DataFrame:
        glob = self._glob("daily_flags", partitioned=True)
        if glob is None:
            return _empty(schema.DAILY_FLAGS)
        sql = f"SELECT * FROM read_parquet('{glob}')"
        params: list[object] = []
        if start is not None and end is not None:
            sql += " WHERE dt BETWEEN ? AND ?"
            params = [start, end]
        return self._con.execute(sql + " ORDER BY dt, symbol", params).pl()
```

- [ ] **Step 4: 实现 `src/qsys/data/pipeline/flags.py`**

```python
"""涨跌停与首板判定。

策略的候选池完全建立在这上面：
  涨停   close >= limit_up
  炸板   high >= limit_up 但 close < limit_up
  首板   今日涨停且昨日未涨停（连板不是首板）

派生自 bars_daily + instruments.board，可完全重算——重算必须得到相同结果。

**已知缺口**：ST 股涨跌幅为 5%，但一期无 ST 历史数据，此处一律按板块判定。
ST 股的涨停会被漏判。补 ST 历史后需重算本表。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import structlog

from qsys.core.trading import to_cents
from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.markets.ashare import PRICE_LIMITS

log = structlog.get_logger(__name__)

_TOLERANCE = 1e-6

_FLAGS_SCHEMA = {
    "dt": pl.Date, "symbol": pl.Utf8, "prev_close": pl.Float64,
    "limit_up": pl.Float64, "limit_down": pl.Float64,
    "is_limit_up": pl.Boolean, "is_limit_down": pl.Boolean,
    "touched_limit_up": pl.Boolean, "is_broken_board": pl.Boolean,
    "is_first_board": pl.Boolean,
}


def limit_prices(prev_close: float, board: str) -> tuple[float, float]:
    """返回 (涨停价, 跌停价)，四舍五入到分。"""
    limit = PRICE_LIMITS.get(board, 0.10)
    return to_cents(prev_close * (1.0 + limit)), to_cents(prev_close * (1.0 - limit))


def build_daily_flags(warehouse: Warehouse, start: date, end: date) -> int:
    """从日线派生涨跌停与首板标记。返回写入行数。"""
    query = WarehouseQuery(warehouse)
    bars = query.daily_bars(None, start, end)
    if bars.height == 0:
        return 0

    instruments = query.instruments()
    boards = dict(
        zip(instruments["symbol"].to_list(), instruments["board"].to_list())
    ) if instruments.height else {}

    frame = bars.sort(["symbol", "dt"]).with_columns(
        pl.col("close").shift(1).over("symbol").alias("prev_close")
    )

    rows: list[dict] = []
    for row in frame.iter_rows(named=True):
        prev = row["prev_close"]
        if prev is None or prev <= 0:
            rows.append({
                "dt": row["dt"], "symbol": row["symbol"], "prev_close": None,
                "limit_up": None, "limit_down": None, "is_limit_up": False,
                "is_limit_down": False, "touched_limit_up": False,
                "is_broken_board": False, "is_first_board": False,
            })
            continue

        up, down = limit_prices(prev, boards.get(row["symbol"], "main"))
        is_up = row["close"] >= up - _TOLERANCE
        touched = row["high"] >= up - _TOLERANCE
        rows.append({
            "dt": row["dt"], "symbol": row["symbol"], "prev_close": prev,
            "limit_up": up, "limit_down": down,
            "is_limit_up": is_up,
            "is_limit_down": row["close"] <= down + _TOLERANCE,
            "touched_limit_up": touched,
            "is_broken_board": touched and not is_up,
            "is_first_board": False,  # 下一步按 symbol 回填
        })

    flags = pl.DataFrame(rows, schema=_FLAGS_SCHEMA).sort(["symbol", "dt"])
    # 首板 = 今日涨停且昨日未涨停
    flags = flags.with_columns(
        (
            pl.col("is_limit_up")
            & ~pl.col("is_limit_up").shift(1, fill_value=False).over("symbol")
        ).alias("is_first_board")
    )

    written = 0
    for year in sorted({d.year for d in flags["dt"].to_list()}):
        chunk = flags.filter(pl.col("dt").dt.year() == year).sort(["dt", "symbol"])
        warehouse.write(
            "daily_flags", chunk.to_arrow().cast(schema.DAILY_FLAGS), year=year
        )
        written += chunk.height

    log.info("daily_flags.built", rows=written, start=str(start), end=str(end))
    return written
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/data/test_flags.py -v`
Expected: PASS —— 12 passed

- [ ] **Step 6: 提交**

```bash
git add src/qsys/data/schema.py src/qsys/data/query.py \
        src/qsys/data/pipeline/flags.py tests/data/test_flags.py
git commit -m "feat(data): 涨跌停与首板判定"
```

---

## Task 4: 市场生态因子

策略的 S7 Gate 完全依赖这些：`index_ma20_slope_5d`、`prior_limitup_nextday_return_mean`、`market_median_return_1d`、`touched_limit_up_count`、`market_advance_ratio`。

**Files:**
- Modify: `src/qsys/data/schema.py`（追加 `MARKET_REGIME`）
- Create: `src/qsys/data/pipeline/regime.py`
- Test: `tests/data/test_regime.py`

**Interfaces:**
- Consumes: `bars_daily` / `daily_flags`（Task 3）
- Produces: `MARKET_REGIME` schema；`build_market_regime(warehouse, start, end) -> int`；`WarehouseQuery.market_regime(start, end)`

字段（对应策略 `_s5_market_raw_factors` 与 `_s5_index_raw_factors`）：
`dt, limit_up_count, limit_down_count, limit_up_down_ratio, touched_limit_up_count,
broken_board_count, broken_board_rate, first_board_count, market_advance_ratio,
market_median_return_1d, market_total_money, prior_limitup_nextday_return_mean,
prior_limitup_nextday_return_median, valid_stock_count`

- [ ] **Step 1: 写失败测试**

Create `tests/data/test_regime.py`:

```python
from datetime import date

import pyarrow as pa
import pytest

from qsys.data import schema
from qsys.data.pipeline.flags import build_daily_flags
from qsys.data.pipeline.regime import build_market_regime
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

D = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]


def seed(warehouse: Warehouse, rows) -> None:
    """rows: [(symbol, dt, open, high, low, close, amount)]"""
    syms = sorted({r[0] for r in rows})
    warehouse.write("bars_daily", pa.table({
        "symbol": [r[0] for r in rows], "dt": [r[1] for r in rows],
        "open": [r[2] for r in rows], "high": [r[3] for r in rows],
        "low": [r[4] for r in rows], "close": [r[5] for r in rows],
        "volume": [1000] * len(rows), "amount": [r[6] for r in rows],
    }, schema=schema.BARS_DAILY), year=2024)
    warehouse.write("instruments", pa.table({
        "symbol": syms, "name": ["x"] * len(syms), "market": ["SH"] * len(syms),
        "list_date": [None] * len(syms), "delist_date": [None] * len(syms),
        "board": ["main"] * len(syms),
    }, schema=schema.INSTRUMENTS))
    build_daily_flags(warehouse, min(r[1] for r in rows), max(r[1] for r in rows))


def regime_of(warehouse, dt):
    df = WarehouseQuery(warehouse).market_regime(dt, dt)
    return df.row(0, named=True)


def test_counts_limit_ups(warehouse):
    seed(warehouse, [
        ("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
        ("600001.SH", D[1], 11.0, 11.0, 11.0, 11.0, 1e8),
        ("600002.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
        ("600002.SH", D[1], 10.1, 10.2, 10.0, 10.1, 1e8),
    ])
    build_market_regime(warehouse, D[0], D[1])
    assert regime_of(warehouse, D[1])["limit_up_count"] == 1


def test_counts_broken_boards_and_rate(warehouse):
    seed(warehouse, [
        ("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
        ("600001.SH", D[1], 11.0, 11.0, 11.0, 11.0, 1e8),   # 封住
        ("600002.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
        ("600002.SH", D[1], 10.5, 11.0, 10.2, 10.6, 1e8),   # 炸板
    ])
    build_market_regime(warehouse, D[0], D[1])
    row = regime_of(warehouse, D[1])
    assert row["touched_limit_up_count"] == 2
    assert row["broken_board_count"] == 1
    assert row["broken_board_rate"] == pytest.approx(0.5)


def test_advance_ratio_and_median_return(warehouse):
    seed(warehouse, [
        ("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
        ("600001.SH", D[1], 10.0, 10.0, 10.0, 10.5, 1e8),   # +5%
        ("600002.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
        ("600002.SH", D[1], 10.0, 10.0, 10.0, 9.5, 1e8),    # -5%
        ("600003.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
        ("600003.SH", D[1], 10.0, 10.0, 10.0, 10.2, 1e8),   # +2%
    ])
    build_market_regime(warehouse, D[0], D[1])
    row = regime_of(warehouse, D[1])
    assert row["market_advance_ratio"] == pytest.approx(2 / 3)
    assert row["market_median_return_1d"] == pytest.approx(0.02)


def test_first_board_count(warehouse):
    seed(warehouse, [
        ("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
        ("600001.SH", D[1], 11.0, 11.0, 11.0, 11.0, 1e8),   # 首板
        ("600001.SH", D[2], 12.1, 12.1, 12.1, 12.1, 1e8),   # 连板，不是首板
    ])
    build_market_regime(warehouse, D[0], D[2])
    assert regime_of(warehouse, D[1])["first_board_count"] == 1
    assert regime_of(warehouse, D[2])["first_board_count"] == 0


def test_prior_limitup_next_day_return(warehouse):
    """昨日涨停股今日的溢价——策略 S7 Gate 的 heat 指标。"""
    seed(warehouse, [
        ("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
        ("600001.SH", D[1], 11.0, 11.0, 11.0, 11.0, 1e8),   # 涨停
        ("600001.SH", D[2], 11.5, 11.6, 11.4, 11.55, 1e8),  # 次日 +5%
    ])
    build_market_regime(warehouse, D[0], D[2])
    assert regime_of(warehouse, D[2])["prior_limitup_nextday_return_mean"] == pytest.approx(0.05)


def test_total_money_is_summed(warehouse):
    seed(warehouse, [
        ("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
        ("600002.SH", D[0], 10.0, 10.0, 10.0, 10.0, 2e8),
    ])
    build_market_regime(warehouse, D[0], D[0])
    assert regime_of(warehouse, D[0])["market_total_money"] == pytest.approx(3e8)


def test_rebuild_is_idempotent(warehouse):
    seed(warehouse, [
        ("600001.SH", D[0], 10.0, 10.0, 10.0, 10.0, 1e8),
        ("600001.SH", D[1], 11.0, 11.0, 11.0, 11.0, 1e8),
    ])
    assert build_market_regime(warehouse, D[0], D[1]) == 2
    assert build_market_regime(warehouse, D[0], D[1]) == 2
    assert WarehouseQuery(warehouse).market_regime().height == 2


def test_build_on_empty_warehouse_returns_zero(warehouse):
    assert build_market_regime(warehouse, D[0], D[1]) == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/test_regime.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.pipeline.regime'`

- [ ] **Step 3: 追加 schema 与查询方法**

`src/qsys/data/schema.py` 追加并注册进 `PARTITIONED`：

```python
MARKET_REGIME = pa.schema(
    [
        ("dt", pa.date32()),
        ("limit_up_count", pa.int64()),
        ("limit_down_count", pa.int64()),
        ("limit_up_down_ratio", pa.float64()),
        ("touched_limit_up_count", pa.int64()),
        ("broken_board_count", pa.int64()),
        ("broken_board_rate", pa.float64()),
        ("first_board_count", pa.int64()),
        ("market_advance_ratio", pa.float64()),
        ("market_median_return_1d", pa.float64()),
        ("market_total_money", pa.float64()),
        ("prior_limitup_nextday_return_mean", pa.float64()),
        ("prior_limitup_nextday_return_median", pa.float64()),
        ("valid_stock_count", pa.int64()),
    ]
)
```

`WarehouseQuery` 追加 `market_regime(start=None, end=None)`，实现与 `daily_flags` 同构（按 `dt` 过滤，按 `dt` 排序）。

- [ ] **Step 4: 实现 `src/qsys/data/pipeline/regime.py`**

```python
"""市场生态因子。

策略的 S7 Gate 完全依赖这些指标判断当日市场环境。
派生自 bars_daily + daily_flags，可完全重算。

全部用 polars 向量化：全市场多年逐股 Python 循环在 1.9 GB 内存的机器上跑不动。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

_REGIME_SCHEMA = {
    "dt": pl.Date, "limit_up_count": pl.Int64, "limit_down_count": pl.Int64,
    "limit_up_down_ratio": pl.Float64, "touched_limit_up_count": pl.Int64,
    "broken_board_count": pl.Int64, "broken_board_rate": pl.Float64,
    "first_board_count": pl.Int64, "market_advance_ratio": pl.Float64,
    "market_median_return_1d": pl.Float64, "market_total_money": pl.Float64,
    "prior_limitup_nextday_return_mean": pl.Float64,
    "prior_limitup_nextday_return_median": pl.Float64,
    "valid_stock_count": pl.Int64,
}


def build_market_regime(warehouse: Warehouse, start: date, end: date) -> int:
    query = WarehouseQuery(warehouse)
    bars = query.daily_bars(None, start, end)
    flags = query.daily_flags(start, end)
    if bars.height == 0 or flags.height == 0:
        return 0

    joined = (
        bars.join(flags, on=["symbol", "dt"], how="inner")
        .sort(["symbol", "dt"])
        .with_columns([
            (pl.col("close") / pl.col("prev_close") - 1.0).alias("ret_1d"),
            pl.col("is_limit_up").shift(1).over("symbol").alias("was_limit_up"),
        ])
    )

    daily = joined.group_by("dt").agg([
        pl.col("is_limit_up").sum().cast(pl.Int64).alias("limit_up_count"),
        pl.col("is_limit_down").sum().cast(pl.Int64).alias("limit_down_count"),
        pl.col("touched_limit_up").sum().cast(pl.Int64).alias("touched_limit_up_count"),
        pl.col("is_broken_board").sum().cast(pl.Int64).alias("broken_board_count"),
        pl.col("is_first_board").sum().cast(pl.Int64).alias("first_board_count"),
        pl.len().cast(pl.Int64).alias("valid_stock_count"),
        pl.col("amount").sum().alias("market_total_money"),
        (pl.col("ret_1d") > 0).sum().alias("_up"),
        (pl.col("ret_1d") < 0).sum().alias("_down"),
        pl.col("ret_1d").median().alias("market_median_return_1d"),
        # 昨日涨停股今日的溢价
        pl.col("ret_1d").filter(pl.col("was_limit_up")).mean()
          .alias("prior_limitup_nextday_return_mean"),
        pl.col("ret_1d").filter(pl.col("was_limit_up")).median()
          .alias("prior_limitup_nextday_return_median"),
    ]).with_columns([
        (pl.col("limit_up_count") / pl.max_horizontal(pl.col("limit_down_count"), pl.lit(1)))
            .cast(pl.Float64).alias("limit_up_down_ratio"),
        pl.when(pl.col("touched_limit_up_count") > 0)
          .then(pl.col("broken_board_count") / pl.col("touched_limit_up_count"))
          .otherwise(0.0).cast(pl.Float64).alias("broken_board_rate"),
        (pl.col("_up") / pl.max_horizontal(pl.col("_up") + pl.col("_down"), pl.lit(1)))
            .cast(pl.Float64).alias("market_advance_ratio"),
    ]).drop(["_up", "_down"]).sort("dt")

    daily = daily.select(list(_REGIME_SCHEMA))

    written = 0
    for year in sorted({d.year for d in daily["dt"].to_list()}):
        chunk = daily.filter(pl.col("dt").dt.year() == year)
        warehouse.write(
            "market_regime", chunk.to_arrow().cast(schema.MARKET_REGIME), year=year
        )
        written += chunk.height

    log.info("market_regime.built", rows=written)
    return written
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/data/test_regime.py -v`
Expected: PASS —— 8 passed

- [ ] **Step 6: 提交**

```bash
git add src/qsys/data/schema.py src/qsys/data/query.py \
        src/qsys/data/pipeline/regime.py tests/data/test_regime.py
git commit -m "feat(data): 市场生态因子"
```

---

## Task 5: 估值快照

策略的 `check_high_open` 需要 `market_cap >= 70亿` 与 `circulating_market_cap <= 520亿`。

**历史估值是本计划最大的数据缺口**：百度源只有一年，东财逐只查询对全市场不可行。本任务只做**每日全市场快照采集**（`stock_zh_a_spot_em` 一次调用拿全市场），历史从采集之日起累积；更早的历史需另寻数据源或由股本反推，明确标注为缺口。

**Files:**
- Modify: `src/qsys/data/schema.py`（追加 `VALUATION_SNAPSHOT`）
- Modify: `src/qsys/data/providers/akshare_provider.py`（追加 `fetch_valuation_snapshot`）
- Create: `src/qsys/data/pipeline/valuation.py`
- Test: `tests/data/test_valuation.py`

**Interfaces:**
- Produces: `VALUATION_SNAPSHOT` schema（dt, symbol, close, market_cap, circulating_market_cap, turnover_ratio）；`AkshareProvider.fetch_valuation_snapshot() -> pl.DataFrame`；`ingest_valuation_snapshot(provider, warehouse, dt) -> int`

- [ ] **Step 1: 写失败测试**

Create `tests/data/test_valuation.py`:

```python
from datetime import date

import pandas as pd
import polars as pl
import pytest

from qsys.data.pipeline.valuation import ingest_valuation_snapshot
from qsys.data.providers.akshare_provider import AkshareProvider
from qsys.data.query import WarehouseQuery

D = date(2024, 1, 3)


class FakeApi:
    def spot(self):
        return pd.DataFrame({
            "代码": ["600036", "300750", "123456"],
            "最新价": [33.18, 191.60, 5.0],
            "总市值": [8.36e11, 8.43e11, 1e9],
            "流通市值": [6.83e11, 7.30e11, 1e9],
            "换手率": [0.51, 0.83, 1.2],
        })


def test_snapshot_columns():
    df = AkshareProvider(api=FakeApi()).fetch_valuation_snapshot()
    assert df.columns == ["symbol", "close", "market_cap",
                          "circulating_market_cap", "turnover_ratio"]


def test_snapshot_normalises_symbols():
    df = AkshareProvider(api=FakeApi()).fetch_valuation_snapshot()
    assert "600036.SH" in df["symbol"].to_list()
    assert "300750.SZ" in df["symbol"].to_list()


def test_snapshot_skips_unknown_code_segments():
    """交易所新号段不该让整次采集失败。"""
    df = AkshareProvider(api=FakeApi()).fetch_valuation_snapshot()
    assert df.height == 2


def test_market_cap_is_in_yuan_not_billions():
    df = AkshareProvider(api=FakeApi()).fetch_valuation_snapshot()
    row = df.filter(pl.col("symbol") == "600036.SH").row(0, named=True)
    assert row["market_cap"] == pytest.approx(8.36e11)


def test_ingest_writes_the_snapshot(warehouse):
    n = ingest_valuation_snapshot(AkshareProvider(api=FakeApi()), warehouse, D)
    assert n == 2
    df = WarehouseQuery(warehouse).valuation(D, D)
    assert df.height == 2
    assert df["dt"].to_list() == [D, D]


def test_ingest_is_idempotent(warehouse):
    p = AkshareProvider(api=FakeApi())
    ingest_valuation_snapshot(p, warehouse, D)
    ingest_valuation_snapshot(p, warehouse, D)
    assert WarehouseQuery(warehouse).valuation(D, D).height == 2


def test_different_days_accumulate(warehouse):
    p = AkshareProvider(api=FakeApi())
    ingest_valuation_snapshot(p, warehouse, D)
    ingest_valuation_snapshot(p, warehouse, date(2024, 1, 4))
    assert WarehouseQuery(warehouse).valuation().height == 4


def test_empty_snapshot_is_a_no_op(warehouse):
    class EmptyApi(FakeApi):
        def spot(self):
            return pd.DataFrame()

    assert ingest_valuation_snapshot(AkshareProvider(api=EmptyApi()), warehouse, D) == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/test_valuation.py -v`
Expected: FAIL —— `AttributeError: 'AkshareProvider' object has no attribute 'fetch_valuation_snapshot'`

- [ ] **Step 3: 实现**

`schema.py` 追加并注册进 `PARTITIONED`：

```python
VALUATION_SNAPSHOT = pa.schema(
    [
        ("dt", pa.date32()),
        ("symbol", pa.string()),
        ("close", pa.float64()),
        ("market_cap", pa.float64()),               # 总市值（元）
        ("circulating_market_cap", pa.float64()),   # 流通市值（元）
        ("turnover_ratio", pa.float64()),           # 换手率（%）
    ]
)
```

`WarehouseQuery` 追加 `valuation(start=None, end=None)`（与 `daily_flags` 同构）。

`_AkshareApi` 追加：

```python
    def spot(self):
        import akshare as ak

        return ak.stock_zh_a_spot_em()
```

`AkshareProvider` 追加：

```python
    def fetch_valuation_snapshot(self) -> pl.DataFrame:
        """全市场当日估值快照。一次调用拿全市场，不逐只查询。

        **已知缺口**：本接口只有当日数据，历史估值从首次采集之日起累积。
        更早的历史需另寻数据源。
        """
        empty = pl.DataFrame(schema={
            "symbol": pl.Utf8, "close": pl.Float64, "market_cap": pl.Float64,
            "circulating_market_cap": pl.Float64, "turnover_ratio": pl.Float64,
        })
        raw = pl.from_pandas(self._api.spot())
        if raw.height == 0:
            return empty

        rows: list[dict] = []
        for row in raw.iter_rows(named=True):
            try:
                symbol = normalize_symbol(str(row["代码"]))
            except ValueError:
                log.warning("valuation.unknown_code", code=row["代码"])
                continue
            rows.append({
                "symbol": symbol,
                "close": float(row.get("最新价") or 0.0),
                "market_cap": float(row.get("总市值") or 0.0),
                "circulating_market_cap": float(row.get("流通市值") or 0.0),
                "turnover_ratio": float(row.get("换手率") or 0.0),
            })
        return pl.DataFrame(rows, schema=empty.schema) if rows else empty
```

Create `src/qsys/data/pipeline/valuation.py`：

```python
"""估值快照采集。

akshare 的 stock_zh_a_spot_em 一次调用返回全市场当日行情与估值，
比逐只查询快几个数量级。

**已知缺口**：只有当日数据。历史估值从首次采集之日起累积，更早的历史
需另寻数据源。策略中依赖历史市值的过滤（check_high_open 的
market_cap >= 70亿 / circulating <= 520亿）在缺口区间内无法生效。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import structlog

from qsys.data import schema
from qsys.data.providers.akshare_provider import AkshareProvider
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

_SCHEMA = {
    "dt": pl.Date, "symbol": pl.Utf8, "close": pl.Float64,
    "market_cap": pl.Float64, "circulating_market_cap": pl.Float64,
    "turnover_ratio": pl.Float64,
}


def ingest_valuation_snapshot(
    provider: AkshareProvider, warehouse: Warehouse, dt: date
) -> int:
    snapshot = provider.fetch_valuation_snapshot()
    if snapshot.height == 0:
        return 0

    fresh = snapshot.with_columns(pl.lit(dt).cast(pl.Date).alias("dt")).select(
        list(_SCHEMA)
    )
    existing = WarehouseQuery(warehouse).valuation(
        date(dt.year, 1, 1), date(dt.year, 12, 31)
    )
    merged = (
        pl.concat([existing, fresh], how="vertical")
        .unique(subset=["dt", "symbol"], keep="last", maintain_order=True)
        .sort(["dt", "symbol"])
        .select(list(_SCHEMA))
    )
    warehouse.write(
        "valuation_snapshot",
        merged.to_arrow().cast(schema.VALUATION_SNAPSHOT),
        year=dt.year,
    )
    log.info("valuation.ingested", dt=str(dt), rows=fresh.height)
    return fresh.height
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/data/test_valuation.py -v`
Expected: PASS —— 8 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/schema.py src/qsys/data/query.py \
        src/qsys/data/providers/akshare_provider.py \
        src/qsys/data/pipeline/valuation.py tests/data/test_valuation.py
git commit -m "feat(data): 估值快照采集"
```

---

## Task 6: 数据覆盖查询

Web 上要能回答「我有哪些数据」。

**Files:**
- Create: `src/qsys/data/coverage.py`
- Test: `tests/data/test_coverage.py`

**Interfaces:**
- Produces: `DatasetCoverage(dataset, rows, symbols, first_dt, last_dt)`；`coverage(warehouse) -> list[DatasetCoverage]`；`symbol_coverage(warehouse, symbol) -> DatasetCoverage | None`

- [ ] **Step 1: 写失败测试**

Create `tests/data/test_coverage.py`:

```python
from datetime import date

import pyarrow as pa

from qsys.data import schema
from qsys.data.coverage import coverage, symbol_coverage
from qsys.data.storage import Warehouse

D = [date(2024, 1, 2), date(2024, 1, 3)]


def seed(warehouse: Warehouse) -> None:
    warehouse.write("bars_daily", pa.table({
        "symbol": ["600036.SH", "600036.SH", "000001.SZ"],
        "dt": [D[0], D[1], D[0]],
        "open": [10.0] * 3, "high": [10.0] * 3, "low": [10.0] * 3,
        "close": [10.0] * 3, "volume": [1000] * 3, "amount": [0.0] * 3,
    }, schema=schema.BARS_DAILY), year=2024)


def test_reports_nothing_on_an_empty_warehouse(warehouse):
    assert coverage(warehouse) == []


def test_reports_row_and_symbol_counts(warehouse):
    seed(warehouse)
    bars = next(c for c in coverage(warehouse) if c.dataset == "bars_daily")
    assert bars.rows == 3
    assert bars.symbols == 2


def test_reports_the_date_range(warehouse):
    seed(warehouse)
    bars = next(c for c in coverage(warehouse) if c.dataset == "bars_daily")
    assert bars.first_dt == D[0]
    assert bars.last_dt == D[1]


def test_symbol_coverage_narrows_to_one_instrument(warehouse):
    seed(warehouse)
    c = symbol_coverage(warehouse, "600036.SH")
    assert c is not None
    assert c.rows == 2
    assert c.symbols == 1


def test_symbol_coverage_of_an_absent_symbol_is_none(warehouse):
    seed(warehouse)
    assert symbol_coverage(warehouse, "999999.SH") is None


def test_datasets_without_a_date_column_are_still_reported(warehouse):
    warehouse.write("instruments", pa.table({
        "symbol": ["600036.SH"], "name": ["招商银行"], "market": ["SH"],
        "list_date": [None], "delist_date": [None], "board": ["main"],
    }, schema=schema.INSTRUMENTS))
    inst = next(c for c in coverage(warehouse) if c.dataset == "instruments")
    assert inst.rows == 1
    assert inst.first_dt is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/data/test_coverage.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.data.coverage'`

- [ ] **Step 3: 实现 `src/qsys/data/coverage.py`**

```python
"""数据覆盖查询。回答「我有哪些数据」。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb

from qsys.data import schema
from qsys.data.storage import Warehouse


@dataclass(frozen=True, slots=True)
class DatasetCoverage:
    dataset: str
    rows: int
    symbols: int
    first_dt: date | None
    last_dt: date | None


def _describe(con, glob: str, has_dt: bool, has_symbol: bool,
              where: str = "", params: list | None = None) -> tuple:
    parts = ["count(*)"]
    parts.append("count(distinct symbol)" if has_symbol else "0")
    parts.append("min(dt)" if has_dt else "NULL")
    parts.append("max(dt)" if has_dt else "NULL")
    sql = f"SELECT {', '.join(parts)} FROM read_parquet('{glob}') {where}"
    return con.execute(sql, params or []).fetchone()


def coverage(warehouse: Warehouse) -> list[DatasetCoverage]:
    con = duckdb.connect()
    out: list[DatasetCoverage] = []
    for dataset, arrow_schema in sorted(schema.ALL.items()):
        partitioned = dataset in schema.PARTITIONED
        pattern = "year=*/part.parquet" if partitioned else "part.parquet"
        base = warehouse.root / dataset
        if not list(base.glob(pattern)):
            continue
        names = {f.name for f in arrow_schema}
        rows, symbols, first, last = _describe(
            con, str(base / pattern), "dt" in names, "symbol" in names
        )
        out.append(DatasetCoverage(dataset, int(rows or 0), int(symbols or 0), first, last))
    return out


def symbol_coverage(warehouse: Warehouse, symbol: str) -> DatasetCoverage | None:
    base = warehouse.root / "bars_daily"
    if not list(base.glob("year=*/part.parquet")):
        return None
    con = duckdb.connect()
    rows, symbols, first, last = _describe(
        con, str(base / "year=*/part.parquet"), True, True,
        where="WHERE symbol = ?", params=[symbol],
    )
    if not rows:
        return None
    return DatasetCoverage("bars_daily", int(rows), int(symbols), first, last)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/data/test_coverage.py -v`
Expected: PASS —— 6 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/data/coverage.py tests/data/test_coverage.py
git commit -m "feat(data): 数据覆盖查询"
```

---

## Task 7: 全市场采集入口与端到端

**Files:**
- Create: `scripts/ingest_universe.py`
- Test: `tests/test_market_data_e2e.py`

**Interfaces:**
- Consumes: Task 1–6 的全部产出
- Produces: `scripts/ingest_universe.py`（`--start` / `--end` / `--warehouse` / `--limit` / `--batch-size` / `--boards`）

- [ ] **Step 1: 写端到端测试**

Create `tests/test_market_data_e2e.py`:

```python
"""全市场数据链路端到端：采集 -> 派生 -> 覆盖查询，全用构造数据不触网。"""

from datetime import date, timedelta

import polars as pl
import pytest

from qsys.data.coverage import coverage
from qsys.data.pipeline.flags import build_daily_flags
from qsys.data.pipeline.regime import build_market_regime
from qsys.data.pipeline.universe import ingest_universe
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse
from qsys.data import schema
import pyarrow as pa

SESSIONS = [date(2024, 1, 2) + timedelta(days=i) for i in range(5)]
BAR_SCHEMA = {
    "dt": pl.Date, "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
    "close": pl.Float64, "volume": pl.Int64, "amount": pl.Float64,
}


class ScriptedProvider:
    """第 1 只每天涨停，第 2 只炸板，其余平走。"""

    name = "scripted"

    def fetch_daily_bars(self, symbol, start, end):
        idx = int(symbol[:6]) - 600001
        rows = []
        price = 10.0
        for i, d in enumerate(SESSIONS):
            if idx == 0 and i > 0:
                close = high = round(price * 1.10, 2)
            elif idx == 1 and i > 0:
                high = round(price * 1.10, 2)
                close = round(price * 1.03, 2)
            else:
                high = close = price
            rows.append({"dt": d, "open": price, "high": high, "low": price * 0.98,
                         "close": close, "volume": 1000, "amount": close * 1000})
            price = close
        return pl.DataFrame(rows, schema=BAR_SCHEMA)


def seed_instruments(warehouse: Warehouse, symbols) -> None:
    warehouse.write("instruments", pa.table({
        "symbol": list(symbols), "name": ["x"] * len(symbols),
        "market": ["SH"] * len(symbols), "list_date": [None] * len(symbols),
        "delist_date": [None] * len(symbols), "board": ["main"] * len(symbols),
    }, schema=schema.INSTRUMENTS))


@pytest.fixture
def rig(warehouse):
    symbols = [f"{600001 + i}.SH" for i in range(5)]
    seed_instruments(warehouse, symbols)
    ingest_universe(ScriptedProvider(), warehouse, symbols, SESSIONS[0], SESSIONS[-1],
                    batch_size=2)
    build_daily_flags(warehouse, SESSIONS[0], SESSIONS[-1])
    build_market_regime(warehouse, SESSIONS[0], SESSIONS[-1])
    return warehouse, symbols


def test_all_symbols_are_ingested(rig):
    warehouse, symbols = rig
    df = WarehouseQuery(warehouse).daily_bars(None, SESSIONS[0], SESSIONS[-1])
    assert df["symbol"].n_unique() == len(symbols)


def test_limit_up_flags_are_derived(rig):
    warehouse, _ = rig
    flags = WarehouseQuery(warehouse).daily_flags(SESSIONS[1], SESSIONS[1])
    hits = flags.filter(flags["is_limit_up"])["symbol"].to_list()
    assert hits == ["600001.SH"]


def test_broken_board_is_derived(rig):
    warehouse, _ = rig
    flags = WarehouseQuery(warehouse).daily_flags(SESSIONS[1], SESSIONS[1])
    broken = flags.filter(flags["is_broken_board"])["symbol"].to_list()
    assert broken == ["600002.SH"]


def test_first_board_only_on_the_first_limit_up_day(rig):
    warehouse, _ = rig
    flags = WarehouseQuery(warehouse).daily_flags()
    first = flags.filter(flags["is_first_board"])
    assert first["symbol"].to_list() == ["600001.SH"]
    assert first["dt"].to_list() == [SESSIONS[1]]


def test_market_regime_counts_match_the_flags(rig):
    warehouse, _ = rig
    regime = WarehouseQuery(warehouse).market_regime(SESSIONS[1], SESSIONS[1]).row(0, named=True)
    assert regime["limit_up_count"] == 1
    assert regime["touched_limit_up_count"] == 2
    assert regime["broken_board_count"] == 1


def test_derived_tables_can_be_fully_rebuilt(rig):
    """派生数据不是独立事实——重算必须得到相同结果。"""
    warehouse, _ = rig
    before = WarehouseQuery(warehouse).market_regime()
    build_daily_flags(warehouse, SESSIONS[0], SESSIONS[-1])
    build_market_regime(warehouse, SESSIONS[0], SESSIONS[-1])
    after = WarehouseQuery(warehouse).market_regime()
    assert before.equals(after)


def test_coverage_lists_every_dataset(rig):
    warehouse, _ = rig
    names = {c.dataset for c in coverage(warehouse)}
    assert {"bars_daily", "daily_flags", "market_regime", "instruments"} <= names


def test_coverage_reports_the_right_range(rig):
    warehouse, _ = rig
    bars = next(c for c in coverage(warehouse) if c.dataset == "bars_daily")
    assert bars.first_dt == SESSIONS[0]
    assert bars.last_dt == SESSIONS[-1]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_market_data_e2e.py -v`
Expected: FAIL —— 前序任务若有缺漏会在此暴露

- [ ] **Step 3: 实现 `scripts/ingest_universe.py`**

```python
"""全市场日线采集与派生数据构建。

可中断可续传：已完成的标的记入 ingest_log，重跑时自动跳过。
5400 只标的的采集会跑很久，建议放 nohup 或 tmux 里。

用法：
  uv run python scripts/ingest_universe.py --start 2023-01-01 --end 2026-09-08
  uv run python scripts/ingest_universe.py --start 2023-01-01 --limit 200   # 先试小批
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import polars as pl
import structlog

from qsys.data.coverage import coverage
from qsys.data.pipeline.flags import build_daily_flags
from qsys.data.pipeline.instruments import build_instruments
from qsys.data.pipeline.regime import build_market_regime
from qsys.data.pipeline.universe import ingest_universe
from qsys.data.pipeline.valuation import ingest_valuation_snapshot
from qsys.data.providers.akshare_provider import AkshareProvider
from qsys.data.providers.mootdx_provider import MootdxProvider
from qsys.data.query import WarehouseQuery
from qsys.data.storage import Warehouse

log = structlog.get_logger("ingest")


def main() -> int:
    parser = argparse.ArgumentParser(description="全市场日线采集")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--limit", type=int, default=0, help="只采前 N 只，用于试跑")
    parser.add_argument("--boards", default="main,gem,star",
                        help="逗号分隔；默认排除北交所与 B 股")
    parser.add_argument("--skip-valuation", action="store_true")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    warehouse = Warehouse(Path(args.warehouse))
    reference = AkshareProvider()

    print("=== 标的主表 ===")
    print(f"  {build_instruments(reference, warehouse)} 只")

    instruments = WarehouseQuery(warehouse).instruments()
    boards = {b.strip() for b in args.boards.split(",") if b.strip()}
    alive = instruments.filter(
        pl.col("board").is_in(list(boards))
        & (pl.col("delist_date").is_null() | (pl.col("delist_date") >= start))
    )
    symbols = alive["symbol"].to_list()
    if args.limit:
        symbols = symbols[: args.limit]
    print(f"  待采集 {len(symbols)} 只（板块 {sorted(boards)}）")

    print("=== 日线采集 ===")
    started = time.time()

    def show(p):
        elapsed = time.time() - started
        rate = (p.done + p.failed) / elapsed if elapsed > 0 else 0
        eta = p.remaining / rate if rate > 0 else 0
        print(f"  {p.done + p.failed}/{p.total}  成功 {p.done} 失败 {p.failed}  "
              f"{rate:.1f} 只/秒  剩余约 {eta / 60:.0f} 分钟", flush=True)

    progress = ingest_universe(
        MootdxProvider(), warehouse, symbols, start, end,
        batch_size=args.batch_size, on_progress=show,
    )
    print(f"  完成: 成功 {progress.done} 失败 {progress.failed}")

    if not args.skip_valuation:
        print("=== 估值快照（当日）===")
        print(f"  {ingest_valuation_snapshot(reference, warehouse, date.today())} 行")

    print("=== 派生数据 ===")
    print(f"  涨跌停标记 {build_daily_flags(warehouse, start, end)} 行")
    print(f"  市场生态因子 {build_market_regime(warehouse, start, end)} 行")

    print("=== 覆盖情况 ===")
    for c in coverage(warehouse):
        span = f"{c.first_dt} ~ {c.last_dt}" if c.first_dt else "（无日期）"
        print(f"  {c.dataset:<20} {c.rows:>10,} 行  {c.symbols:>5} 只  {span}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_market_data_e2e.py -v`
Expected: PASS —— 8 passed

- [ ] **Step 5: 运行全部测试**

Run: `uv run pytest -q`
Expected: PASS —— 一期全部（647）+ 本计划新增项通过

- [ ] **Step 6: 小批试跑（真实数据）**

Run: `uv run python scripts/ingest_universe.py --start 2024-01-01 --end 2024-06-30 --limit 50`
Expected: 50 只采集完成，派生表生成，覆盖情况打印出来

**先试 50 只再全量**——全市场会跑很久，先确认速率与失败率。观察打印的「只/秒」估算全量耗时。

- [ ] **Step 7: 提交**

```bash
git add scripts/ingest_universe.py tests/test_market_data_e2e.py
git commit -m "feat(data): 全市场采集入口与端到端"
```

---

## 完成标准

1. `uv run pytest` 全绿，一期 647 项无回归
2. **分页有效** —— `MootdxProvider` 能取到 2023-01 至今的完整日线且 `truncated == []`
3. **分批落盘** —— `test_writes_in_batches_rather_than_all_at_once` 通过；全量采集过程中 `.venv` 进程 RSS 不超过 500 MB
4. **可中断可续传** —— 杀掉采集进程后重跑，只采未完成的标的
5. **派生数据可完全重算** —— `test_derived_tables_can_be_fully_rebuilt` 通过
6. 涨停/炸板/首板判定与策略 `get_hl_stock` / `get_ever_hl_stock2` 语义一致
7. 市场生态因子字段覆盖策略 `_s6_classify` 所需的全部输入
8. `scripts/ingest_universe.py --help` 可用，小批试跑成功

## 已知缺口（转入后续）

- **历史估值** —— `stock_zh_a_spot_em` 只有当日，历史从首次采集起累积。策略 `check_high_open` 的市值过滤在缺口区间失效。需另寻数据源或由股本反推
- **ST 历史** —— 无数据，`daily_flags` 一律按板块判涨跌幅，ST 股（5%）的涨停会漏判
- **指数因子** —— 策略的 `index_ma20_slope_5d` 等需要沪深300指数日线，本计划未覆盖（指数数据源与个股不同）
- **集合竞价成交量、11:25/14:25/14:50 日内价格** —— 需分钟线，归入计划 2-1b
- **退市股历史取不到** —— 试跑实测 mootdx 对退市股返回空。采集侧保留退市股（不做剔除），但数据源没有，全市场扫描残留约 2.7% 幸存者偏差（三板块 5550 只中 333 只已退市，2023 年后退市 145 只）。空结果按「完成」记账，不会无休止重试

## 执行实测（2026-09-09）

- **分页有效**：600036.SH 自 2023-01-01 取到 893 行、`truncated == []`（原上限 800 根）
- **小批试跑**：50 只 / 2024-01~06，19.6 只/秒，0 失败，45 只有数据（5 只为退市股）
- **内存**：试跑进程峰值 RSS 194 MB，远低于 500 MB 门槛。全市场规模的峰值仍需在 Linux 上实测
- **派生**：5265 行日线 → 5265 行 daily_flags、117 行 market_regime（117 个交易日）
- **估值**：Mac 上被代理拒绝，已降级为可失败步骤，待在 Linux 上复测

计划外的两处修正：
- `PriceLimitConstraint.limit_prices` 取整错误（前收 1.65 的跌停价算成 1.48，应为 1.49），改用 Decimal 精确乘法
- `daily_flags` / `market_regime` / `valuation` 三个查询方法用 `SELECT *` 读 `year=*` 分区会多返回一列 `year`，已改为显式列名并补回归测试

## 下一步

**计划 2-1b：分钟线（QMT 下载 + 同步）** —— Windows 侧用 `xtdata.download_history_data` 下载候选股在候选日的分钟线，异步同步到 Linux。

之后才是**策略移植**（日线近似版 → 精确版）与 **Web 回测平台**。
