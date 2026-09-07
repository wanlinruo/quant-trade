# 一期 1-4：监控台与日报 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建成实盘会话日志、告警链路、每日运行报告与只读 Web 监控台——持仓、净值曲线、当日成交与拒单、三层对账结果、数据质量报告、双向心跳。产出：一期收官，系统可进入影子模式一周的验收。

**Architecture:** 实盘会话把净值、成交、拒单、对账结果写入 `SessionJournal`（复用计划 1-1 的 Parquet 仓库），监控台只读这些表——**Web 层不持有任何业务逻辑，也不写任何数据**。页面**零外部依赖**：内联 CSS、服务端渲染 SVG 折线图、约 20 行原生 JS 自动刷新。监控页面最需要的性质是「出事的时候一定打得开」。

**Tech Stack:** Python 3.11+ · uv · FastAPI · Jinja2（自动转义）· uvicorn · polars · structlog · pytest · httpx（测试）

**Spec:** `docs/superpowers/specs/2026-09-06-personal-quant-system-design.md`（实现 §7.5 一期 Web 增量、§9 可观测性全部、§6.2 三层对账的展示面）

## 与 spec §7.3 的一处偏离

spec 指定 HTMX + ECharts + Tailwind。**一期只读监控台改用零外部依赖**：

- ECharts 约 1MB、Tailwind CDN 约 400KB，而本期页面只有一条净值曲线
- 服务器在境内，CDN 可能慢；故障时使用者可能在移动网络下
- **监控页面最需要的性质是「出事的时候一定打得开」**——零外部资源意味着断网也能看

实现方式：内联 CSS（约 120 行）、服务端渲染内联 SVG 折线图、约 20 行原生 JS 做片段轮询。
HTMX 与 ECharts 保留给三期的因子研究界面——那里的交互复杂度才真正需要它们。

## Global Constraints

- Python **3.11+**
- **Web 层只读**：一期不提供任何写接口（启停策略、改参数属四期，spec §7.5）
- **Web 层不持有业务逻辑**：所有数据来自 `SessionJournal` 与既有查询层，视图模型是纯数据结构
- **页面零外部依赖**：不引用任何 CDN、不打包任何第三方前端资源
- **默认只监听 127.0.0.1**：通过 Tailscale/WireGuard 私有网络访问，绝不暴露公网（spec §7.1）
- 所有用户可见文本经 Jinja2 **自动转义**——拒单原因、标的名称可能含 HTML 字符
- 单元测试不得连接真实 Redis、不得启动真实 uvicorn 进程（用 `TestClient`）
- 日志使用 **structlog**（spec §9）
- 环境隔离：所有命令前置
  `unset VIRTUAL_ENV; export PATH="$HOME/.local/bin:$(echo "$PATH" | tr ':' '\n' | grep -v 'project-002/.venv' | paste -sd: -)"`

---

## File Structure

```
src/qsys/data/schema.py       ← 追加四张实盘日志表
src/qsys/ops/
  __init__.py
  journal.py        SessionJournal —— 实盘会话日志读写
  alerts.py         Severity / Alert / AlertSink / LogAlertSink / WebhookAlertSink / AlertBus
  daily_report.py   DailyReport + build_daily_report
src/qsys/web/
  __init__.py
  config.py         WebConfig
  svg.py            服务端渲染的内联 SVG 折线图
  views.py          视图模型（纯数据，无 HTML）
  app.py            FastAPI 应用与路由
  templates/
    base.html       内联 CSS + 20 行自动刷新 JS
    overview.html   净值曲线 + 持仓 + 心跳
    trades.html     当日成交与拒单
    health.html     三层对账 + 数据质量
    report.html     每日运行报告
    _fragment_*.html  轮询片段
scripts/
  run_monitor.py    监控台启动入口
tests/ops/
  test_journal.py test_alerts.py test_daily_report.py
tests/web/
  test_svg.py test_views.py test_app.py
tests/
  test_monitor_e2e.py
```

---

## Task 1: 实盘日志表 schema

**Files:**
- Modify: `src/qsys/data/schema.py`
- Test: `tests/ops/test_journal.py`（本任务只加 schema 断言）

**Interfaces:**
- Consumes: 计划 1-1 的 `schema` 模块
- Produces: `LIVE_EQUITY`、`LIVE_FILLS`、`LIVE_REJECTS`、`LIVE_RECON` 四个 `pa.Schema`，全部注册进 `PARTITIONED` 与 `ALL`

- [ ] **Step 1: 写失败测试**

Create `tests/ops/test_journal.py`:

```python
import pyarrow as pa

from qsys.data import schema


def test_live_tables_are_registered():
    for name in ("live_equity", "live_fills", "live_rejects", "live_recon"):
        assert name in schema.ALL, f"{name} 未注册"
        assert name in schema.PARTITIONED, f"{name} 应按年分区"


def test_live_equity_columns():
    assert [f.name for f in schema.LIVE_EQUITY] == [
        "dt", "cash", "market_value", "total_value",
    ]


def test_live_fills_columns():
    assert [f.name for f in schema.LIVE_FILLS] == [
        "dt", "client_order_id", "symbol", "side", "qty", "price", "fee", "filled_at",
    ]


def test_live_rejects_columns():
    assert [f.name for f in schema.LIVE_REJECTS] == [
        "dt", "client_order_id", "symbol", "qty", "reason", "at",
    ]


def test_live_recon_columns():
    assert [f.name for f in schema.LIVE_RECON] == [
        "dt", "at", "layer", "is_consistent", "cash_diff", "n_mismatches", "detail",
    ]


def test_live_equity_types():
    assert schema.LIVE_EQUITY.field("dt").type == pa.date32()
    assert schema.LIVE_EQUITY.field("total_value").type == pa.float64()


def test_recon_layer_is_a_string_tag():
    """layer 取值：intraday / eod / sim_vs_live。"""
    assert schema.LIVE_RECON.field("layer").type == pa.string()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
mkdir -p tests/ops
```

Run: `uv run pytest tests/ops/test_journal.py -v`
Expected: FAIL —— `AttributeError: module 'qsys.data.schema' has no attribute 'LIVE_EQUITY'`

- [ ] **Step 3: 追加实现到 `src/qsys/data/schema.py`**

在 `INGEST_LOG` 之后、`PARTITIONED` 之前插入：

```python
# --- 实盘会话日志。监控台只读这四张表 ---

LIVE_EQUITY = pa.schema(
    [
        ("dt", pa.date32()),
        ("cash", pa.float64()),
        ("market_value", pa.float64()),
        ("total_value", pa.float64()),
    ]
)

LIVE_FILLS = pa.schema(
    [
        ("dt", pa.date32()),
        ("client_order_id", pa.string()),
        ("symbol", pa.string()),
        ("side", pa.string()),
        ("qty", pa.int64()),
        ("price", pa.float64()),
        ("fee", pa.float64()),
        ("filled_at", pa.timestamp("us")),
    ]
)

LIVE_REJECTS = pa.schema(
    [
        ("dt", pa.date32()),
        ("client_order_id", pa.string()),
        ("symbol", pa.string()),
        ("qty", pa.int64()),
        ("reason", pa.string()),
        ("at", pa.timestamp("us")),
    ]
)

LIVE_RECON = pa.schema(
    [
        ("dt", pa.date32()),
        ("at", pa.timestamp("us")),
        ("layer", pa.string()),  # intraday / eod / sim_vs_live
        ("is_consistent", pa.bool_()),
        ("cash_diff", pa.float64()),
        ("n_mismatches", pa.int64()),
        ("detail", pa.string()),
    ]
)
```

并把 `PARTITIONED` 改为：

```python
PARTITIONED: dict[str, pa.Schema] = {
    "bars_daily": BARS_DAILY,
    "ingest_log": INGEST_LOG,
    "live_equity": LIVE_EQUITY,
    "live_fills": LIVE_FILLS,
    "live_rejects": LIVE_REJECTS,
    "live_recon": LIVE_RECON,
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/ops/test_journal.py -v`
Expected: PASS —— 7 passed

- [ ] **Step 5: 确认无回归**

Run: `uv run pytest -q`
Expected: PASS —— 542 + 7 = 549 passed

- [ ] **Step 6: 提交**

```bash
git add src/qsys/data/schema.py tests/ops/test_journal.py
git commit -m "feat(data): 实盘会话日志表 schema"
```

---

## Task 2: SessionJournal

实盘会话写、监控台读。**写入必须幂等**——盘中对账每 N 分钟跑一次，重复写不得产生重复行。

**Files:**
- Create: `src/qsys/ops/__init__.py`
- Create: `src/qsys/ops/journal.py`
- Modify: `tests/ops/test_journal.py`（追加）

**Interfaces:**
- Consumes: `Warehouse` / `schema`（1-1）、`Fill`（1-2a）、`Reject`（1-2b）、`PositionReconResult` / `SlippageReport`（1-3）
- Produces: `SessionJournal(warehouse)` 带 `record_equity(dt, cash, market_value)`、`record_fills(dt, fills)`、`record_rejects(dt, rejects)`、`record_reconciliation(dt, layer, *, is_consistent, cash_diff=0.0, n_mismatches=0, detail="", at=None)`、`equity(start=None, end=None) -> pl.DataFrame`、`fills(dt) -> pl.DataFrame`、`rejects(dt) -> pl.DataFrame`、`reconciliations(dt) -> pl.DataFrame`、`sessions() -> list[date]`

- [ ] **Step 1: 追加失败测试**

Append to `tests/ops/test_journal.py`:

```python


# --- SessionJournal ---

from datetime import date, datetime

import pytest

from qsys.core.events import Reject
from qsys.core.trading import Fill, Side
from qsys.core.types import Market, Symbol
from qsys.ops.journal import SessionJournal

CMB = Symbol("600036", Market.SH)
D = date(2024, 1, 3)
T = datetime(2024, 1, 3, 9, 30)


@pytest.fixture
def journal(warehouse) -> SessionJournal:
    return SessionJournal(warehouse)


def fill(oid="c-1", qty=100, price=10.0) -> Fill:
    return Fill(oid, CMB, Side.BUY, qty, price, 5.01, T)


def reject(oid="c-1", reason="limit up") -> Reject:
    return Reject(oid, CMB, Side.BUY, 100, reason, T)


def test_record_equity_then_read_back(journal):
    journal.record_equity(D, cash=98_000.0, market_value=12_000.0)
    df = journal.equity()
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["cash"] == 98_000.0
    assert row["total_value"] == 110_000.0


def test_equity_is_idempotent_per_session(journal):
    """盘中多次记录同一天，只保留最后一次。"""
    journal.record_equity(D, cash=98_000.0, market_value=12_000.0)
    journal.record_equity(D, cash=97_000.0, market_value=13_000.0)
    df = journal.equity()
    assert df.height == 1
    assert df.row(0, named=True)["cash"] == 97_000.0


def test_equity_range_filter(journal):
    journal.record_equity(date(2024, 1, 2), cash=1.0, market_value=0.0)
    journal.record_equity(date(2024, 1, 3), cash=2.0, market_value=0.0)
    journal.record_equity(date(2024, 1, 4), cash=3.0, market_value=0.0)
    df = journal.equity(start=date(2024, 1, 3), end=date(2024, 1, 3))
    assert df["cash"].to_list() == [2.0]


def test_equity_is_sorted_by_date(journal):
    journal.record_equity(date(2024, 1, 4), cash=3.0, market_value=0.0)
    journal.record_equity(date(2024, 1, 2), cash=1.0, market_value=0.0)
    assert journal.equity()["cash"].to_list() == [1.0, 3.0]


def test_empty_equity_returns_typed_frame(journal):
    df = journal.equity()
    assert df.height == 0
    assert df.columns == ["dt", "cash", "market_value", "total_value"]


def test_record_fills_then_read_back(journal):
    journal.record_fills(D, [fill("a"), fill("b")])
    df = journal.fills(D)
    assert df.height == 2
    assert sorted(df["client_order_id"].to_list()) == ["a", "b"]


def test_fills_are_idempotent_by_client_order_id(journal):
    journal.record_fills(D, [fill("a")])
    journal.record_fills(D, [fill("a"), fill("b")])
    assert journal.fills(D).height == 2


def test_fills_of_another_session_are_excluded(journal):
    journal.record_fills(D, [fill("a")])
    journal.record_fills(date(2024, 1, 4), [fill("b")])
    assert journal.fills(D)["client_order_id"].to_list() == ["a"]


def test_recording_no_fills_is_a_no_op(journal):
    journal.record_fills(D, [])
    assert journal.fills(D).height == 0


def test_record_rejects_then_read_back(journal):
    journal.record_rejects(D, [reject("a", "limit up"), reject("b", "suspended")])
    df = journal.rejects(D)
    assert df.height == 2
    assert "limit up" in df["reason"].to_list()


def test_record_reconciliation(journal):
    journal.record_reconciliation(D, "intraday", is_consistent=True, at=T)
    df = journal.reconciliations(D)
    assert df.height == 1
    assert df.row(0, named=True)["layer"] == "intraday"
    assert df.row(0, named=True)["is_consistent"] is True


def test_reconciliation_records_mismatch_detail(journal):
    journal.record_reconciliation(
        D, "eod", is_consistent=False, cash_diff=1000.0, n_mismatches=2,
        detail="600036.SH: local=500 remote=400", at=T,
    )
    row = journal.reconciliations(D).row(0, named=True)
    assert row["is_consistent"] is False
    assert row["cash_diff"] == 1000.0
    assert row["n_mismatches"] == 2
    assert "600036.SH" in row["detail"]


def test_three_layers_coexist_in_one_session(journal):
    for layer in ("intraday", "eod", "sim_vs_live"):
        journal.record_reconciliation(D, layer, is_consistent=True, at=T)
    assert sorted(journal.reconciliations(D)["layer"].to_list()) == [
        "eod", "intraday", "sim_vs_live",
    ]


def test_repeated_intraday_reconciliation_keeps_every_run(journal):
    """盘中对账每 N 分钟一次，每次都要留痕——按时刻区分，不去重。"""
    journal.record_reconciliation(D, "intraday", is_consistent=True,
                                  at=datetime(2024, 1, 3, 10, 0))
    journal.record_reconciliation(D, "intraday", is_consistent=False,
                                  at=datetime(2024, 1, 3, 10, 5))
    assert journal.reconciliations(D).height == 2


def test_sessions_lists_recorded_days(journal):
    journal.record_equity(date(2024, 1, 2), cash=1.0, market_value=0.0)
    journal.record_equity(date(2024, 1, 4), cash=1.0, market_value=0.0)
    assert journal.sessions() == [date(2024, 1, 2), date(2024, 1, 4)]


def test_sessions_is_empty_on_a_fresh_journal(journal):
    assert journal.sessions() == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/ops/test_journal.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.ops'`

- [ ] **Step 3: 实现**

```bash
mkdir -p src/qsys/ops
touch src/qsys/ops/__init__.py
```

Create `src/qsys/ops/journal.py`:

```python
"""实盘会话日志。

实盘会话写、监控台读——Web 层不持有业务逻辑，也不写任何数据。

写入必须幂等：盘中对账每 N 分钟跑一次，成交回报可能重复投递，
重复写不得产生重复行。净值按交易日去重（保留最后一次），
成交按 client_order_id 去重，对账按 (layer, 时刻) 保留每一次运行留痕。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

import polars as pl
import structlog

from qsys.core.events import Reject
from qsys.core.trading import Fill, to_cents
from qsys.data import schema
from qsys.data.storage import Warehouse

log = structlog.get_logger(__name__)

_EPOCH = date(1990, 1, 1)
_HORIZON = date(2100, 1, 1)

_EQUITY_SCHEMA = {
    "dt": pl.Date, "cash": pl.Float64,
    "market_value": pl.Float64, "total_value": pl.Float64,
}
_FILLS_SCHEMA = {
    "dt": pl.Date, "client_order_id": pl.Utf8, "symbol": pl.Utf8, "side": pl.Utf8,
    "qty": pl.Int64, "price": pl.Float64, "fee": pl.Float64,
    "filled_at": pl.Datetime("us"),
}
_REJECTS_SCHEMA = {
    "dt": pl.Date, "client_order_id": pl.Utf8, "symbol": pl.Utf8,
    "qty": pl.Int64, "reason": pl.Utf8, "at": pl.Datetime("us"),
}
_RECON_SCHEMA = {
    "dt": pl.Date, "at": pl.Datetime("us"), "layer": pl.Utf8,
    "is_consistent": pl.Boolean, "cash_diff": pl.Float64,
    "n_mismatches": pl.Int64, "detail": pl.Utf8,
}


class SessionJournal:
    def __init__(self, warehouse: Warehouse) -> None:
        self._wh = warehouse

    # --- 内部：分区读写 ---

    def _read(self, dataset: str, year: int, pl_schema: dict) -> pl.DataFrame:
        path = self._wh.path_for(dataset, year)
        if not path.exists():
            return pl.DataFrame(schema=pl_schema)
        return pl.read_parquet(path)

    def _read_all(self, dataset: str, pl_schema: dict) -> pl.DataFrame:
        base = self._wh.root / dataset
        parts = sorted(base.glob("year=*/part.parquet"))
        if not parts:
            return pl.DataFrame(schema=pl_schema)
        return pl.concat([pl.read_parquet(p) for p in parts], how="vertical")

    def _write(
        self, dataset: str, year: int, frame: pl.DataFrame, arrow_schema
    ) -> None:
        self._wh.write(dataset, frame.to_arrow().cast(arrow_schema), year=year)

    def _merge(
        self, dataset: str, dt: date, fresh: pl.DataFrame, pl_schema: dict,
        arrow_schema, key: list[str], columns: list[str],
    ) -> None:
        existing = self._read(dataset, dt.year, pl_schema)
        merged = (
            pl.concat([existing, fresh], how="vertical")
            .unique(subset=key, keep="last", maintain_order=True)
            .sort(key)
            .select(columns)
        )
        self._write(dataset, dt.year, merged, arrow_schema)

    # --- 净值 ---

    def record_equity(self, dt: date, *, cash: float, market_value: float) -> None:
        fresh = pl.DataFrame(
            {
                "dt": [dt], "cash": [to_cents(cash)],
                "market_value": [to_cents(market_value)],
                "total_value": [to_cents(cash + market_value)],
            },
            schema=_EQUITY_SCHEMA,
        )
        self._merge("live_equity", dt, fresh, _EQUITY_SCHEMA, schema.LIVE_EQUITY,
                    ["dt"], list(_EQUITY_SCHEMA))

    def equity(self, start: date | None = None, end: date | None = None) -> pl.DataFrame:
        df = self._read_all("live_equity", _EQUITY_SCHEMA)
        if df.height == 0:
            return df
        lo = start or _EPOCH
        hi = end or _HORIZON
        return df.filter(pl.col("dt").is_between(lo, hi)).sort("dt")

    def sessions(self) -> list[date]:
        return self.equity()["dt"].to_list()

    # --- 成交 ---

    def record_fills(self, dt: date, fills: Sequence[Fill]) -> None:
        if not fills:
            return
        fresh = pl.DataFrame(
            {
                "dt": [dt] * len(fills),
                "client_order_id": [f.order_id for f in fills],
                "symbol": [str(f.symbol) for f in fills],
                "side": [f.side.value for f in fills],
                "qty": [f.qty for f in fills],
                "price": [f.price for f in fills],
                "fee": [f.fee for f in fills],
                "filled_at": [f.filled_at for f in fills],
            },
            schema=_FILLS_SCHEMA,
        )
        self._merge("live_fills", dt, fresh, _FILLS_SCHEMA, schema.LIVE_FILLS,
                    ["client_order_id"], list(_FILLS_SCHEMA))

    def fills(self, dt: date) -> pl.DataFrame:
        df = self._read("live_fills", dt.year, _FILLS_SCHEMA)
        return df.filter(pl.col("dt") == dt).sort("filled_at")

    # --- 拒单 ---

    def record_rejects(self, dt: date, rejects: Sequence[Reject]) -> None:
        if not rejects:
            return
        fresh = pl.DataFrame(
            {
                "dt": [dt] * len(rejects),
                "client_order_id": [r.order_id for r in rejects],
                "symbol": [str(r.symbol) for r in rejects],
                "qty": [r.qty for r in rejects],
                "reason": [r.reason for r in rejects],
                "at": [r.at for r in rejects],
            },
            schema=_REJECTS_SCHEMA,
        )
        self._merge("live_rejects", dt, fresh, _REJECTS_SCHEMA, schema.LIVE_REJECTS,
                    ["client_order_id", "at", "reason"], list(_REJECTS_SCHEMA))

    def rejects(self, dt: date) -> pl.DataFrame:
        df = self._read("live_rejects", dt.year, _REJECTS_SCHEMA)
        return df.filter(pl.col("dt") == dt).sort("at")

    # --- 对账 ---

    def record_reconciliation(
        self, dt: date, layer: str, *, is_consistent: bool,
        cash_diff: float = 0.0, n_mismatches: int = 0, detail: str = "",
        at: datetime | None = None,
    ) -> None:
        moment = at or datetime.now()
        fresh = pl.DataFrame(
            {
                "dt": [dt], "at": [moment], "layer": [layer],
                "is_consistent": [is_consistent], "cash_diff": [cash_diff],
                "n_mismatches": [n_mismatches], "detail": [detail],
            },
            schema=_RECON_SCHEMA,
        )
        # 每次运行都要留痕，按 (layer, 时刻) 区分
        self._merge("live_recon", dt, fresh, _RECON_SCHEMA, schema.LIVE_RECON,
                    ["layer", "at"], list(_RECON_SCHEMA))

    def reconciliations(self, dt: date) -> pl.DataFrame:
        df = self._read("live_recon", dt.year, _RECON_SCHEMA)
        return df.filter(pl.col("dt") == dt).sort("at")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/ops/test_journal.py -v`
Expected: PASS —— 23 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/ops tests/ops/test_journal.py
git commit -m "feat(ops): 实盘会话日志"
```

---

## Task 3: 告警链路

**Files:**
- Create: `src/qsys/ops/alerts.py`
- Test: `tests/ops/test_alerts.py`

**Interfaces:**
- Consumes: 无
- Produces: `Severity` (StrEnum: INFO/WARNING/CRITICAL)、`Alert(severity, title, detail, at)` 带 `as_text()`；`AlertSink` (ABC: `send(alert)`)、`LogAlertSink()`、`RecordingAlertSink()` 带 `alerts`、`WebhookAlertSink(url, *, timeout=5.0, poster=None)`；`AlertBus(sinks)` 带 `publish(alert)`、`info/warning/critical(title, detail="")`

- [ ] **Step 1: 写失败测试**

Create `tests/ops/test_alerts.py`:

```python
from datetime import datetime

import pytest

from qsys.ops.alerts import (
    Alert,
    AlertBus,
    RecordingAlertSink,
    Severity,
    WebhookAlertSink,
)

T = datetime(2024, 1, 3, 10, 0)


def test_alert_text_contains_severity_and_title():
    text = Alert(Severity.CRITICAL, "持仓不一致", "600036.SH local=500 remote=400", T).as_text()
    assert "CRITICAL" in text
    assert "持仓不一致" in text
    assert "600036.SH" in text


