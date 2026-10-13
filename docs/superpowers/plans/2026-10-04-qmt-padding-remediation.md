# QMT 填充行治理 实施计划

> **执行状态（2026-10-06）：已完成。** 本页以下复选框保留为实施时的原始步骤，
> 不再代表当前进度。PR #130 已完成源头修复、质检收敛、生产 117 个日分区备份与
> 重写，删除 1,208 行，并通过全量复验；最终数字与证据见
> `docs/reference/deferred-corrections.md` D-23 和 Issue #114。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 QMT 分钟线管线不再产生昨收价填充行，清掉生产 `minute_bars` 里已有的 20,838 行，并把质检判据换成能发现这个缺陷的那一条。

**Architecture:** Task 1–4 按序：先修源头（导出时丢弃首笔成交前的行），再换质检判据（「首根必须有成交」），再清历史数据（逐日重写分区），最后验证另外两个用同样区间调用的下载脚本。源头不修就清不干净，判据不换则清理后的正确数据会被误报。Task 5 独立，可与 Task 1 并行：下载清单扣掉直取停牌，与质检期望同源——那是 42 个整日虚构标的日的成因。

**Tech Stack:** Python 3.12、polars、pyarrow、duckdb、pytest；生产节点 Linux，下载脚本运行在 Windows（xtquant）。

**Spec:** `docs/reference/qmt-range-query-padding.md`

## Global Constraints

- 剔除规则只许删除，不许新增或推算任何值。规则：每个（标的，交易日）保留首笔成交（`volume > 0`）及其之后的行；该日无任何成交则整日丢弃。
- `volume = 0` 本身不是删除依据。**首笔成交之后**的 0 量分钟是真实的无成交分钟，必须保留。
- 每个任务结束时 `uv run pytest` 全绿。
- 分支按 `docs/development-governance.md` §3.2：从最新 `test` 开 `fix/<slug>`，本地 squash 回 `test`，随下一次 `test -> main` 发布。
- 生产数据改动必须给出前后行数、键集合与校验摘要（§4），并记录回滚点。
- 不在 Linux 上手改受 Git 管理的文件（§1.6）。

---

### Task 1: 导出时丢弃首笔成交前的填充行

**Files:**
- Modify: `scripts/win_download_minutes.py`（`to_rows`，约 77–106 行）
- Test: `tests/scripts/test_win_download_minutes.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces: `to_rows(symbol: str, data: dict, stamps: list[str]) -> list[tuple]`，行元组仍为 `(symbol, datetime, open, high, low, close, volume, amount)`，顺序与 `schema.MINUTE_BARS` 一致。后续任务不依赖它。

- [ ] **Step 1: 写失败的测试**

创建 `tests/scripts/test_win_download_minutes.py`：

```python
"""QMT 区间查询会把首笔成交前的分钟用昨收价填满（见
docs/reference/qmt-range-query-padding.md）。导出时必须丢弃那些行。"""

import pandas as pd

from scripts.win_download_minutes import to_rows


def _frames(stamps, opens, highs, lows, closes, volumes, amounts):
    """造 xtdata.get_market_data 的返回形状：{字段: DataFrame(index=[symbol])}。"""
    def frame(values):
        return pd.DataFrame([values], index=["513100.SH"], columns=stamps)
    return {"open": frame(opens), "high": frame(highs), "low": frame(lows),
            "close": frame(closes), "volume": frame(volumes), "amount": frame(amounts)}


def test_pads_before_first_trade_are_dropped():
    stamps = ["20260929093000", "20260929093100", "20260929093200"]
    data = _frames(stamps,
                   opens=[2.304, 2.304, 2.310], highs=[2.304, 2.304, 2.315],
                   lows=[2.304, 2.304, 2.308], closes=[2.304, 2.304, 2.312],
                   volumes=[0, 0, 500], amounts=[0.0, 0.0, 1156.0])
    rows = to_rows("513100.SH", data, ["20260929"])
    assert [r[1].strftime("%H:%M") for r in rows] == ["09:32"]