def test_alert_text_without_detail():
    text = Alert(Severity.INFO, "启动完成", "", T).as_text()
    assert "启动完成" in text


def test_recording_sink_captures_alerts():
    sink = RecordingAlertSink()
    bus = AlertBus([sink])
    bus.warning("心跳超时", "agent 90 秒无心跳")
    assert len(sink.alerts) == 1
    assert sink.alerts[0].severity is Severity.WARNING


def test_bus_fans_out_to_every_sink():
    a, b = RecordingAlertSink(), RecordingAlertSink()
    AlertBus([a, b]).critical("停机")
    assert len(a.alerts) == 1 and len(b.alerts) == 1


def test_severity_helpers():
    sink = RecordingAlertSink()
    bus = AlertBus([sink])
    bus.info("i")
    bus.warning("w")
    bus.critical("c")
    assert [a.severity for a in sink.alerts] == [
        Severity.INFO, Severity.WARNING, Severity.CRITICAL,
    ]


def test_a_failing_sink_does_not_block_the_others():
    """告警发不出去也不能让主流程崩——告警是旁路，不是主路。"""

    class Broken(RecordingAlertSink):
        def send(self, alert):
            raise RuntimeError("webhook down")

    good = RecordingAlertSink()
    AlertBus([Broken(), good]).critical("重要告警")
    assert len(good.alerts) == 1


def test_bus_with_no_sinks_does_not_raise():
    AlertBus([]).critical("nobody listening")


def test_webhook_posts_the_alert_text():
    posted = []

    def poster(url, payload, timeout):
        posted.append((url, payload, timeout))

    sink = WebhookAlertSink("https://example.invalid/hook", poster=poster)
    sink.send(Alert(Severity.CRITICAL, "熔断", "日内亏损 5%", T))

    assert posted[0][0] == "https://example.invalid/hook"
    assert "熔断" in str(posted[0][1])
    assert posted[0][2] == 5.0


def test_webhook_timeout_is_configurable():
    posted = []
    sink = WebhookAlertSink("https://x.invalid", timeout=1.5,
                            poster=lambda u, p, t: posted.append(t))
    sink.send(Alert(Severity.INFO, "x", "", T))
    assert posted[0] == 1.5


def test_alert_is_frozen():
    with pytest.raises(Exception):
        Alert(Severity.INFO, "x", "", T).title = "y"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/ops/test_alerts.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.ops.alerts'`

- [ ] **Step 3: 实现 `src/qsys/ops/alerts.py`**

```python
"""告警链路。

告警是旁路，不是主路：任何一个 sink 发送失败都不得中断主流程，
更不得让其它 sink 收不到——实盘出问题时最不该发生的就是
「告警系统自己挂了导致你不知道出了问题」。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import structlog

log = structlog.get_logger(__name__)


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class Alert:
    severity: Severity
    title: str
    detail: str
    at: datetime

    def as_text(self) -> str:
        stamp = self.at.strftime("%Y-%m-%d %H:%M:%S")
        head = f"[{self.severity.value}] {stamp} {self.title}"
        return f"{head}\n{self.detail}" if self.detail else head


class AlertSink(ABC):
    @abstractmethod
    def send(self, alert: Alert) -> None: ...


class LogAlertSink(AlertSink):
    """始终装配的兜底 sink：至少日志里留得下。"""

    def send(self, alert: Alert) -> None:
        emit = {
            Severity.INFO: log.info,
            Severity.WARNING: log.warning,
            Severity.CRITICAL: log.error,
        }[alert.severity]
        emit("alert", title=alert.title, detail=alert.detail, at=alert.at.isoformat())


class RecordingAlertSink(AlertSink):
    """测试用。"""

    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def send(self, alert: Alert) -> None:
        self.alerts.append(alert)


def _default_poster(url: str, payload: dict, timeout: float) -> None:
    import json
    import urllib.request

    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(request, timeout=timeout).close()


class WebhookAlertSink(AlertSink):
    """通用 webhook —— 企业微信 / 钉钉 / Server 酱等均可对接。

    poster 可注入以便测试；默认用标准库，不引入新依赖。
    """

    def __init__(
        self, url: str, *, timeout: float = 5.0,
        poster: Callable[[str, dict, float], None] | None = None,
    ) -> None:
        self._url = url
        self._timeout = timeout
        self._poster = poster or _default_poster

    def send(self, alert: Alert) -> None:
        self._poster(
            self._url,
            {"msgtype": "text", "text": {"content": alert.as_text()}},
            self._timeout,
        )


class AlertBus:
    def __init__(self, sinks: Sequence[AlertSink]) -> None:
        self._sinks = tuple(sinks)

    def publish(self, alert: Alert) -> None:
        for sink in self._sinks:
            try:
                sink.send(alert)
            except Exception as exc:  # 一个 sink 挂了不能影响其它 sink 与主流程
                log.error("alert.sink_failed", sink=type(sink).__name__, error=str(exc))

    def info(self, title: str, detail: str = "") -> None:
        self.publish(Alert(Severity.INFO, title, detail, datetime.now()))

    def warning(self, title: str, detail: str = "") -> None:
        self.publish(Alert(Severity.WARNING, title, detail, datetime.now()))

    def critical(self, title: str, detail: str = "") -> None:
        self.publish(Alert(Severity.CRITICAL, title, detail, datetime.now()))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/ops/test_alerts.py -v`
Expected: PASS —— 10 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/ops/alerts.py tests/ops/test_alerts.py
git commit -m "feat(ops): 告警链路"
```

---

## Task 4: 每日运行报告

**Files:**
- Create: `src/qsys/ops/daily_report.py`
- Test: `tests/ops/test_daily_report.py`

**Interfaces:**
- Consumes: `SessionJournal`（Task 2）、`QualityReport`（1-1）
- Produces: `EquityLine(dt, cash, market_value, total_value, change_pct)`；`DailyReport(dt, equity, fills, rejects, reconciliations, quality_summary, dead_sources)` 带 `is_healthy: bool`、`headline: str`、`as_text() -> str`；`build_daily_report(journal, dt, *, quality_summary="", dead_sources=()) -> DailyReport`

- [ ] **Step 1: 写失败测试**

Create `tests/ops/test_daily_report.py`:

```python
from datetime import date, datetime

import pytest

from qsys.core.events import Reject
from qsys.core.trading import Fill, Side
from qsys.core.types import Market, Symbol
from qsys.ops.daily_report import build_daily_report
from qsys.ops.journal import SessionJournal

CMB = Symbol("600036", Market.SH)
D0 = date(2024, 1, 2)
D = date(2024, 1, 3)
T = datetime(2024, 1, 3, 9, 30)


@pytest.fixture
def journal(warehouse) -> SessionJournal:
    return SessionJournal(warehouse)


def seed(journal: SessionJournal, *, consistent: bool = True) -> None:
    journal.record_equity(D0, cash=100_000.0, market_value=0.0)
    journal.record_equity(D, cash=88_000.0, market_value=13_000.0)
    journal.record_fills(D, [Fill("a", CMB, Side.BUY, 1000, 12.0, 5.1, T)])
    journal.record_reconciliation(
        D, "intraday", is_consistent=consistent,
        cash_diff=0.0 if consistent else 1234.5,
        n_mismatches=0 if consistent else 1,
        detail="" if consistent else "600036.SH: local=500 remote=400", at=T,
    )


def test_report_carries_the_session_date(journal):
    seed(journal)
    assert build_daily_report(journal, D).dt == D


def test_equity_change_is_computed_against_the_previous_session(journal):
    seed(journal)
    report = build_daily_report(journal, D)
    # 100000 -> 101000 = +1%
    assert report.equity.total_value == 101_000.0
    assert report.equity.change_pct == pytest.approx(0.01)


def test_change_is_zero_on_the_first_session(journal):
    journal.record_equity(D, cash=100_000.0, market_value=0.0)
    assert build_daily_report(journal, D).equity.change_pct == 0.0


def test_report_counts_fills_and_rejects(journal):
    seed(journal)
    journal.record_rejects(D, [Reject("b", CMB, Side.BUY, 100, "limit up", T)])
    report = build_daily_report(journal, D)
    assert len(report.fills) == 1
    assert len(report.rejects) == 1


def test_healthy_when_reconciliation_passes(journal):
    seed(journal, consistent=True)
    assert build_daily_report(journal, D).is_healthy is True


def test_unhealthy_when_reconciliation_fails(journal):
    seed(journal, consistent=False)
    report = build_daily_report(journal, D)
    assert report.is_healthy is False
    assert "对账" in report.headline


def test_unhealthy_when_a_source_is_silent(journal):
    seed(journal)
    report = build_daily_report(journal, D, dead_sources=("agent",))
    assert report.is_healthy is False
    assert "agent" in report.headline


def test_text_report_contains_every_section(journal):
    seed(journal)
    text = build_daily_report(journal, D, quality_summary="quality: clean").as_text()
    for marker in ("净值", "成交", "拒单", "对账", "数据质量"):
        assert marker in text


def test_text_report_shows_reject_reasons(journal):
    seed(journal)
    journal.record_rejects(D, [Reject("b", CMB, Side.BUY, 100, "涨停买不到", T)])
    assert "涨停买不到" in build_daily_report(journal, D).as_text()


def test_text_report_shows_mismatch_detail(journal):
    seed(journal, consistent=False)
    assert "600036.SH" in build_daily_report(journal, D).as_text()


def test_report_on_a_session_with_no_data(journal):
    report = build_daily_report(journal, D)
    assert report.equity.total_value == 0.0
    assert report.fills == ()
    assert report.is_healthy is True


def test_headline_is_short_and_actionable(journal):
    seed(journal, consistent=False)
    headline = build_daily_report(journal, D).headline
    assert len(headline) < 120
    assert headline.startswith("异常") or headline.startswith("正常")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/ops/test_daily_report.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.ops.daily_report'`

- [ ] **Step 3: 实现 `src/qsys/ops/daily_report.py`**

```python
"""每日运行报告。

一条消息回答四个问题：钱变了多少、成交了什么、有什么被拒了、
对账过没过。异常时 headline 必须一眼可读——你可能是在手机上看它。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import polars as pl

from qsys.ops.journal import SessionJournal


@dataclass(frozen=True, slots=True)
class EquityLine:
    dt: date
    cash: float = 0.0
    market_value: float = 0.0
    total_value: float = 0.0
    change_pct: float = 0.0


@dataclass(frozen=True, slots=True)
class DailyReport:
    dt: date
    equity: EquityLine
    fills: tuple[dict, ...]
    rejects: tuple[dict, ...]
    reconciliations: tuple[dict, ...]
    quality_summary: str = ""
    dead_sources: tuple[str, ...] = ()

    @property
    def failed_reconciliations(self) -> tuple[dict, ...]:
        return tuple(r for r in self.reconciliations if not r["is_consistent"])

    @property
    def is_healthy(self) -> bool:
        return not self.failed_reconciliations and not self.dead_sources

    @property
    def headline(self) -> str:
        if self.is_healthy:
            return (
                f"正常 · {self.dt} · 净值 {self.equity.total_value:,.2f} "
                f"({self.equity.change_pct:+.2%}) · 成交 {len(self.fills)} 笔"
            )
        problems: list[str] = []
        if self.failed_reconciliations:
            problems.append(f"对账不一致 {len(self.failed_reconciliations)} 次")
        if self.dead_sources:
            problems.append(f"失联: {', '.join(self.dead_sources)}")
        return f"异常 · {self.dt} · " + " · ".join(problems)

    def as_text(self) -> str:
        lines = [
            "=" * 60,
            f"每日运行报告 {self.dt}",
            "=" * 60,
            self.headline,
            "",
            "净值",
            f"  现金      : {self.equity.cash:,.2f}",
            f"  持仓市值  : {self.equity.market_value:,.2f}",
            f"  总值      : {self.equity.total_value:,.2f} ({self.equity.change_pct:+.2%})",
            "",
            f"成交 ({len(self.fills)} 笔)",
        ]
        for f in self.fills[:20]:
            lines.append(
                f"  {f['symbol']} {f['side']} {f['qty']} @ {f['price']:.2f} "
                f"费用 {f['fee']:.2f}"
            )
        if len(self.fills) > 20:
            lines.append(f"  ... 另有 {len(self.fills) - 20} 笔")

        lines += ["", f"拒单 ({len(self.rejects)} 笔)"]
        for r in self.rejects[:20]:
            lines.append(f"  {r['symbol']} {r['qty']} —— {r['reason']}")
        if len(self.rejects) > 20:
            lines.append(f"  ... 另有 {len(self.rejects) - 20} 笔")

        lines += ["", f"对账 ({len(self.reconciliations)} 次)"]
        for r in self.reconciliations:
            status = "一致" if r["is_consistent"] else "不一致"
            lines.append(f"  [{r['layer']}] {status}")
            if not r["is_consistent"]:
                lines.append(
                    f"      现金差 {r['cash_diff']:,.2f} · 持仓不符 {r['n_mismatches']} 项"
                )
                if r["detail"]:
                    lines.append(f"      {r['detail']}")

        lines += ["", "数据质量", f"  {self.quality_summary or '（未运行）'}"]
        if self.dead_sources:
            lines += ["", "失联", f"  {', '.join(self.dead_sources)}"]
        lines.append("=" * 60)
        return "\n".join(lines)


def _rows(df: pl.DataFrame) -> tuple[dict, ...]:
    return tuple(df.iter_rows(named=True))


def build_daily_report(
    journal: SessionJournal,
    dt: date,
    *,
    quality_summary: str = "",
    dead_sources: Sequence[str] = (),
) -> DailyReport:
    curve = journal.equity(end=dt)
    equity = EquityLine(dt=dt)
    if curve.height:
        last = curve.row(curve.height - 1, named=True)
        change = 0.0
        if curve.height > 1:
            prev = curve.row(curve.height - 2, named=True)["total_value"]
            if prev:
                change = last["total_value"] / prev - 1.0
        equity = EquityLine(
            dt=dt, cash=last["cash"], market_value=last["market_value"],
            total_value=last["total_value"], change_pct=change,
        )

    return DailyReport(
        dt=dt,
        equity=equity,
        fills=_rows(journal.fills(dt)),
        rejects=_rows(journal.rejects(dt)),
        reconciliations=_rows(journal.reconciliations(dt)),
        quality_summary=quality_summary,
        dead_sources=tuple(dead_sources),
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/ops/test_daily_report.py -v`
Expected: PASS —— 12 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/ops/daily_report.py tests/ops/test_daily_report.py
git commit -m "feat(ops): 每日运行报告"
```

---

## Task 5: 服务端 SVG 折线图

零外部依赖的核心：图表在服务端渲染成内联 SVG，浏览器不需要下载任何 JS 库。

**Files:**
- Create: `src/qsys/web/__init__.py`
- Create: `src/qsys/web/svg.py`
- Test: `tests/web/test_svg.py`

**Interfaces:**
- Consumes: 无
- Produces: `line_chart(values, *, width=760, height=200, padding=24, stroke="#2563eb") -> str`（返回 SVG 标记）

- [ ] **Step 1: 写失败测试**

Create `tests/web/test_svg.py`:

```python
import re

from qsys.web.svg import line_chart


def test_returns_an_svg_element():
    svg = line_chart([1.0, 2.0, 3.0])
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")


def test_dimensions_are_honored():
    svg = line_chart([1.0, 2.0], width=400, height=100)
    assert 'width="400"' in svg
    assert 'height="100"' in svg


def test_one_point_per_value():
    svg = line_chart([1.0, 2.0, 3.0, 4.0])
    points = re.search(r'points="([^"]+)"', svg).group(1)
    assert len(points.split()) == 4


def test_rising_series_goes_up_on_screen():
    """SVG 的 y 轴向下，上升序列的 y 必须递减。"""
    svg = line_chart([1.0, 2.0, 3.0])
    points = re.search(r'points="([^"]+)"', svg).group(1).split()
    ys = [float(p.split(",")[1]) for p in points]
    assert ys[0] > ys[-1]


def test_flat_series_is_a_horizontal_line():
    svg = line_chart([5.0, 5.0, 5.0])
    points = re.search(r'points="([^"]+)"', svg).group(1).split()
    ys = {round(float(p.split(",")[1]), 3) for p in points}
    assert len(ys) == 1


def test_empty_series_renders_a_placeholder():
    svg = line_chart([])
    assert "<svg" in svg
    assert "暂无数据" in svg


def test_single_point_does_not_crash():
    svg = line_chart([42.0])
    assert "<svg" in svg


def test_stroke_colour_is_applied():
    assert 'stroke="#ff0000"' in line_chart([1.0, 2.0], stroke="#ff0000")


def test_points_stay_inside_the_padded_area():
    svg = line_chart([1.0, 5.0, 3.0], width=200, height=100, padding=10)
    points = re.search(r'points="([^"]+)"', svg).group(1).split()
    for p in points:
        x, y = (float(v) for v in p.split(","))
        assert 10 <= x <= 190
        assert 10 <= y <= 90


def test_no_external_references():
    """零外部依赖：除 SVG 命名空间外不得出现任何 http 引用。"""
    svg = line_chart([1.0, 2.0, 3.0]).replace(
        'xmlns="http://www.w3.org/2000/svg"', ""
    )
    assert "http://" not in svg
    assert "https://" not in svg
```

- [ ] **Step 2: 运行测试确认失败**

```bash
mkdir -p src/qsys/web tests/web
touch src/qsys/web/__init__.py
```

Run: `uv run pytest tests/web/test_svg.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.web.svg'`

- [ ] **Step 3: 实现 `src/qsys/web/svg.py`**

```python
"""服务端渲染的内联 SVG 折线图。

零外部依赖：图表在服务端算好坐标直接输出 SVG，浏览器不下载任何 JS 库。
监控页面最需要的性质是「出事的时候一定打得开」。
"""

from __future__ import annotations

from collections.abc import Sequence


def line_chart(
    values: Sequence[float],
    *,
    width: int = 760,
    height: int = 200,
    padding: int = 24,
    stroke: str = "#2563eb",
) -> str:
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">'
    )

    if not values:
        return (
            f"{head}"
            f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle" '
            f'fill="#94a3b8" font-size="14">暂无数据</text>'
            "</svg>"
        )

    inner_w = max(1, width - 2 * padding)
    inner_h = max(1, height - 2 * padding)
    lo, hi = min(values), max(values)
    span = hi - lo

    def x_of(i: int) -> float:
        if len(values) == 1:
            return padding + inner_w / 2
        return padding + inner_w * i / (len(values) - 1)

    def y_of(v: float) -> float:
        if span == 0:
            return padding + inner_h / 2  # 平线居中
        return padding + inner_h * (1.0 - (v - lo) / span)

    points = " ".join(f"{x_of(i):.2f},{y_of(v):.2f}" for i, v in enumerate(values))
    baseline = padding + inner_h

    return (
        f"{head}"
        f'<polyline fill="none" stroke="{stroke}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round" points="{points}" />'
        f'<line x1="{padding}" y1="{baseline}" x2="{width - padding}" y2="{baseline}" '
        f'stroke="#e2e8f0" stroke-width="1" />'
        "</svg>"
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/web/test_svg.py -v`
Expected: PASS —— 10 passed

- [ ] **Step 5: 提交**

```bash
git add src/qsys/web tests/web/test_svg.py
git commit -m "feat(web): 服务端渲染的内联 SVG 折线图"
```

---

## Task 6: 视图模型

视图模型是**纯数据结构**，不含任何 HTML——它让页面逻辑可以脱离 Web 框架单独测试。

**Files:**
- Create: `src/qsys/web/config.py`
- Create: `src/qsys/web/views.py`
- Test: `tests/web/test_views.py`

**Interfaces:**
- Consumes: `SessionJournal`（Task 2）、`DailyReport`（Task 4）、`AccountSnapshot` / `HeartbeatMonitor`（1-3）
- Produces: `WebConfig(host="127.0.0.1", port=8000, title="量化监控台", equity_days=120, watch_sources=("agent","core"), heartbeat_max_age=90.0)`；`OverviewView(session, equity_curve, equity_line, positions, cash, heartbeats, is_healthy)`；`TradesView(session, fills, rejects)`；`HealthView(session, reconciliations, quality_summary, dead_sources)`；`MonitorService(journal, *, config, transport=None, quality_summary_provider=None)` 带 `latest_session() -> date | None`、`has_session(date) -> bool`、`overview(session) -> OverviewView`、`trades(session) -> TradesView`、`health(session) -> HealthView`、`report(session) -> DailyReport`

- [ ] **Step 1: 写失败测试**

Create `tests/web/test_views.py`:

```python
from datetime import date, datetime, timedelta

import pytest

from qsys.core.events import Reject
from qsys.core.trading import Fill, Side
from qsys.core.types import Market, Symbol
from qsys.live.heartbeat import HeartbeatMonitor
from qsys.live.messages import AccountSnapshot, PositionSnapshot
from qsys.live.transport import MemoryTransport
from qsys.ops.journal import SessionJournal
from qsys.web.config import WebConfig
from qsys.web.views import MonitorService

CMB = Symbol("600036", Market.SH)
D0 = date(2024, 1, 2)
D = date(2024, 1, 3)
T = datetime(2024, 1, 3, 9, 30)


@pytest.fixture
def service(warehouse):
    journal = SessionJournal(warehouse)
    journal.record_equity(D0, cash=100_000.0, market_value=0.0)
    journal.record_equity(D, cash=88_000.0, market_value=13_000.0)
    journal.record_fills(D, [Fill("a", CMB, Side.BUY, 1000, 12.0, 5.1, T)])
    journal.record_rejects(D, [Reject("b", CMB, Side.BUY, 100, "涨停买不到", T)])
    journal.record_reconciliation(D, "intraday", is_consistent=True, at=T)

    transport = MemoryTransport()
    transport.set_key(
        "state:account",
        AccountSnapshot(
            at=T, cash=88_000.0,
            positions={"600036.SH": PositionSnapshot(total_qty=1000, available_qty=0,
                                                     avg_cost=12.01)},
        ).model_dump_json(),
    )
    HeartbeatMonitor(transport).beat("agent")

    return MonitorService(journal, config=WebConfig(), transport=transport)


def test_latest_session(service):
    assert service.latest_session() == D


def test_latest_session_is_none_when_empty(warehouse):
    svc = MonitorService(SessionJournal(warehouse), config=WebConfig())
    assert svc.latest_session() is None


def test_has_session(service):
    assert service.has_session(D) is True
    assert service.has_session(date(2020, 1, 1)) is False


def test_overview_carries_the_equity_curve(service):
    view = service.overview(D)
    assert view.equity_curve == [100_000.0, 101_000.0]


def test_overview_equity_line_shows_change(service):
    view = service.overview(D)
    assert view.equity_line.total_value == 101_000.0
    assert view.equity_line.change_pct == pytest.approx(0.01)


def test_overview_lists_positions_from_the_account_snapshot(service):
    view = service.overview(D)
    assert len(view.positions) == 1
    assert view.positions[0]["symbol"] == "600036.SH"
    assert view.positions[0]["total_qty"] == 1000


def test_overview_positions_are_empty_without_a_snapshot(warehouse):
    svc = MonitorService(SessionJournal(warehouse), config=WebConfig(),
                         transport=MemoryTransport())
    assert svc.overview(D).positions == ()


def test_overview_reports_heartbeats(service):
    view = service.overview(D)
    assert any(h["source"] == "agent" and h["alive"] for h in view.heartbeats)


def test_overview_marks_a_silent_source(service):
    """心跳超时必须被标出来。"""
    view = service.overview(D, now=datetime.now() + timedelta(seconds=600))
    assert all(not h["alive"] for h in view.heartbeats)
    assert view.is_healthy is False


def test_overview_without_a_transport_has_no_heartbeats(warehouse):
    svc = MonitorService(SessionJournal(warehouse), config=WebConfig())
    assert svc.overview(D).heartbeats == ()


def test_trades_view(service):
    view = service.trades(D)
    assert len(view.fills) == 1
    assert len(view.rejects) == 1
    assert view.rejects[0]["reason"] == "涨停买不到"


def test_health_view(service):
    view = service.health(D)
    assert len(view.reconciliations) == 1
    assert view.reconciliations[0]["layer"] == "intraday"


def test_health_view_uses_the_quality_provider(warehouse):
    svc = MonitorService(
        SessionJournal(warehouse), config=WebConfig(),
        quality_summary_provider=lambda: "quality: clean (0 violations)",
    )
    assert "clean" in svc.health(D).quality_summary


def test_quality_provider_failure_is_contained(warehouse):
    """质量报告取不到不能让整个页面 500。"""

    def broken() -> str:
        raise RuntimeError("warehouse offline")

    svc = MonitorService(SessionJournal(warehouse), config=WebConfig(),
                         quality_summary_provider=broken)
    assert "warehouse offline" in svc.health(D).quality_summary


def test_report_builds_a_daily_report(service):
    report = service.report(D)
    assert report.dt == D
    assert report.is_healthy is True


def test_equity_curve_is_limited_by_config(warehouse):
    journal = SessionJournal(warehouse)
    for i in range(10):
        journal.record_equity(date(2024, 1, 1) + timedelta(days=i),
                              cash=float(i), market_value=0.0)
    svc = MonitorService(journal, config=WebConfig(equity_days=3))
    assert len(svc.overview(date(2024, 1, 10)).equity_curve) == 3
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/web/test_views.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.web.config'`