def test_zero_volume_after_first_trade_is_kept():
    """首笔成交之后的 0 量分钟是真实的无成交分钟，不是填充。"""
    stamps = ["20260929093000", "20260929093100", "20260929093200"]
    data = _frames(stamps,
                   opens=[2.30, 2.31, 2.31], highs=[2.30, 2.31, 2.31],
                   lows=[2.30, 2.31, 2.31], closes=[2.30, 2.31, 2.31],
                   volumes=[100, 0, 300], amounts=[230.0, 0.0, 693.0])
    rows = to_rows("513100.SH", data, ["20260929"])
    assert [r[1].strftime("%H:%M") for r in rows] == ["09:30", "09:31", "09:32"]


def test_day_without_any_trade_is_dropped_entirely():
    stamps = ["20260929093000", "20260929093100"]
    data = _frames(stamps,
                   opens=[2.304, 2.304], highs=[2.304, 2.304],
                   lows=[2.304, 2.304], closes=[2.304, 2.304],
                   volumes=[0, 0], amounts=[0.0, 0.0])
    assert to_rows("513100.SH", data, ["20260929"]) == []


def test_each_day_is_judged_on_its_own_first_trade():
    stamps = ["20260928093000", "20260928093100",
              "20260929093000", "20260929093100"]
    data = _frames(stamps,
                   opens=[2.20, 2.21, 2.30, 2.31], highs=[2.20, 2.21, 2.30, 2.31],
                   lows=[2.20, 2.21, 2.30, 2.31], closes=[2.20, 2.21, 2.30, 2.31],
                   volumes=[0, 400, 700, 0], amounts=[0.0, 884.0, 1610.0, 0.0])
    rows = to_rows("513100.SH", data, ["20260928", "20260929"])
    assert [r[1].strftime("%m-%d %H:%M") for r in rows] == [
        "09-28 09:31", "09-29 09:30", "09-29 09:31"]
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `uv run pytest tests/scripts/test_win_download_minutes.py -v`
Expected: FAIL —— `test_pads_before_first_trade_are_dropped` 实得 `['09:30','09:31','09:32']`，期望 `['09:32']`；`test_day_without_any_trade_is_dropped_entirely` 实得 2 行，期望 0 行。另两条应当已经通过。

- [ ] **Step 3: 改 `to_rows`**

把 `scripts/win_download_minutes.py` 的 `to_rows` 整体替换为：

```python
def to_rows(symbol: str, data: dict, stamps: list[str]) -> list[tuple]:
    """把 xtdata 的 {字段: DataFrame} 摊平成行。

    **丢弃每个交易日首笔成交之前的行。** `get_market_data` 按区间取数时，会把当日
    首笔成交之前的分钟用昨收价填满、`volume=0`；按单日取则不会。那些行是填充，
    不是行情——它们看起来是一笔可成交的价格，而市场上并没有发生过。
    详见 docs/reference/qmt-range-query-padding.md（2026-10-04）。

    首笔成交**之后**的 0 量分钟保留：那是真实的无成交分钟。
    """
    closes = data.get("close")
    if closes is None or closes.empty:
        return []

    columns = [c for c in closes.columns if c[:8] in stamps]
    if not columns:
        return []

    opens, highs = data["open"], data["high"]
    lows, volumes, amounts = data["low"], data["volume"], data["amount"]

    # 时间戳是 YYYYMMDDHHMMSS，字典序即时序。
    first_traded: dict[str, str] = {}
    for stamp in sorted(columns):
        day = stamp[:8]
        if day not in first_traded and int(volumes.iloc[0][stamp]) > 0:
            first_traded[day] = stamp

    rows = []
    for stamp in sorted(columns):
        start = first_traded.get(stamp[:8])
        if start is None or stamp < start:
            continue
        moment = datetime.strptime(stamp, "%Y%m%d%H%M%S")
        rows.append(
            (
                symbol,
                moment,
                float(opens.iloc[0][stamp]),
                float(highs.iloc[0][stamp]),
                float(lows.iloc[0][stamp]),
                float(closes.iloc[0][stamp]),
                int(volumes.iloc[0][stamp]),
                float(amounts.iloc[0][stamp]),
            )
        )
    return rows
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/scripts/test_win_download_minutes.py -v`
Expected: 4 passed

- [ ] **Step 5: 跑全量**

Run: `uv run pytest`
Expected: 退出码 0

- [ ] **Step 6: 提交**