- [ ] **Step 3: 实现 `src/qsys/web/config.py`**

```python
"""监控台配置。

默认只监听 127.0.0.1 —— 通过 Tailscale / WireGuard 私有网络访问。
一个能看到持仓的页面不该出现在公网上。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class WebConfig:
    host: str = "127.0.0.1"  # 绝不默认 0.0.0.0
    port: int = 8000
    title: str = "量化监控台"
    equity_days: int = 120
    watch_sources: tuple[str, ...] = ("agent", "core")
    heartbeat_max_age: float = 90.0
    account_key: str = "state:account"
    heartbeat_prefix: str = "state:heartbeat"
```

- [ ] **Step 4: 实现 `src/qsys/web/views.py`**

```python
"""视图模型。

纯数据结构，不含任何 HTML —— 页面逻辑因此可以脱离 Web 框架单独测试。
Web 层不持有业务逻辑，也不写任何数据。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

import structlog

from qsys.live.heartbeat import HeartbeatMonitor
from qsys.live.messages import AccountSnapshot
from qsys.live.transport import Transport
from qsys.ops.daily_report import DailyReport, EquityLine, build_daily_report
from qsys.ops.journal import SessionJournal
from qsys.web.config import WebConfig

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class OverviewView:
    session: date
    equity_curve: list[float]
    equity_line: EquityLine
    positions: tuple[dict, ...]
    cash: float
    heartbeats: tuple[dict, ...]
    is_healthy: bool


@dataclass(frozen=True, slots=True)
class TradesView:
    session: date
    fills: tuple[dict, ...]
    rejects: tuple[dict, ...]


@dataclass(frozen=True, slots=True)
class HealthView:
    session: date
    reconciliations: tuple[dict, ...]
    quality_summary: str
    dead_sources: tuple[str, ...]


class MonitorService:
    def __init__(
        self,
        journal: SessionJournal,
        *,
        config: WebConfig,
        transport: Transport | None = None,
        quality_summary_provider: Callable[[], str] | None = None,
    ) -> None:
        self._journal = journal
        self._cfg = config
        self._transport = transport
        self._quality = quality_summary_provider

    # --- 辅助 ---

    def latest_session(self) -> date | None:
        sessions = self._journal.sessions()
        return sessions[-1] if sessions else None

    def has_session(self, session: date) -> bool:
        return session in self._journal.sessions()

    def _account(self) -> AccountSnapshot | None:
        if self._transport is None:
            return None
        raw = self._transport.get_key(self._cfg.account_key)
        if raw is None:
            return None
        try:
            return AccountSnapshot.model_validate_json(raw)
        except Exception as exc:
            log.error("views.bad_account_snapshot", error=str(exc))
            return None

    def _heartbeats(self, now: datetime | None) -> tuple[dict, ...]:
        if self._transport is None:
            return ()
        monitor = HeartbeatMonitor(self._transport, prefix=self._cfg.heartbeat_prefix)
        out = []
        for source in self._cfg.watch_sources:
            last = monitor.last_beat(source)
            out.append(
                {
                    "source": source,
                    "last_beat": last,
                    "alive": monitor.is_alive(
                        source, max_age_seconds=self._cfg.heartbeat_max_age, now=now
                    ),
                }
            )
        return tuple(out)

    def _quality_summary(self) -> str:
        if self._quality is None:
            return ""
        try:
            return self._quality()
        except Exception as exc:  # 质量报告取不到不能让整个页面 500
            log.error("views.quality_failed", error=str(exc))
            return f"数据质量报告不可用: {exc}"

    def _dead_sources(self, now: datetime | None) -> tuple[str, ...]:
        return tuple(h["source"] for h in self._heartbeats(now) if not h["alive"])

    # --- 视图 ---

    def overview(self, session: date, *, now: datetime | None = None) -> OverviewView:
        curve_df = self._journal.equity(end=session).tail(self._cfg.equity_days)
        curve = curve_df["total_value"].to_list()
        report = build_daily_report(self._journal, session)

        snapshot = self._account()
        positions: tuple[dict, ...] = ()
        cash = report.equity.cash
        if snapshot is not None:
            cash = snapshot.cash
            positions = tuple(
                {
                    "symbol": symbol,
                    "total_qty": p.total_qty,
                    "available_qty": p.available_qty,
                    "avg_cost": p.avg_cost,
                }
                for symbol, p in sorted(snapshot.positions.items())
            )

        heartbeats = self._heartbeats(now)
        healthy = report.is_healthy and all(h["alive"] for h in heartbeats)

        return OverviewView(
            session=session, equity_curve=curve, equity_line=report.equity,
            positions=positions, cash=cash, heartbeats=heartbeats, is_healthy=healthy,
        )

    def trades(self, session: date) -> TradesView:
        return TradesView(
            session=session,
            fills=tuple(self._journal.fills(session).iter_rows(named=True)),
            rejects=tuple(self._journal.rejects(session).iter_rows(named=True)),
        )

    def health(self, session: date, *, now: datetime | None = None) -> HealthView:
        return HealthView(
            session=session,
            reconciliations=tuple(
                self._journal.reconciliations(session).iter_rows(named=True)
            ),
            quality_summary=self._quality_summary(),
            dead_sources=self._dead_sources(now),
        )

    def report(self, session: date, *, now: datetime | None = None) -> DailyReport:
        return build_daily_report(
            self._journal, session,
            quality_summary=self._quality_summary(),
            dead_sources=self._dead_sources(now),
        )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/web/test_views.py -v`
Expected: PASS —— 15 passed

- [ ] **Step 6: 提交**

```bash
git add src/qsys/web/config.py src/qsys/web/views.py tests/web/test_views.py
git commit -m "feat(web): 监控台配置与视图模型"
```

---

## Task 7: FastAPI 应用与模板

**Files:**
- Create: `src/qsys/web/app.py`
- Create: `src/qsys/web/templates/base.html`
- Create: `src/qsys/web/templates/overview.html`
- Create: `src/qsys/web/templates/trades.html`
- Create: `src/qsys/web/templates/health.html`
- Create: `src/qsys/web/templates/report.html`
- Modify: `pyproject.toml`（加 fastapi / uvicorn / jinja2 与 dev 的 httpx）
- Test: `tests/web/test_app.py`

**Interfaces:**
- Consumes: `MonitorService` / `WebConfig`（Task 6）、`line_chart`（Task 5）
- Produces: `create_app(service, config) -> FastAPI`；路由 `GET /`（重定向到最新交易日）、`GET /s/{session}`、`GET /s/{session}/trades`、`GET /s/{session}/health`、`GET /s/{session}/report`、`GET /s/{session}/report.txt`、`GET /healthz`、`GET /fragment/{session}/status`

- [ ] **Step 1: 加依赖**

Add to `pyproject.toml` `dependencies`: `"fastapi>=0.115"`, `"uvicorn>=0.30"`, `"jinja2>=3.1"`.
Add to `dev`: `"httpx>=0.27"`.

Run: `uv sync`
Expected: 安装成功

- [ ] **Step 2: 写失败测试**

Create `tests/web/test_app.py`:

```python
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from qsys.core.events import Reject
from qsys.core.trading import Fill, Side
from qsys.core.types import Market, Symbol
from qsys.live.messages import AccountSnapshot, PositionSnapshot
from qsys.live.transport import MemoryTransport
from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.config import WebConfig
from qsys.web.views import MonitorService

CMB = Symbol("600036", Market.SH)
D0 = date(2024, 1, 2)
D = date(2024, 1, 3)
T = datetime(2024, 1, 3, 9, 30)


@pytest.fixture
def client(warehouse) -> TestClient:
    journal = SessionJournal(warehouse)
    journal.record_equity(D0, cash=100_000.0, market_value=0.0)
    journal.record_equity(D, cash=88_000.0, market_value=13_000.0)
    journal.record_fills(D, [Fill("a", CMB, Side.BUY, 1000, 12.0, 5.1, T)])
    journal.record_rejects(D, [Reject("b", CMB, Side.BUY, 100, "涨停 <买不到>", T)])
    journal.record_reconciliation(D, "intraday", is_consistent=True, at=T)

    transport = MemoryTransport()
    transport.set_key(
        "state:account",
        AccountSnapshot(at=T, cash=88_000.0, positions={
            "600036.SH": PositionSnapshot(total_qty=1000, available_qty=0, avg_cost=12.01)
        }).model_dump_json(),
    )

    cfg = WebConfig()
    service = MonitorService(journal, config=cfg, transport=transport,
                             quality_summary_provider=lambda: "quality: clean")
    return TestClient(create_app(service, cfg))


def test_healthz_is_always_available(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_root_redirects_to_the_latest_session(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "2024-01-03" in r.headers["location"]


def test_overview_page_renders(client):
    r = client.get(f"/s/{D}")
    assert r.status_code == 200
    assert "600036.SH" in r.text
    assert "<svg" in r.text


def test_overview_shows_the_equity_value(client):
    r = client.get(f"/s/{D}")
    assert "101,000.00" in r.text


def test_trades_page_lists_fills_and_rejects(client):
    r = client.get(f"/s/{D}/trades")
    assert r.status_code == 200
    assert "600036.SH" in r.text


def test_reject_reason_is_html_escaped(client):
    """拒单原因可能含 HTML 字符，必须转义。"""
    r = client.get(f"/s/{D}/trades")
    assert "&lt;买不到&gt;" in r.text
    assert "<买不到>" not in r.text


def test_health_page_shows_reconciliation_and_quality(client):
    r = client.get(f"/s/{D}/health")
    assert r.status_code == 200
    assert "intraday" in r.text
    assert "quality: clean" in r.text


def test_report_page_renders(client):
    r = client.get(f"/s/{D}/report")
    assert r.status_code == 200
    assert "每日运行报告" in r.text


def test_report_text_endpoint_is_plain_text(client):
    r = client.get(f"/s/{D}/report.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "每日运行报告" in r.text


def test_status_fragment_is_small_and_pollable(client):
    r = client.get(f"/fragment/{D}/status")
    assert r.status_code == 200
    assert "<html" not in r.text.lower()


def test_unknown_session_returns_404(client):
    assert client.get("/s/2020-01-01/trades").status_code == 404


def test_malformed_session_returns_422(client):
    assert client.get("/s/not-a-date").status_code == 422


def test_pages_have_no_external_resources(client):
    """零外部依赖：页面不得引用任何 CDN。"""
    for path in (f"/s/{D}", f"/s/{D}/trades", f"/s/{D}/health", f"/s/{D}/report"):
        text = client.get(path).text
        assert "cdn." not in text
        assert "//unpkg" not in text
        assert "googleapis" not in text


def test_no_write_endpoints_exist(client):
    """一期 Web 只读——不得存在任何写接口。"""
    app = client.app
    methods = {m for route in app.routes for m in getattr(route, "methods", set())}
    assert methods <= {"GET", "HEAD", "OPTIONS"}


def test_empty_journal_root_shows_a_placeholder(warehouse):
    cfg = WebConfig()
    service = MonitorService(SessionJournal(warehouse), config=cfg)
    c = TestClient(create_app(service, cfg))
    r = c.get("/")
    assert r.status_code == 200
    assert "暂无" in r.text
```