```bash
git add scripts/win_download_minutes.py tests/scripts/test_win_download_minutes.py
git commit -m "fix(data): drop the bars QMT invents before the day's first trade"
```

---

### Task 2: 质检判据换成「首根必须有成交」

**Files:**
- Modify: `src/qsys/ops/service_checks.py`（`_minutes_complete`，约 576 行注册处）
- Test: `tests/ops/test_service_checks.py`（追加；若文件不存在则新建）

**Interfaces:**
- Consumes: Task 1 的产物（导出后分钟数不再恒为 241）
- Produces: `ServiceCheck("daily-archive", "分钟线首根有成交", "minute_bars", _minutes_first_bar_traded)`

**为什么换：** 「每只 241 根」不仅发现不了填充，**填充正是满足它的手段**——填充的作用恰恰是把不足 241 根的日子补齐到 241。Task 1 之后正确数据会低于 241 根，旧判据会把它们误报成缺失。

- [ ] **Step 1: 先确认测试文件位置**

Run: `ls tests/ops/ | grep service_checks`
若有 `test_service_checks.py` 就追加到该文件；没有则新建同名文件。

- [ ] **Step 2: 写失败的测试**

```python
import polars as pl

from qsys.ops.service_checks import BAD, OK, _minutes_first_bar_traded


class _Ctx:
    def __init__(self, frame):
        self._frame = frame


def _bars(rows):
    return pl.DataFrame(rows, schema={"symbol": pl.Utf8, "dt": pl.Datetime,
                                      "volume": pl.Int64}, orient="row")


def test_first_bar_without_volume_is_bad(monkeypatch):
    """首根 volume=0 就是填充残留。"""
    import qsys.ops.service_checks as sc
    from datetime import datetime
    frame = _bars([("513100.SH", datetime(2026, 9, 29, 9, 30), 0),
                   ("513100.SH", datetime(2026, 9, 29, 9, 31), 500)])
    monkeypatch.setattr(sc, "_minute_bar_rows", lambda ctx: frame)
    assert _minutes_first_bar_traded(_Ctx(frame)).status == BAD


def test_late_first_trade_is_ok(monkeypatch):
    """513100 这类首笔成交晚的标的是正确数据，不能报错。"""
    import qsys.ops.service_checks as sc
    from datetime import datetime
    frame = _bars([("513100.SH", datetime(2026, 9, 29, 10, 31), 800),
                   ("513100.SH", datetime(2026, 9, 29, 10, 32), 0)])
    monkeypatch.setattr(sc, "_minute_bar_rows", lambda ctx: frame)
    assert _minutes_first_bar_traded(_Ctx(frame)).status == OK
```

- [ ] **Step 3: 跑测试确认它失败**

Run: `uv run pytest tests/ops/test_service_checks.py -k first_bar -v`
Expected: FAIL with `ImportError: cannot import name '_minutes_first_bar_traded'`

- [ ] **Step 4: 实现**

在 `src/qsys/ops/service_checks.py` 中，于 `_minutes_complete` 之后加入：

```python
def _minute_bar_rows(ctx: CheckContext) -> pl.DataFrame:
    """当日已归档的分钟线**明细**（schema.MINUTE_BARS 全列），供首根判据使用。

    注意不能复用 `_minute_bars`：它返回的是按标的聚合后的 (symbol, bars, volume合计)，
    没有 dt，也没有逐行 volume，判不了"首根"。
    """
    wanted = _archive_wanted(ctx)
    if wanted.height == 0:
        return pl.DataFrame(schema={"symbol": pl.Utf8, "dt": pl.Datetime,
                                    "volume": pl.Int64})
    return ctx.query.minute_bars(
        wanted["symbol"].to_list(),
        datetime.combine(ctx.day, time(0, 0)),
        datetime.combine(ctx.day, time(23, 59, 59)),
    )


def _minutes_first_bar_traded(ctx: CheckContext) -> CheckResult:
    """每个标的当日的首根必须有成交。

    首根 volume=0 意味着那是 QMT 区间查询的昨收价填充残留，不是行情。
    取代原先的「每只 241 根」：填充正是满足 241 根的手段，而首笔成交晚的
    标的（如 513100.SH，首笔 10:31）在正确数据下本来就不足 241 根。
    """
    rows = _minute_bar_rows(ctx)
    if rows.height == 0:
        return CheckResult(SKIP, "首根有成交", "没有分钟线可核对")
    first = (rows.sort("dt").group_by("symbol").first())
    bad = first.filter(pl.col("volume") <= 0)
    return CheckResult(BAD if bad.height else OK, "首根有成交",
                       f"{bad.height} 只首根无成交（填充残留）",
                       _samples(set(bad["symbol"].to_list())),
                       rows=first.height)
```

把 576 行的注册改为：

```python
    ServiceCheck("daily-archive", "分钟线首根有成交", "minute_bars", _minutes_first_bar_traded),
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/ops/test_service_checks.py -k first_bar -v`
Expected: 2 passed

- [ ] **Step 6: 跑全量**

Run: `uv run pytest`
Expected: 退出码 0。**若有既存测试断言「241 根」这条检查项的名称或存在，一并更新**，并在提交信息里说明改了哪几条。

- [ ] **Step 7: 提交**

```bash
git add src/qsys/ops/service_checks.py tests/ops/test_service_checks.py
git commit -m "fix(ops): the 241-bar check was satisfied by the very padding it missed"
```

---

### Task 3: 清理生产 `minute_bars` 的 20,838 行

**Files:**
- Create: `scripts/repair_minute_bars_padding.py`
- Test: `tests/scripts/test_repair_minute_bars_padding.py`

**Interfaces:**
- Consumes: Task 1 与 Task 2 已合入（源头已修、判据已换）
- Produces: `drop_padding(frame: pl.DataFrame) -> pl.DataFrame`，入出同列（`symbol, dt, open, high, low, close, volume, amount`）

**待清理规模（2026-10-04 实测，执行前须重测）：** 整日虚构 10,122 行 / 42 标的日；前导填充 10,716 行 / 9,972 标的日；合计 20,838 行，占 233,673,841 行的 0.01%。

- [ ] **Step 1: 写失败的测试**

```python
from datetime import datetime

import polars as pl

from scripts.repair_minute_bars_padding import drop_padding

COLS = {"symbol": pl.Utf8, "dt": pl.Datetime, "open": pl.Float64,
        "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
        "volume": pl.Int64, "amount": pl.Float64}


def _f(rows):
    return pl.DataFrame(rows, schema=COLS, orient="row")


def test_leading_zero_volume_rows_are_dropped():
    frame = _f([("600000.SH", datetime(2026, 6, 15, 9, 30), 7.1, 7.1, 7.1, 7.1, 0, 0.0),
                ("600000.SH", datetime(2026, 6, 15, 9, 31), 7.1, 7.2, 7.1, 7.2, 300, 2160.0)])
    assert drop_padding(frame)["dt"].to_list() == [datetime(2026, 6, 15, 9, 31)]


def test_zero_volume_after_first_trade_survives():
    frame = _f([("600000.SH", datetime(2026, 6, 15, 9, 30), 7.1, 7.1, 7.1, 7.1, 300, 2130.0),
                ("600000.SH", datetime(2026, 6, 15, 9, 31), 7.1, 7.1, 7.1, 7.1, 0, 0.0)])
    assert drop_padding(frame).height == 2


def test_day_with_no_trade_disappears():
    frame = _f([("600000.SH", datetime(2026, 6, 15, 9, 30), 7.1, 7.1, 7.1, 7.1, 0, 0.0),
                ("600000.SH", datetime(2026, 6, 15, 9, 31), 7.1, 7.1, 7.1, 7.1, 0, 0.0)])
    assert drop_padding(frame).height == 0


def test_volume_total_is_never_changed():
    frame = _f([("600000.SH", datetime(2026, 6, 15, 9, 30), 7.1, 7.1, 7.1, 7.1, 0, 0.0),
                ("600000.SH", datetime(2026, 6, 15, 9, 31), 7.1, 7.2, 7.1, 7.2, 300, 2160.0),
                ("600001.SH", datetime(2026, 6, 15, 9, 30), 5.0, 5.0, 5.0, 5.0, 100, 500.0)])
    assert drop_padding(frame)["volume"].sum() == frame["volume"].sum()


def test_other_symbols_same_day_are_independent():
    frame = _f([("600000.SH", datetime(2026, 6, 15, 9, 30), 7.1, 7.1, 7.1, 7.1, 0, 0.0),
                ("600000.SH", datetime(2026, 6, 15, 9, 31), 7.1, 7.2, 7.1, 7.2, 300, 2160.0),
                ("600001.SH", datetime(2026, 6, 15, 9, 30), 5.0, 5.0, 5.0, 5.0, 100, 500.0)])
    out = drop_padding(frame)
    assert sorted(out["symbol"].to_list()) == ["600000.SH", "600001.SH"]
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `uv run pytest tests/scripts/test_repair_minute_bars_padding.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.repair_minute_bars_padding'`