- [ ] **Step 3: 运行测试确认失败**

Run: `uv run pytest tests/web/test_app.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'qsys.web.app'`

- [ ] **Step 4: 创建模板 `src/qsys/web/templates/base.html`**

```html
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<style>
:root { --fg:#0f172a; --muted:#64748b; --line:#e2e8f0; --bg:#f8fafc;
        --ok:#16a34a; --warn:#d97706; --bad:#dc2626; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font-size:14px;
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
                   "Hiragino Sans GB","Microsoft YaHei",sans-serif; }
header { background:#fff; border-bottom:1px solid var(--line); padding:12px 20px;
         display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
header h1 { font-size:16px; margin:0; font-weight:600; }
nav a { color:var(--muted); text-decoration:none; margin-right:14px; }
nav a.active, nav a:hover { color:var(--fg); }
main { padding:20px; max-width:1100px; margin:0 auto; }
.card { background:#fff; border:1px solid var(--line); border-radius:8px;
        padding:16px; margin-bottom:16px; }
.card h2 { font-size:14px; margin:0 0 12px; font-weight:600; color:var(--muted); }
.kpis { display:flex; gap:24px; flex-wrap:wrap; }
.kpi .label { color:var(--muted); font-size:12px; }
.kpi .value { font-size:22px; font-weight:600; font-variant-numeric:tabular-nums; }
table { width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }
th, td { text-align:left; padding:7px 8px; border-bottom:1px solid var(--line); }
th { color:var(--muted); font-weight:500; font-size:12px; }
td.num, th.num { text-align:right; }
.pill { display:inline-block; padding:1px 8px; border-radius:999px; font-size:12px; }
.pill.ok { background:#dcfce7; color:var(--ok); }
.pill.bad { background:#fee2e2; color:var(--bad); }
.pill.warn { background:#fef3c7; color:var(--warn); }
.up { color:var(--ok); } .down { color:var(--bad); }
.empty { color:var(--muted); padding:12px 0; }
pre { background:#0f172a; color:#e2e8f0; padding:14px; border-radius:8px;
      overflow-x:auto; font-size:12px; line-height:1.5; }
</style>
</head>
<body>
<header>
  <h1>{{ title }}</h1>
  {% if session %}
  <nav>
    <a href="/s/{{ session }}" class="{{ 'active' if page == 'overview' }}">总览</a>
    <a href="/s/{{ session }}/trades" class="{{ 'active' if page == 'trades' }}">成交</a>
    <a href="/s/{{ session }}/health" class="{{ 'active' if page == 'health' }}">对账与质量</a>
    <a href="/s/{{ session }}/report" class="{{ 'active' if page == 'report' }}">日报</a>
  </nav>
  <span id="status" style="margin-left:auto"></span>
  {% endif %}
</header>
<main>{% block content %}{% endblock %}</main>
{% if session %}
<script>
// 零外部依赖的自动刷新：每 15 秒拉一次状态片段。
(function () {
  var target = document.getElementById("status");
  if (!target) return;
  var url = "/fragment/{{ session }}/status";
  function tick() {
    fetch(url, { cache: "no-store" })
      .then(function (r) { return r.text(); })
      .then(function (html) { target.innerHTML = html; })
      .catch(function () { target.innerHTML = '<span class="pill bad">监控台失联</span>'; });
  }
  tick();
  setInterval(tick, 15000);
})();
</script>
{% endif %}
</body>
</html>
```

- [ ] **Step 5: 创建其余四个模板**

Create `src/qsys/web/templates/overview.html`:

```html
{% extends "base.html" %}
{% block content %}
<div class="card">
  <h2>净值</h2>
  <div class="kpis">
    <div class="kpi"><div class="label">总值</div>
      <div class="value">{{ "{:,.2f}".format(view.equity_line.total_value) }}</div></div>
    <div class="kpi"><div class="label">当日变动</div>
      <div class="value {{ 'up' if view.equity_line.change_pct >= 0 else 'down' }}">
        {{ "{:+.2%}".format(view.equity_line.change_pct) }}</div></div>
    <div class="kpi"><div class="label">现金</div>
      <div class="value">{{ "{:,.2f}".format(view.cash) }}</div></div>
    <div class="kpi"><div class="label">持仓市值</div>
      <div class="value">{{ "{:,.2f}".format(view.equity_line.market_value) }}</div></div>
  </div>
  <div style="margin-top:14px">{{ chart | safe }}</div>
</div>

<div class="card">
  <h2>持仓</h2>
  {% if view.positions %}
  <table>
    <tr><th>标的</th><th class="num">总量</th><th class="num">可卖</th><th class="num">成本</th></tr>
    {% for p in view.positions %}
    <tr><td>{{ p.symbol }}</td><td class="num">{{ p.total_qty }}</td>
        <td class="num">{{ p.available_qty }}</td>
        <td class="num">{{ "{:.2f}".format(p.avg_cost) }}</td></tr>
    {% endfor %}
  </table>
  {% else %}<div class="empty">暂无持仓快照</div>{% endif %}
</div>

<div class="card">
  <h2>心跳</h2>
  {% if view.heartbeats %}
  <table>
    <tr><th>来源</th><th>状态</th><th>最后心跳</th></tr>
    {% for h in view.heartbeats %}
    <tr><td>{{ h.source }}</td>
        <td><span class="pill {{ 'ok' if h.alive else 'bad' }}">
            {{ '正常' if h.alive else '失联' }}</span></td>
        <td>{{ h.last_beat or '—' }}</td></tr>
    {% endfor %}
  </table>
  {% else %}<div class="empty">未接入心跳</div>{% endif %}
</div>
{% endblock %}
```

Create `src/qsys/web/templates/trades.html`:

```html
{% extends "base.html" %}
{% block content %}
<div class="card">
  <h2>成交（{{ view.fills | length }} 笔）</h2>
  {% if view.fills %}
  <table>
    <tr><th>时间</th><th>标的</th><th>方向</th><th class="num">数量</th>
        <th class="num">价格</th><th class="num">费用</th><th>指令号</th></tr>
    {% for f in view.fills %}
    <tr><td>{{ f.filled_at }}</td><td>{{ f.symbol }}</td><td>{{ f.side }}</td>
        <td class="num">{{ f.qty }}</td>
        <td class="num">{{ "{:.2f}".format(f.price) }}</td>
        <td class="num">{{ "{:.2f}".format(f.fee) }}</td>
        <td>{{ f.client_order_id }}</td></tr>
    {% endfor %}
  </table>
  {% else %}<div class="empty">当日无成交</div>{% endif %}
</div>

<div class="card">
  <h2>拒单（{{ view.rejects | length }} 笔）</h2>
  {% if view.rejects %}
  <table>
    <tr><th>时间</th><th>标的</th><th class="num">数量</th><th>原因</th><th>指令号</th></tr>
    {% for r in view.rejects %}
    <tr><td>{{ r.at }}</td><td>{{ r.symbol }}</td><td class="num">{{ r.qty }}</td>
        <td>{{ r.reason }}</td><td>{{ r.client_order_id }}</td></tr>
    {% endfor %}
  </table>
  {% else %}<div class="empty">当日无拒单</div>{% endif %}
</div>
{% endblock %}
```

Create `src/qsys/web/templates/health.html`:

```html
{% extends "base.html" %}
{% block content %}
<div class="card">
  <h2>对账（{{ view.reconciliations | length }} 次）</h2>
  {% if view.reconciliations %}
  <table>
    <tr><th>时间</th><th>层</th><th>结果</th><th class="num">现金差</th>
        <th class="num">持仓不符</th><th>明细</th></tr>
    {% for r in view.reconciliations %}
    <tr><td>{{ r.at }}</td><td>{{ r.layer }}</td>
        <td><span class="pill {{ 'ok' if r.is_consistent else 'bad' }}">
            {{ '一致' if r.is_consistent else '不一致' }}</span></td>
        <td class="num">{{ "{:,.2f}".format(r.cash_diff) }}</td>
        <td class="num">{{ r.n_mismatches }}</td>
        <td>{{ r.detail }}</td></tr>
    {% endfor %}
  </table>
  {% else %}<div class="empty">当日无对账记录</div>{% endif %}
</div>

<div class="card">
  <h2>数据质量</h2>
  <pre>{{ view.quality_summary or "（未运行）" }}</pre>
</div>

{% if view.dead_sources %}
<div class="card">
  <h2>失联</h2>
  {% for s in view.dead_sources %}<span class="pill bad">{{ s }}</span> {% endfor %}
</div>
{% endif %}
{% endblock %}
```

Create `src/qsys/web/templates/report.html`:

```html
{% extends "base.html" %}
{% block content %}
<div class="card">
  <h2>每日运行报告
    <a href="/s/{{ session }}/report.txt" style="float:right;font-weight:400">纯文本</a>
  </h2>
  <pre>{{ report_text }}</pre>
</div>
{% endblock %}
```

- [ ] **Step 6: 实现 `src/qsys/web/app.py`**