- [ ] **Step 3: 实现**

创建 `scripts/repair_minute_bars_padding.py`：

```python
#!/usr/bin/env python3
"""清除 minute_bars 中 QMT 区间查询产生的昨收价填充行。

规则：每个（标的，交易日）保留首笔成交（volume>0）及其之后的行；该日无任何成交
则整日丢弃。纯删除，不新增、不推算任何值。
详见 docs/reference/qmt-range-query-padding.md。

逐日重写分区，先出报告后落盘（--apply 才写）。
"""

import argparse
from datetime import date
from pathlib import Path

import polars as pl
import pyarrow as pa

from qsys.data import schema
from qsys.data.storage import Warehouse


def drop_padding(frame: pl.DataFrame) -> pl.DataFrame:
    """丢弃每个（标的，日）首笔成交之前的行；整日无成交则整日丢弃。"""
    if frame.height == 0:
        return frame
    framed = frame.with_columns(pl.col("dt").dt.date().alias("_d"))
    first = (framed.filter(pl.col("volume") > 0)
                   .group_by(["symbol", "_d"])
                   .agg(pl.col("dt").min().alias("_first")))
    return (framed.join(first, on=["symbol", "_d"], how="inner")
                 .filter(pl.col("dt") >= pl.col("_first"))
                 .drop(["_d", "_first"])
                 .sort(["symbol", "dt"]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--warehouse", type=Path, required=True)
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument("--end", type=date.fromisoformat, required=True)
    ap.add_argument("--apply", action="store_true", help="不加则只报告，不落盘")
    args = ap.parse_args()

    warehouse = Warehouse(args.warehouse)
    total_before = total_after = 0
    touched = []
    for path in sorted((args.warehouse / "minute_bars").glob("year=*/date=*/part.parquet")):
        stamp = path.parent.name.removeprefix("date=")
        day = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))
        if not (args.start <= day <= args.end):
            continue
        frame = pl.read_parquet(path)
        cleaned = drop_padding(frame)
        total_before += frame.height
        total_after += cleaned.height
        if cleaned.height == frame.height:
            continue
        touched.append((day, frame.height, cleaned.height))
        if args.apply:
            table = cleaned.to_arrow().cast(schema.MINUTE_BARS)
            warehouse.write_day("minute_bars", table, day=day)

    print(f"扫描 {total_before:,} 行 -> {total_after:,} 行，"
          f"删除 {total_before - total_after:,} 行，涉及 {len(touched)} 个交易日")
    for day, before, after in touched[:20]:
        print(f"  {day}  {before:,} -> {after:,}  (-{before - after:,})")
    if not args.apply:
        print("（未加 --apply，没有写入任何文件）")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/scripts/test_repair_minute_bars_padding.py -v`
Expected: 5 passed

- [ ] **Step 5: 把新脚本加进入口导入清单**

按 `docs/development-governance.md` §3.1.2，新增入口脚本必须加进 `tests/scripts/test_scripts_import.py` 的清单。打开该文件，把 `repair_minute_bars_padding` 加进去。

Run: `uv run pytest tests/scripts/test_scripts_import.py -v`
Expected: PASS

- [ ] **Step 6: 跑全量并提交**

```bash
uv run pytest
git add scripts/repair_minute_bars_padding.py tests/scripts/test_repair_minute_bars_padding.py tests/scripts/test_scripts_import.py
git commit -m "fix(data): a tool to remove the invented bars from minute_bars"
```

- [ ] **Step 7: 生产节点上先跑只读报告**

部署到生产后，在节点上执行（**不加 `--apply`**）：

```bash
cd /home/ubuntu/qsys && .venv/bin/python scripts/repair_minute_bars_padding.py \
    --warehouse /data/qsys/warehouse --start 2025-01-01 --end 2026-12-31
```

Expected: 删除行数应接近 20,838（2026-10-04 实测值）。**数量级对不上就停下来查清楚，不要 `--apply`。**

- [ ] **Step 8: 记录回滚点并落盘**

```bash
# 回滚点：先备份受影响的分区
tar czf /data/qsys/warehouse/backups/minute_bars-pre-padding-repair-$(date +%Y%m%d).tar.gz \
    -C /data/qsys/warehouse minute_bars
# 落盘
cd /home/ubuntu/qsys && .venv/bin/python scripts/repair_minute_bars_padding.py \
    --warehouse /data/qsys/warehouse --start 2025-01-01 --end 2026-12-31 --apply
```

- [ ] **Step 9: 落盘后复验**

再跑一次只读报告，Expected: `删除 0 行，涉及 0 个交易日`。

并在节点上确认成交量合计未变——落盘前后两次执行以下查询，结果必须相同：

```bash
.venv/bin/python -c "
import duckdb
c = duckdb.connect(); c.execute(\"SET memory_limit='4GB'\"); c.execute('SET threads=2')
c.execute(\"SET temp_directory='/data/qsys/var-prod/duckdb-tmp'\")
c.execute(\"SET max_temp_directory_size='6GB'\")
print(c.execute(\"SELECT count(*), sum(volume) FROM read_parquet('/data/qsys/warehouse/minute_bars/**/*.parquet')\").fetchone())
"
```

把前后行数、成交量合计、涉及交易日数写进发布 PR 的「数据/schema/配置影响」一节（§4 要求）。

---

### Task 4: 验证另外两个下载脚本是否受同一缺陷影响

**Files:**
- Create: `docs/reference/qmt-range-query-padding.md` 的第 6 节追加结论（**修改**，不新建）
- 可能 Modify: `scripts/win_download_daily.py`、`scripts/win_download_auction_ticks.py`

**Interfaces:**
- Consumes: 无
- Produces: 一个结论——这两个脚本是否产生填充；若产生，各自的待清理规模

**背景：** 两者都用区间调用 `get_market_data`（`win_download_daily.py:87` 用 `period="1d"`，`win_download_auction_ticks.py:99` 用 `period="tick"`），与已证实有填充的分钟线调用是同一模式。**不预设结论。**

- [ ] **Step 1: 查日线是否有填充**

在生产节点上执行（只读）：

```bash
cd /home/ubuntu/qsys && .venv/bin/python -c "
import duckdb
c = duckdb.connect(); c.execute(\"SET memory_limit='4GB'\"); c.execute('SET threads=2')
c.execute(\"SET temp_directory='/data/qsys/var-prod/duckdb-tmp'\")
c.execute(\"SET max_temp_directory_size='6GB'\")
print('bar_daily 零量行:', c.execute(\"\"\"
  SELECT count(*) FILTER (WHERE volume=0) AS 零量行, count(*) AS 总行
  FROM read_parquet('/data/qsys/warehouse/bar_daily/**/*.parquet')\"\"\").fetchone())
"
```

判据：日线的 `volume=0` 行若对应 Tushare `suspend_d` 里的停牌日，那是真实停牌；若不对应，就是填充。**逐条比对，不要凭比例下结论。**

- [ ] **Step 2: 查竞价逐笔是否有填充**

```bash
cd /home/ubuntu/qsys && .venv/bin/python -c "
import duckdb
c = duckdb.connect(); c.execute(\"SET memory_limit='4GB'\"); c.execute('SET threads=2')
c.execute(\"SET temp_directory='/data/qsys/var-prod/duckdb-tmp'\")
c.execute(\"SET max_temp_directory_size='6GB'\")
print(c.execute(\"\"\"
  SELECT count(*) AS 总行,
         count(*) FILTER (WHERE last_price=0 AND volume=0) AS 空报价行
  FROM read_parquet('/data/qsys/warehouse/auction_ticks/**/*.parquet')\"\"\").fetchone())
"
```