```python
"""只读监控台。

一期 Web 只读：不提供任何写接口（启停策略、改参数属四期）。
Web 层不持有业务逻辑——数据全部来自 MonitorService。

页面零外部依赖：内联 CSS、服务端渲染 SVG、约 20 行原生 JS。
监控页面最需要的性质是「出事的时候一定打得开」。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from qsys.web.config import WebConfig
from qsys.web.svg import line_chart
from qsys.web.views import MonitorService

log = structlog.get_logger(__name__)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(service: MonitorService, config: WebConfig) -> FastAPI:
    app = FastAPI(title=config.title, docs_url=None, redoc_url=None)

    def _require_session(session: date) -> None:
        if not service.has_session(session):
            raise HTTPException(status_code=404, detail=f"no data for {session}")

    def _page(request: Request, template: str, session: date, page: str, **ctx):
        return TEMPLATES.TemplateResponse(
            request=request, name=template,
            context={"title": config.title, "session": session, "page": page, **ctx},
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def root(request: Request):
        latest = service.latest_session()
        if latest is None:
            return HTMLResponse(
                "<!doctype html><meta charset='utf-8'>"
                f"<title>{config.title}</title>"
                "<body style='font-family:sans-serif;padding:40px;color:#475569'>"
                "<h1 style='font-size:18px'>暂无会话数据</h1>"
                "<p>实盘或影子会话尚未写入任何记录。</p></body>"
            )
        return RedirectResponse(f"/s/{latest}")

    @app.get("/s/{session}", response_class=HTMLResponse)
    def overview(request: Request, session: date):
        _require_session(session)
        view = service.overview(session)
        return _page(request, "overview.html", session, "overview",
                     view=view, chart=line_chart(view.equity_curve))

    @app.get("/s/{session}/trades", response_class=HTMLResponse)
    def trades(request: Request, session: date):
        _require_session(session)
        return _page(request, "trades.html", session, "trades",
                     view=service.trades(session))

    @app.get("/s/{session}/health", response_class=HTMLResponse)
    def health(request: Request, session: date):
        _require_session(session)
        return _page(request, "health.html", session, "health",
                     view=service.health(session))

    @app.get("/s/{session}/report", response_class=HTMLResponse)
    def report(request: Request, session: date):
        _require_session(session)
        return _page(request, "report.html", session, "report",
                     report_text=service.report(session).as_text())

    @app.get("/s/{session}/report.txt", response_class=PlainTextResponse)
    def report_text(session: date) -> str:
        _require_session(session)
        return service.report(session).as_text()

    @app.get("/fragment/{session}/status", response_class=HTMLResponse)
    def status_fragment(session: date) -> str:
        _require_session(session)
        view = service.overview(session)
        css = "ok" if view.is_healthy else "bad"
        label = "正常" if view.is_healthy else "异常"
        return f'<span class="pill {css}">{label}</span>'

    return app
```

- [ ] **Step 7: 运行测试确认通过**

Run: `uv run pytest tests/web/test_app.py -v`
Expected: PASS —— 15 passed

- [ ] **Step 8: 提交**

```bash
git add src/qsys/web/app.py src/qsys/web/templates tests/web/test_app.py \
        pyproject.toml uv.lock
git commit -m "feat(web): 只读监控台（零外部依赖）"
```

---

## Task 8: 补上 LiveBroker 的下单前约束检查

**这是计划 1-3 留下的缺口。** `LiveBroker.submit` 只跑了 `RiskGate`，没跑
`ConstraintPipeline`——于是「当日买入当日卖出」这种违反 T+1 的单会畅通无阻地
发到券商，靠券商拒单兜底。spec §5.3 明确要求约束在两处共用：回测撮合前检查
**与实盘风控下单前检查**。

依赖真实券商兜底不可接受：本地不检查意味着你的持仓推演与实际不符，
而对账的前提正是本地推演可信。

**能在下单时刻评估的约束**（依赖账户状态）：`no_short` / `settlement` /
`cash` / `lot_size`。
**不能的**（依赖当时行情）：`price_limit` / `suspension` —— 核心侧下单时未必
持有实时报价，这两条留给券商，其拒单会经 `evt:rejects` 流回来。

**Files:**
- Modify: `src/qsys/markets/ashare.py`（新增 `ashare_pre_trade_pipeline`）
- Modify: `src/qsys/live/live_broker.py`
- Modify: `tests/markets/test_ashare.py`（追加）
- Modify: `tests/live/test_live_broker.py`（追加）

**Interfaces:**
- Consumes: `ConstraintPipeline` / `ConstraintContext`（1-2a）、`LiveBroker`（1-3）
- Produces: `ashare_pre_trade_pipeline(*, fee_buffer=0.001) -> ConstraintPipeline`；
  `LiveBroker(..., constraints=None, constraint_context_factory=None)` —— 两者同时提供时在风控之后施加约束

- [ ] **Step 1: 追加失败测试**

Append to `tests/markets/test_ashare.py`:

```python


# --- 下单时刻可评估的约束子集 ---

from qsys.markets.ashare import ashare_pre_trade_pipeline


def test_pre_trade_pipeline_excludes_market_dependent_rules():
    """涨跌停与停牌依赖当时行情，核心侧下单时未必持有，留给券商。"""
    names = ashare_pre_trade_pipeline().names
    assert "price_limit" not in names
    assert "suspension" not in names


def test_pre_trade_pipeline_keeps_account_dependent_rules():
    assert ashare_pre_trade_pipeline().names == (
        "no_short", "settlement", "cash", "lot_size",
    )


def test_pre_trade_pipeline_still_puts_lot_size_last():
    assert ashare_pre_trade_pipeline().names[-1] == "lot_size"


def test_pre_trade_pipeline_enforces_t_plus_one():
    p = Portfolio(100_000.0)
    p.apply_fill(Fill("o", CMB, Side.BUY, 500, 10.0, 0.0, NOW))
    ctx = ConstraintContext(portfolio=p, reference_price=10.0)
    assert ashare_pre_trade_pipeline().apply(
        OrderIntent(CMB, Side.SELL, 500), ctx
    ).is_rejected
```

Append to `tests/live/test_live_broker.py`:

```python


# --- 下单前约束检查（Task 8 补上的缺口） ---

from qsys.core.constraints import ConstraintContext
from qsys.core.trading import Fill
from qsys.markets.ashare import ashare_pre_trade_pipeline


def make_with_constraints(tmp_path, *, held: int = 0, settled: bool = False,
                          cash: float = 1_000_000.0):
    transport = MemoryTransport()
    ks = KillSwitch(transport, local_path=tmp_path / "K")
    gate = default_ashare_gate(ks, CAL)

    portfolio = Portfolio(cash)
    if held:
        portfolio.apply_fill(Fill("seed", CMB, Side.BUY, held, 10.0, 0.0, T))
        if settled:
            portfolio.unfreeze()

    broker = LiveBroker(
        transport, STREAMS, risk_gate=gate,
        risk_context_factory=lambda: RiskContext(
            portfolio=portfolio, now=T, reference_price=10.0, day_start_value=cash
        ),
        constraints=ashare_pre_trade_pipeline(),
        constraint_context_factory=lambda: ConstraintContext(
            portfolio=portfolio, reference_price=10.0
        ),
    )
    return broker, transport


def test_unsettled_sell_is_blocked_before_the_broker(tmp_path):
    """T+1 违规必须在核心侧被拦下，不得发到券商。"""
    b, t = make_with_constraints(tmp_path, held=500, settled=False)
    b.submit(order(Side.SELL, 500))
    assert t.all_messages(STREAMS.orders) == []
    assert "settled" in b.poll_rejects()[0].reason


def test_settled_sell_passes_through(tmp_path):
    b, t = make_with_constraints(tmp_path, held=500, settled=True)
    b.submit(order(Side.SELL, 500))
    assert len(t.all_messages(STREAMS.orders)) == 1


def test_odd_lot_buy_is_rounded_down_before_sending(tmp_path):
    b, t = make_with_constraints(tmp_path)
    b.submit(order(Side.BUY, 250))
    cmd = OrderCommand.from_payload(t.all_messages(STREAMS.orders)[0].payload)
    assert cmd.qty == 200


def test_short_sell_is_blocked(tmp_path):
    b, t = make_with_constraints(tmp_path, held=0)
    b.submit(order(Side.SELL, 100))
    assert t.all_messages(STREAMS.orders) == []
    assert "no position" in b.poll_rejects()[0].reason


def test_constraints_are_optional(tmp_path):
    """不提供约束时行为与原先一致——不破坏既有调用方。"""
    b, t, _ = make(tmp_path)
    b.submit(order(Side.BUY, 250))
    cmd = OrderCommand.from_payload(t.all_messages(STREAMS.orders)[0].payload)
    assert cmd.qty == 250


def test_risk_gate_runs_before_constraints(tmp_path):
    """kill switch 按下时不该再去算约束。"""
    transport = MemoryTransport()
    ks = KillSwitch(transport, local_path=tmp_path / "K")
    ks.engage("halt")
    portfolio = Portfolio(1_000_000.0)
    b = LiveBroker(
        transport, STREAMS, risk_gate=default_ashare_gate(ks, CAL),
        risk_context_factory=lambda: RiskContext(
            portfolio=portfolio, now=T, reference_price=10.0, day_start_value=1e6
        ),
        constraints=ashare_pre_trade_pipeline(),
        constraint_context_factory=lambda: ConstraintContext(
            portfolio=portfolio, reference_price=10.0
        ),
    )
    b.submit(order(Side.SELL, 100))
    assert "kill_switch" in b.poll_rejects()[0].reason
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/markets/test_ashare.py tests/live/test_live_broker.py -v`
Expected: FAIL —— `ImportError: cannot import name 'ashare_pre_trade_pipeline'`

- [ ] **Step 3: 追加实现到 `src/qsys/markets/ashare.py`**

```python


def ashare_pre_trade_pipeline(*, fee_buffer: float = 0.001) -> ConstraintPipeline:
    """实盘下单时刻可评估的约束子集。

    排除 price_limit 与 suspension —— 它们依赖当时行情，核心侧下单时未必
    持有实时报价。这两条留给券商，其拒单会经回报流回来。

    保留的四条只依赖账户状态，核心侧必须自己检查：
    依赖券商兜底意味着本地持仓推演与实际不符，而对账的前提正是本地推演可信。
    """
    return ConstraintPipeline(
        [
            NoShortConstraint(),
            SettlementConstraint(),
            CashConstraint(fee_buffer=fee_buffer),
            LotSizeConstraint(lot=LOT_SIZE),
        ]
    )
```

- [ ] **Step 4: 修改 `src/qsys/live/live_broker.py`**

在 import 区补上：

```python
from qsys.core.constraints import ConstraintContext, ConstraintPipeline
```

`__init__` 签名追加两个可选参数：

```python
        constraints: ConstraintPipeline | None = None,
        constraint_context_factory: Callable[[], ConstraintContext] | None = None,
```

并在方法体内保存：

```python
        self._constraints = constraints
        self._constraint_ctx = constraint_context_factory
```

把 `submit` 中风控通过之后、写入 orders 流之前的部分改为：

```python
        qty = order.qty
        if self._constraints is not None and self._constraint_ctx is not None:
            result = self._constraints.apply(intent, self._constraint_ctx())
            if result.is_rejected:
                log.warning("live_broker.constrained", client_order_id=order.order_id,
                            symbol=str(order.symbol), **result.as_log_fields())
                self._rejects.append(
                    Reject(order.order_id, order.symbol, order.side, order.qty,
                           result.binding_reason, order.created_at)
                )
                return
            qty = result.allowed_qty

        self._t.append(
            self._s.orders,
            OrderCommand(
                client_order_id=order.order_id,
                symbol=str(order.symbol),
                side=order.side.value,
                qty=qty,
                order_type=order.order_type.value,
                limit_price=order.limit_price,
                created_at=order.created_at,
            ).to_payload(),
        )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/markets/test_ashare.py tests/live/test_live_broker.py -v`
Expected: PASS —— 12 + 20 = 32 passed

- [ ] **Step 6: 确认无回归**

Run: `uv run pytest -q`
Expected: PASS —— 前序全部通过

- [ ] **Step 7: 提交**