竞价逐笔是事件序列，本来就可能有 `volume=0` 的报价快照——**`volume=0` 在这里不等于填充**。判据是：是否存在 `last_price` 等于前收盘价、且该时刻并无任何报价变化的整段重复行。

- [ ] **Step 3: 把结论写进文档**

无论结果如何，都把结论写进 `docs/reference/qmt-range-query-padding.md` 第 6 节，替换掉现有的「影响未验证」那一条。写清查了什么、判据是什么、结论是什么。**查出没问题也要写**——下一个人才不用再查一遍。

- [ ] **Step 4: 提交**

```bash
git add docs/reference/qmt-range-query-padding.md
git commit -m "docs: what the other two QMT downloaders actually do"
```

若查出这两个脚本也有填充，**不要在本计划里顺手修**——另开任务，本计划到此为止。

---

### Task 5: 下载清单扣掉直取停牌，与质检期望同源

**Files:**
- Modify: `src/qsys/data/pipeline/candidates.py`（新增 `drop_suspended`）
- Modify: `scripts/daily_archive.py`（`_minute_wanted` 调用处，约 289 行）
- Modify: `src/qsys/ops/service_checks.py`（`_archive_wanted` 改为复用）
- Test: `tests/data/pipeline/test_candidates.py`（追加；不存在则新建）

**Interfaces:**
- Consumes: 无（与 Task 1–4 独立，可并行）
- Produces: `drop_suspended(query: WarehouseQuery, days: pl.DataFrame) -> pl.DataFrame`，入出列均为 `symbol, dt`

**为什么做：** 质检的到货期望扣掉了直取停牌（`_archive_wanted`），**而下载清单没扣**——
`candidate_stock_days` 无停牌过滤，`daily_archive.py` 全文不提停牌。于是清单照样去要停牌股
的分钟线，QMT 返回 241 根前收盘价，虚构的一天进了仓库；而质检的期望里已经把它扣掉了，
所以**永远不会报**。2025-09-26 ~ 2025-12-31 的 42 个整日虚构标的日全部由此而来，
经 Tushare `suspend_d` 确认 42/42 都是全天停牌。

这个任务修的是成因；Task 3 清的是已有的 42 条。两件都要做。

- [ ] **Step 1: 写失败的测试**

```python
from datetime import date

import polars as pl

from qsys.data.pipeline.candidates import drop_suspended

DAYS = pl.DataFrame({"symbol": ["600000.SH", "600001.SH"],
                     "dt": [date(2026, 6, 15), date(2026, 6, 15)]})


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def suspensions(self, symbols, start, end):
        return pl.DataFrame(self._rows, schema={"symbol": pl.Utf8, "dt": pl.Date,
                                                "suspend_type": pl.Utf8}, orient="row")


def test_suspended_symbol_day_is_dropped():
    q = _Query([("600000.SH", date(2026, 6, 15), "S")])
    assert drop_suspended(q, DAYS)["symbol"].to_list() == ["600001.SH"]


def test_resumption_rows_are_not_dropped():
    """suspend_type='R' 是复牌，当天照常交易，不能扣。"""
    q = _Query([("600000.SH", date(2026, 6, 15), "R")])
    assert sorted(drop_suspended(q, DAYS)["symbol"].to_list()) == ["600000.SH", "600001.SH"]


def test_suspension_on_another_day_does_not_leak():
    q = _Query([("600000.SH", date(2026, 6, 12), "S")])
    assert sorted(drop_suspended(q, DAYS)["symbol"].to_list()) == ["600000.SH", "600001.SH"]


def test_empty_input_returns_empty():
    q = _Query([])
    empty = DAYS.clear()
    assert drop_suspended(q, empty).height == 0
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `uv run pytest tests/data/pipeline/test_candidates.py -k suspend -v`
Expected: FAIL with `ImportError: cannot import name 'drop_suspended'`

- [ ] **Step 3: 实现共享函数**

在 `src/qsys/data/pipeline/candidates.py` 末尾加入。顶部加 `from qsys.data.query import WarehouseQuery`
——已确认无循环 import：`query.py` 不 import `candidates`。

```python
def drop_suspended(query: WarehouseQuery, days: pl.DataFrame) -> pl.DataFrame:
    """扣掉当日全天停牌的（标的, 日期）。

    停牌真值只认 `suspend_daily`（Tushare ``suspend_d``），`suspend_type='S'` 为停牌、
    `'R'` 为复牌。

    **下载清单与服务质检的到货期望必须都走这一个函数。** 两边各写一份就会漂移：
    2025 年质检扣了停牌而下载没扣，QMT 对停牌股返回 241 根前收盘价、成交量全 0，
    42 个整日虚构的标的日因此进了仓库，而质检因为期望里已扣掉它们所以从不报警。
    见 docs/reference/qmt-range-query-padding.md。
    """
    if days.height == 0:
        return days
    suspended = (
        query.suspensions(None, days["dt"].min(), days["dt"].max())
        .filter(pl.col("suspend_type") == "S")
        .select(["symbol", "dt"])
    )
    return days.join(suspended, on=["symbol", "dt"], how="anti")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/data/pipeline/test_candidates.py -k suspend -v`
Expected: 4 passed

- [ ] **Step 5: 接到下载清单上**

`scripts/daily_archive.py` 约 289 行，把：

```python
    wanted = _minute_wanted(candidates, held)
    print(f"分钟线清单：首板次日候选 {candidates.height} 组，模拟盘持仓 {held.height} 组，"
          f"合并去重 {wanted.height} 组")
```

改为：

```python
    wanted = _minute_wanted(candidates, held)
    before = wanted.height
    wanted = drop_suspended(WarehouseQuery(warehouse), wanted)
    print(f"分钟线清单：首板次日候选 {candidates.height} 组，模拟盘持仓 {held.height} 组，"
          f"合并去重 {before} 组，扣掉全天停牌 {before - wanted.height} 组，"
          f"实取 {wanted.height} 组")
```

并在文件顶部的 `from qsys.data.pipeline.candidates import (...)` 里加上 `drop_suspended`。

- [ ] **Step 6: 让质检复用同一函数**

`src/qsys/ops/service_checks.py` 的 `_archive_wanted`，把手写的 anti-join 换掉：

```python
def _archive_wanted(ctx: CheckContext) -> pl.DataFrame:
    """与每日归档的下载清单同源：首板次日候选，扣掉当日直取停牌。

    停牌扣除走 `drop_suspended`，与 daily_archive 同一份代码——两边各写一份正是
    2025 年那 42 个整日虚构标的日的成因。
    """
    lookback = ctx.sessions[max(0, ctx.sessions.index(ctx.day) - 10)]
    wanted = candidate_stock_days(ctx.warehouse, lookback, ctx.day, sessions=ctx.sessions)
    return drop_suspended(ctx.query, wanted.filter(pl.col("dt") == ctx.day))
```

并在该文件的 import 里加上 `drop_suspended`。

- [ ] **Step 7: 跑全量**

Run: `uv run pytest`
Expected: 退出码 0。质检相关测试若断言了 `_archive_wanted` 的内部实现，一并更新。

- [ ] **Step 8: 提交**

```bash
git add src/qsys/data/pipeline/candidates.py scripts/daily_archive.py \
        src/qsys/ops/service_checks.py tests/data/pipeline/test_candidates.py
git commit -m "fix(data): the check excluded suspended days, the download never did"
```


---

## 收尾

五个任务都完成后，按 §3.2 把 `fix/<slug>` squash 回 `test` 并直接 push，CI 全绿后随下一次 `test -> main` 发布 PR 上生产。生产部署按 §9.2 手工 `workflow_dispatch`。

**本次只需重启 `qsys-web-prod`**（质检判据改动影响服务管理页）；数据同步器、paper、shadow、live core 不受影响，按 §9.2「禁止每次部署无差别重启全部服务」不重启。

## 不在本计划范围

Spec 第 6 节还列了一条**没有对应任务**的事，这里写明为什么：

**`513100.SH`、`159501.SZ`、`159941.SZ` 为什么每天恰好 10:31 才有首笔成交，不查。**
513100 连续 7 个交易日首笔都精确在 10:31，对市场行为而言整齐得可疑，但回答它需要
盘口／逐笔数据，而我们对这些标的只有分钟线。它也不影响本计划——无论原因是什么，
那 180 根都是正确数据，剔除规则对它的处理都一样。要查得先有数据，另开任务。