```bash
git add src/qsys/markets/ashare.py src/qsys/live/live_broker.py \
        tests/markets/test_ashare.py tests/live/test_live_broker.py
git commit -m "fix(live): LiveBroker 补上下单前约束检查

计划 1-3 的缺口：submit 只跑 RiskGate，未跑 ConstraintPipeline，
T+1 违规单会畅通无阻发到券商。spec §5.3 要求约束在回测撮合与
实盘风控两处共用。依赖券商兜底会让本地持仓推演与实际不符，
而对账的前提正是本地推演可信。"
```

---

## Task 9: 启动脚本与端到端集成

**Files:**
- Create: `scripts/run_monitor.py`
- Test: `tests/test_monitor_e2e.py`

**Interfaces:**
- Consumes: 全部前序组件
- Produces: `scripts/run_monitor.py`（`--host` / `--port` / `--warehouse` / `--config`）

- [ ] **Step 1: 写端到端测试**

Create `tests/test_monitor_e2e.py`:

```python
"""监控台端到端：从实盘链路产生数据，到页面正确呈现。

链路：LiveBroker 下单 -> ExecutionAgent 成交 -> Journal 记录
      -> 对账 -> 监控台展示 -> 日报生成 -> 告警发出
"""

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from qsys.agent.adapter import FakeAdapter
from qsys.agent.execution_agent import ExecutionAgent
from qsys.core.calendar import AShareCalendar
from qsys.core.portfolio import Portfolio
from qsys.core.trading import Order, OrderType, Side
from qsys.core.types import Market, Symbol
from qsys.live.config import StreamConfig
from qsys.live.kill_switch import KillSwitch
from qsys.live.live_broker import LiveBroker
from qsys.live.messages import AccountSnapshot
from qsys.live.reconcile import reconcile_positions
from qsys.core.constraints import ConstraintContext
from qsys.live.risk import RiskContext, default_ashare_gate
from qsys.live.transport import MemoryTransport
from qsys.markets.ashare import ashare_pre_trade_pipeline
from qsys.ops.alerts import AlertBus, RecordingAlertSink, Severity
from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.config import WebConfig
from qsys.web.views import MonitorService

CMB = Symbol("600036", Market.SH)
D = date(2024, 1, 3)
CAL = AShareCalendar([D])
T = datetime(2024, 1, 3, 10, 0)
STREAMS = StreamConfig()


@pytest.fixture
def rig(warehouse, tmp_path):
    transport = MemoryTransport()
    portfolio = Portfolio(1_000_000.0)
    journal = SessionJournal(warehouse)
    sink = RecordingAlertSink()
    bus = AlertBus([sink])

    ks = KillSwitch(transport, local_path=tmp_path / "KILL")
    gate = default_ashare_gate(ks, CAL)
    broker = LiveBroker(
        transport, STREAMS, risk_gate=gate,
        risk_context_factory=lambda: RiskContext(
            portfolio=portfolio, now=T, reference_price=10.0, day_start_value=1_000_000.0
        ),
        constraints=ashare_pre_trade_pipeline(),
        constraint_context_factory=lambda: ConstraintContext(
            portfolio=portfolio, reference_price=10.0
        ),
    )
    agent = ExecutionAgent(transport, FakeAdapter(cash=1_000_000.0), STREAMS)
    return broker, agent, transport, portfolio, journal, bus, sink


def run_session(rig) -> None:
    broker, agent, transport, portfolio, journal, bus, _ = rig

    broker.submit(Order("o1", CMB, Side.BUY, 1000, OrderType.MARKET, None, T))
    broker.submit(Order("o2", CMB, Side.SELL, 500, OrderType.MARKET, None, T))
    agent.run_once()

    fills = broker.poll_fills()
    rejects = broker.poll_rejects()
    for f in fills:
        portfolio.apply_fill(f)

    journal.record_fills(D, fills)
    journal.record_rejects(D, rejects)

    agent.publish_account()
    snapshot = broker.account_snapshot()
    result = reconcile_positions(portfolio, snapshot, cash_tolerance=1e9)
    journal.record_reconciliation(
        D, "intraday", is_consistent=result.is_consistent,
        cash_diff=result.cash_diff, n_mismatches=len(result.mismatches),
        detail=result.summary(), at=T,
    )
    if not result.is_consistent:
        bus.critical("持仓不一致", result.summary())

    journal.record_equity(D, cash=portfolio.cash, market_value=0.0)


def client_for(rig) -> TestClient:
    _, _, transport, _, journal, _, _ = rig
    cfg = WebConfig()
    service = MonitorService(journal, config=cfg, transport=transport,
                             quality_summary_provider=lambda: "quality: clean")
    return TestClient(create_app(service, cfg))


def test_session_data_reaches_the_overview_page(rig):
    run_session(rig)
    r = client_for(rig).get(f"/s/{D}")
    assert r.status_code == 200
    assert "600036.SH" in r.text


def test_fills_appear_on_the_trades_page(rig):
    run_session(rig)
    r = client_for(rig).get(f"/s/{D}/trades")
    assert "o1" in r.text


def test_settlement_violation_is_blocked_before_reaching_the_broker(rig):
    """T+1：当日买入不可卖，卖单必须在核心侧被拦下，不得发到券商。

    这条断言的是 Task 9 补上的约束检查。若 LiveBroker 只跑 RiskGate
    而不跑 ConstraintPipeline，这一单会畅通无阻地发给券商。
    """
    run_session(rig)
    r = client_for(rig).get(f"/s/{D}/trades")
    assert "o2" in r.text
    assert "settlement" in r.text or "settled" in r.text


def test_reconciliation_result_reaches_the_health_page(rig):
    run_session(rig)
    r = client_for(rig).get(f"/s/{D}/health")
    assert "intraday" in r.text


def test_position_mismatch_raises_a_critical_alert(rig):
    """FakeAdapter 不跟踪持仓，本地有仓远端无仓 -> 必须告警。"""
    run_session(rig)
    _, _, _, _, _, _, sink = rig
    assert any(a.severity is Severity.CRITICAL for a in sink.alerts)


def test_daily_report_text_is_available(rig):
    run_session(rig)
    r = client_for(rig).get(f"/s/{D}/report.txt")
    assert r.status_code == 200
    assert "每日运行报告" in r.text
    assert "对账" in r.text


def test_status_fragment_reflects_the_problem(rig):
    run_session(rig)
    r = client_for(rig).get(f"/fragment/{D}/status")
    assert "异常" in r.text


def test_whole_site_has_no_external_resources(rig):
    run_session(rig)
    c = client_for(rig)
    for path in (f"/s/{D}", f"/s/{D}/trades", f"/s/{D}/health", f"/s/{D}/report"):
        text = c.get(path).text
        assert "cdn." not in text and "googleapis" not in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_monitor_e2e.py -v`
Expected: FAIL —— 前序模块若有缺漏会在此暴露

- [ ] **Step 3: 实现 `scripts/run_monitor.py`**

```python
"""监控台启动入口。

默认只监听 127.0.0.1 —— 通过 Tailscale / WireGuard 私有网络访问。
**绝不要绑到 0.0.0.0 并开放安全组**：这个页面能看到你的全部持仓。

用法：
  uv run python scripts/run_monitor.py --warehouse warehouse
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import structlog
import uvicorn

from qsys.data.storage import Warehouse
from qsys.ops.journal import SessionJournal
from qsys.web.app import create_app
from qsys.web.config import WebConfig
from qsys.web.views import MonitorService

log = structlog.get_logger("monitor")


def build_app(warehouse_root: Path, config: WebConfig, config_path: str | None):
    journal = SessionJournal(Warehouse(warehouse_root))

    transport = None
    try:
        from qsys.live.config import load_deploy_config
        from qsys.live.redis_transport import RedisTransport

        transport = RedisTransport.from_config(load_deploy_config(config_path))
    except Exception as exc:
        # Redis 不可用时监控台仍应能打开——历史数据来自仓库，不依赖 Redis
        log.warning("monitor.redis_unavailable", error=str(exc))

    service = MonitorService(journal, config=config, transport=transport)
    return create_app(service, config)


def main() -> int:
    parser = argparse.ArgumentParser(description="只读监控台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--warehouse", default="warehouse")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    if args.host == "0.0.0.0":  # noqa: S104 - 刻意检查
        log.warning(
            "monitor.public_bind",
            hint="监控台可看到全部持仓，应通过 Tailscale/WireGuard 私有网络访问",
        )

    config = WebConfig(host=args.host, port=args.port)
    app = build_app(Path(args.warehouse), config, args.config)
    log.info("monitor.started", host=args.host, port=args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_monitor_e2e.py -v`
Expected: PASS —— 8 passed

- [ ] **Step 5: 确认启动脚本可用**

Run: `uv run python scripts/run_monitor.py --help`
Expected: 打印用法，退出码 0

- [ ] **Step 6: 运行全部测试**

Run: `uv run pytest -q`
Expected: PASS —— 前四个计划（542）+ 本计划新增项全部通过

- [ ] **Step 7: 提交**

```bash
git add scripts/run_monitor.py tests/test_monitor_e2e.py
git commit -m "feat(web): 监控台启动入口与端到端集成"
```

---

## 完成标准

1. `uv run pytest` 全绿，前四个计划（542 项）无回归
2. **页面零外部依赖** —— `test_pages_have_no_external_resources` 与 `test_whole_site_has_no_external_resources` 通过；页面不引用任何 CDN
3. **Web 只读** —— `test_no_write_endpoints_exist` 通过：全站路由方法集合 ⊆ {GET, HEAD, OPTIONS}
4. **默认只监听 127.0.0.1** —— `WebConfig.host` 默认值为 `"127.0.0.1"`；`run_monitor.py` 在 `--host 0.0.0.0` 时打印告警
5. **用户可见文本自动转义** —— `test_reject_reason_is_html_escaped` 通过（`<买不到>` 渲染为 `&lt;买不到&gt;`）
6. **告警旁路不阻塞主流程** —— 一个 sink 抛异常时其它 sink 仍收到告警
7. **Redis 不可用时监控台仍可打开** —— `build_app` 捕获连接失败并降级为无心跳/无持仓快照
8. 日报纯文本接口 `GET /s/{session}/report.txt` 返回 `text/plain`，可直接推送
9. `scripts/run_monitor.py --help` 可用
10. **LiveBroker 在下单前施加账户类约束** —— T+1 违规单在核心侧被拦下，不发到券商

## 一期收官检查（本计划完成后执行）

一期四个计划全部完成后，spec §10 的六条一期验收标准即可逐条核对：

1. 合成数据断言套件全绿 —— 计划 1-2b Task 11
2. 聚宽交叉验证第二层残差可逐项解释 —— **需你导出数据后运行 `scripts/compare_joinquant.py`**
3. 未来函数专项测试通过 —— 计划 1-1 Task 14 + 1-2b Task 9
4. 影子模式连续跑满一周无中断、无告警 —— **需实际运行**
5. 三层对账连续一周零不一致 —— **需实际运行**
6. 用小资金完成至少一轮真实开仓平仓 —— **需实际运行**

前三条是代码层面的，已具备。后三条需要真实运行时间。

## 下一步

一期代码收官。进入**影子模式一周**的验收：

1. 在云 Linux 上部署核心与监控台，云 Windows 上部署执行代理
2. 复制 `config/deploy.example.toml` 为 `deploy.local.toml` 并填入真实地址
3. 配置 Redis TLS + 强密码 + 安全组白名单
4. 用 `ShadowBroker` 跑满一周，每日核对监控台与日报
5. 期间完成聚宽交叉验证（需浏览器导出数据）
6. 全部通过后换 `LiveBroker`，用小资金完成一轮真实开仓平仓

**接真实券商前必须处理**：计划 1-3 已知待办——`ExecutionAgent` 的 `FillEvent` 目前是同步发布的假成交价，必须改为等 xtquant 异步回调。
