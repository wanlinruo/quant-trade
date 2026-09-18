# 策略运行日志 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 影子盘与模拟盘的每个策略事件都留下一条可读记录（空仓、无候选、提前返回、报错同样有），并在各自页面按交易日回看。

**Architecture:** 新增 `qsys.ops.strategy_log`，内含一个可变累加器 `RunReport`（策略通过 `ctx.report(...)` 上报漏斗）与不可变结果 `RunRecord`（运行时补齐账户快照与下单数后合成 `summary`）。两个运行时在现有 `commit_event` 旁写入各自 sqlite 的新表 `live_strategy_runs` / `paper_strategy_runs`；写入包在 try/except 内，失败只打日志。Web 层把两种存储读成同一结构，两个页面共用一个模板片段。

**Tech Stack:** Python 3.12、sqlite3（标准库）、FastAPI + Jinja2、structlog、pytest、polars（仅页面数值格式化不涉及）。

**Spec:** `docs/superpowers/specs/2026-09-18-strategy-run-log-design.md`

## Global Constraints

- 记录失败绝不影响交易：所有记录调用包在 try/except 内，失败只 `log.warning`，不得向上抛。
- 不得引入推断字段：`report` 只接收策略给出的数字与原因文本，运行时不猜原因。
- `candidates`/`passed` 为 `None` 表示「该策略没有筛选漏斗」，为 `0` 表示「筛过，候选池为空」；两者页面文案不同，不得合并。
- 幂等：主键为事件键（模拟盘为 `account_id` + `event_key`），同一事件重跑覆盖同一行。
- 所有面向用户的字符串用中文；代码注释解释「为什么」，不重复「做什么」。
- 提交信息用 Conventional Commits，一次提交只做一类事情。
- 测试命令统一 `uv run pytest`（仓库根目录）。

---

### Task 1: 记录累加器与结论合成

**Files:**
- Create: `src/qsys/ops/strategy_log.py`
- Test: `tests/ops/test_strategy_log.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `RunReport()`：`.report(candidates: int | None = None, passed: int | None = None, reasons: Mapping[str, int] | None = None, note: str | None = None) -> None`，字段后写覆盖、`reasons` 按 key 累加；只读属性 `candidates`、`passed`、`reasons: dict[str, int]`、`note: str`
  - `RunRecord`（frozen dataclass）：字段 `event_key: str`、`session: date`、`phase: str`、`started_at: datetime`、`finished_at: datetime`、`duration_ms: int`、`cash: float`、`market_value: float`、`total_value: float`、`position_count: int`、`candidates: int | None`、`passed: int | None`、`orders: int`、`rejects: int`、`summary: str`、`reasons: dict[str, int]`、`note: str`、`error: str`
  - `build_record(report: RunReport, *, event_key: str, session: date, phase: str, started_at: datetime, finished_at: datetime, cash: float, market_value: float, position_count: int, orders: int, rejects: int, error: str = "") -> RunRecord`
  - `PHASE_LABELS: dict[str, str]`，把 `session_start`/`auction`/`checkpoint`/`bar`/`session_end` 映射成「日初结算」/「竞价选股」/「检查点」/「收盘撮合」/「收盘对账」

- [ ] **Step 1: 写失败的测试**

```python
"""策略运行记录：空仓也要说得清卡在哪，报错也要留痕。"""

from datetime import date, datetime

from qsys.ops.strategy_log import PHASE_LABELS, RunReport, build_record

DAY = date(2026, 9, 18)
T0 = datetime(2026, 9, 18, 9, 30, 0)
T1 = datetime(2026, 9, 18, 9, 30, 1, 800000)


def _record(report, **kwargs):
    fields = dict(
        event_key=f"{DAY}:auction:09:30:00", session=DAY, phase="auction",
        started_at=T0, finished_at=T1, cash=50_000.0, market_value=0.0,
        position_count=0, orders=0, rejects=0,
    )
    fields.update(kwargs)
    return build_record(report, **fields)


def test_report_merges_fields_and_accumulates_reasons():
    report = RunReport()
    report.report(candidates=37, reasons={"高开超过阈值": 12})
    report.report(passed=2, reasons={"高开超过阈值": 3, "竞价量不足": 5})
    assert report.candidates == 37 and report.passed == 2
    assert report.reasons == {"高开超过阈值": 15, "竞价量不足": 5}


def test_summary_shows_the_funnel_and_the_orders():
    report = RunReport()
    report.report(candidates=37, passed=2)
    record = _record(report, orders=2)
    assert record.summary == "候选 37 只 → 通过 2 只 → 下单 2 笔"
    assert record.duration_ms == 1800
    assert record.total_value == 50_000.0


def test_summary_says_why_nothing_was_bought():
    passed_none = _record(_funnel(candidates=37, passed=0))
    assert passed_none.summary == "候选 37 只 → 通过 0 只，未下单"
    empty_pool = _record(_funnel(candidates=0, passed=0))
    assert empty_pool.summary == "候选池为空，未下单"


def _funnel(**kwargs):
    report = RunReport()
    report.report(**kwargs)
    return report


def test_strategy_without_a_funnel_is_not_reported_as_zero():
    record = _record(RunReport(), phase="checkpoint", position_count=2,
                     market_value=19_880.0)
    assert record.candidates is None and record.passed is None
    assert record.summary == "持仓 2 只，无下单"
    assert record.total_value == 69_880.0


def test_note_replaces_the_funnel_when_the_strategy_returned_early():
    record = _record(_funnel(note="没有上一交易日，无从选股"))
    assert record.summary == "没有上一交易日，无从选股"
    assert record.note == "没有上一交易日，无从选股"


def test_error_wins_over_everything_else():
    record = _record(_funnel(candidates=37, passed=2), orders=2,
                     error="券商账户快照已过期: 19950.0s")
    assert record.summary == "事件失败：券商账户快照已过期: 19950.0s"


def test_phase_labels_cover_every_phase():
    assert PHASE_LABELS == {
        "session_start": "日初结算", "auction": "竞价选股", "checkpoint": "检查点",
        "bar": "收盘撮合", "session_end": "收盘对账",
    }
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/ops/test_strategy_log.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'qsys.ops.strategy_log'`

- [ ] **Step 3: 写最小实现**

```python
"""策略运行记录：一个事件一条，说清跑了什么、为什么没动作。

2026-09-18 的由来：两个运行时只在事件表里留「完成 / 已过期」，页面上看不出
策略当天扫了多少票、卡在哪个条件上；空仓时更是完全无从判断。

`RunReport` 由策略通过 `ctx.report(...)` 填写；运行时事件结束时补齐账户快照与
下单数，`build_record` 合成一行中文结论。这里只做转述，不做任何推断。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

PHASE_LABELS = {
    "session_start": "日初结算",
    "auction": "竞价选股",
    "checkpoint": "检查点",
    "bar": "收盘撮合",
    "session_end": "收盘对账",
}


class RunReport:
    """策略在一个事件里的上报。字段后写覆盖，原因按 key 累加。"""

    def __init__(self) -> None:
        self._candidates: int | None = None
        self._passed: int | None = None
        self._reasons: dict[str, int] = {}
        self._note: str = ""

    def report(
        self,
        candidates: int | None = None,
        passed: int | None = None,
        reasons: Mapping[str, int] | None = None,
        note: str | None = None,
    ) -> None:
        if candidates is not None:
            self._candidates = int(candidates)
        if passed is not None:
            self._passed = int(passed)
        for key, count in (reasons or {}).items():
            self._reasons[key] = self._reasons.get(key, 0) + int(count)
        if note is not None:
            self._note = note

    @property
    def candidates(self) -> int | None:
        return self._candidates

    @property
    def passed(self) -> int | None:
        return self._passed

    @property
    def reasons(self) -> dict[str, int]:
        return dict(self._reasons)

    @property
    def note(self) -> str:
        return self._note


@dataclass(frozen=True, slots=True)
class RunRecord:
    event_key: str
    session: date
    phase: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    cash: float
    market_value: float
    total_value: float
    position_count: int
    candidates: int | None
    passed: int | None
    orders: int
    rejects: int
    summary: str
    reasons: dict[str, int] = field(default_factory=dict)
    note: str = ""
    error: str = ""


def _summarize(
    report: RunReport, *, orders: int, position_count: int, error: str
) -> str:
    if error:
        return f"事件失败：{error}"
    if report.note:
        return report.note
    if report.candidates is None:
        # 策略没有筛选漏斗（如 ma_cross）：只说它看到的账户与动作，不编造候选数
        if orders:
            return f"持仓 {position_count} 只，下单 {orders} 笔"
        return f"持仓 {position_count} 只，无下单"
    if report.candidates == 0:
        return "候选池为空，未下单"
    funnel = f"候选 {report.candidates} 只 → 通过 {report.passed or 0} 只"
    return f"{funnel} → 下单 {orders} 笔" if orders else f"{funnel}，未下单"


def build_record(
    report: RunReport,
    *,
    event_key: str,
    session: date,
    phase: str,
    started_at: datetime,
    finished_at: datetime,
    cash: float,
    market_value: float,
    position_count: int,
    orders: int,
    rejects: int,
    error: str = "",
) -> RunRecord:
    return RunRecord(
        event_key=event_key, session=session, phase=phase,
        started_at=started_at, finished_at=finished_at,
        duration_ms=int((finished_at - started_at).total_seconds() * 1000),
        cash=cash, market_value=market_value, total_value=cash + market_value,
        position_count=position_count,
        candidates=report.candidates, passed=report.passed,
        orders=orders, rejects=rejects,
        summary=_summarize(report, orders=orders, position_count=position_count,
                           error=error),
        reasons=report.reasons, note=report.note, error=error,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/ops/test_strategy_log.py -v`
Expected: PASS（7 个用例）

- [ ] **Step 5: 提交**

```bash
git add src/qsys/ops/strategy_log.py tests/ops/test_strategy_log.py
git commit -m "feat(ops): accumulate and summarise one strategy run record per event"
```

---

### Task 2: 两个状态库的读写

**Files:**
- Modify: `src/qsys/live/store.py`（在 `_connect` 建表处加表，并加两个方法）
- Modify: `src/qsys/paper/store.py`（同上，多一列 `account_id`）
- Test: `tests/live/test_live_store.py`、`tests/paper/test_paper_store.py`

**Interfaces:**
- Consumes: Task 1 的 `RunRecord`
- Produces:
  - `LiveStore.record_run(record: RunRecord) -> None`；`LiveStore.strategy_runs(session: date) -> list[dict]`（按 `started_at` 升序，`reasons` 已由 JSON 解析成 dict）
  - `LiveStore.run_sessions(limit: int = 60) -> list[date]`（有记录的交易日，倒序）
  - `PaperStore.record_run(ident: str, record: RunRecord) -> None`；`PaperStore.strategy_runs(ident: str, session: date) -> list[dict]`；`PaperStore.run_sessions(ident: str, limit: int = 60) -> list[date]`

- [ ] **Step 1: 写失败的测试（影子盘）**

在 `tests/live/test_live_store.py` 末尾追加：

```python
def test_strategy_runs_round_trip_and_rerun_overwrites(tmp_path):
    from datetime import date, datetime

    from qsys.ops.strategy_log import RunReport, build_record
    from qsys.live.store import LiveStore

    store = LiveStore(tmp_path / "state.sqlite3")
    day = date(2026, 9, 18)
    report = RunReport()
    report.report(candidates=37, passed=2, reasons={"高开超过阈值": 12})
    base = dict(
        event_key=f"{day}:auction:09:30:00", session=day, phase="auction",
        started_at=datetime(2026, 9, 18, 9, 30), cash=50_000.0,
        market_value=0.0, position_count=0, rejects=0,
    )
    store.record_run(build_record(
        report, finished_at=datetime(2026, 9, 18, 9, 30, 1), orders=2, **base))

    rows = store.strategy_runs(day)
    assert len(rows) == 1
    assert rows[0]["summary"] == "候选 37 只 → 通过 2 只 → 下单 2 笔"
    assert rows[0]["reasons"] == {"高开超过阈值": 12}
    assert rows[0]["candidates"] == 37 and rows[0]["phase"] == "auction"

    # 同一事件重跑：覆盖同一行，不追加
    store.record_run(build_record(
        RunReport(), finished_at=datetime(2026, 9, 18, 9, 30, 2), orders=0, **base))
    rows = store.strategy_runs(day)
    assert len(rows) == 1 and rows[0]["orders"] == 0
    assert rows[0]["candidates"] is None          # 无漏斗与 0 只必须区分
    assert store.run_sessions() == [day]
    assert store.strategy_runs(date(2026, 9, 17)) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/live/test_live_store.py -k strategy_runs -v`
Expected: FAIL，`AttributeError: 'LiveStore' object has no attribute 'record_run'`

- [ ] **Step 3: 实现影子盘存储**

在 `src/qsys/live/store.py` 建表的 `executescript` 里追加（与 `live_events` 同一处）：

```sql
                CREATE TABLE IF NOT EXISTS live_strategy_runs (
                    event_key TEXT PRIMARY KEY, session TEXT NOT NULL,
                    phase TEXT NOT NULL, started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL, duration_ms INTEGER NOT NULL,
                    cash REAL NOT NULL, market_value REAL NOT NULL,
                    total_value REAL NOT NULL, position_count INTEGER NOT NULL,
                    candidates INTEGER, passed INTEGER,
                    orders INTEGER NOT NULL, rejects INTEGER NOT NULL,
                    summary TEXT NOT NULL, reasons_json TEXT NOT NULL DEFAULT '{}',
                    note TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS live_runs_session
                    ON live_strategy_runs(session, started_at);
```

并加方法（`json` 已在该模块导入）：

```python
    def record_run(self, record: RunRecord) -> None:
        with self._connect() as con:
            con.execute(
                f"INSERT OR REPLACE INTO live_strategy_runs "
                f"({','.join(RUN_COLUMNS)}) "
                f"VALUES ({','.join('?' * len(RUN_COLUMNS))})",
                run_values(record),
            )

    def strategy_runs(self, session: date) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM live_strategy_runs WHERE session=? ORDER BY started_at",
                (session.isoformat(),),
            ).fetchall()
        return [decode_run(row) for row in rows]

    def run_sessions(self, limit: int = 60) -> list[date]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT DISTINCT session FROM live_strategy_runs "
                "ORDER BY session DESC LIMIT ?", (limit,),
            ).fetchall()
        return [date.fromisoformat(row["session"]) for row in rows]
```

`src/qsys/ops/strategy_log.py` 再加一个列顺序常量与两个辅助函数（两个 store 共用，
避免两份列顺序漂移）：

```python
RUN_COLUMNS: tuple[str, ...] = (
    "event_key", "session", "phase", "started_at", "finished_at", "duration_ms",
    "cash", "market_value", "total_value", "position_count", "candidates",
    "passed", "orders", "rejects", "summary", "reasons_json", "note", "error",
)
```

以及：

```python
def run_values(record: RunRecord) -> tuple:
    """按 `RUN_COLUMNS` 的顺序展开，reasons 编成 JSON。"""
    return (
        record.event_key, record.session.isoformat(), record.phase,
        record.started_at.isoformat(timespec="seconds"),
        record.finished_at.isoformat(timespec="seconds"), record.duration_ms,
        record.cash, record.market_value, record.total_value, record.position_count,
        record.candidates, record.passed, record.orders, record.rejects,
        record.summary, json.dumps(record.reasons, ensure_ascii=False),
        record.note, record.error,
    )


def decode_run(row) -> dict:
    """sqlite 行 → 页面用的 dict；reasons 解析回 dict。"""
    decoded = dict(row)
    decoded["reasons"] = json.loads(decoded.pop("reasons_json") or "{}")
    return decoded
```

`strategy_log.py` 顶部加 `import json`；`live/store.py` 顶部加
`from qsys.ops.strategy_log import RUN_COLUMNS, RunRecord, decode_run, run_values`。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/live/test_live_store.py -k strategy_runs -v`
Expected: PASS

- [ ] **Step 5: 写失败的测试（模拟盘）**

在 `tests/paper/test_paper_store.py` 末尾追加：

```python
def test_strategy_runs_are_per_account(tmp_path):
    from datetime import date, datetime

    from qsys.ops.strategy_log import RunReport, build_record
    from qsys.paper.store import PaperStore

    store = PaperStore(tmp_path / "workbench.sqlite3")
    day = date(2026, 9, 18)
    report = RunReport()
    report.report(candidates=12, passed=0, reasons={"估值不达标": 12})
    record = build_record(
        report, event_key=f"{day}:auction:09:30:00", session=day, phase="auction",
        started_at=datetime(2026, 9, 18, 9, 30),
        finished_at=datetime(2026, 9, 18, 9, 30, 1), cash=100_000.0,
        market_value=0.0, position_count=0, orders=0, rejects=0,
    )
    store.record_run("acct-a", record)

    rows = store.strategy_runs("acct-a", day)
    assert len(rows) == 1 and rows[0]["summary"] == "候选 12 只 → 通过 0 只，未下单"
    assert rows[0]["reasons"] == {"估值不达标": 12}
    assert store.strategy_runs("acct-b", day) == []      # 账户之间不串
    assert store.run_sessions("acct-a") == [day]
```

- [ ] **Step 6: 运行测试确认失败**

Run: `uv run pytest tests/paper/test_paper_store.py -k strategy_runs -v`
Expected: FAIL，`AttributeError: 'PaperStore' object has no attribute 'record_run'`

- [ ] **Step 7: 实现模拟盘存储**

`src/qsys/paper/store.py` 建表处追加（注意主键含 `account_id`）：

```sql
                CREATE TABLE IF NOT EXISTS paper_strategy_runs (
                    account_id TEXT NOT NULL, event_key TEXT NOT NULL,
                    session TEXT NOT NULL, phase TEXT NOT NULL,
                    started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL, cash REAL NOT NULL,
                    market_value REAL NOT NULL, total_value REAL NOT NULL,
                    position_count INTEGER NOT NULL, candidates INTEGER,
                    passed INTEGER, orders INTEGER NOT NULL, rejects INTEGER NOT NULL,
                    summary TEXT NOT NULL, reasons_json TEXT NOT NULL DEFAULT '{}',
                    note TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(account_id, event_key)
                );
                CREATE INDEX IF NOT EXISTS paper_runs_session
                    ON paper_strategy_runs(account_id, session, started_at);
```

方法与影子盘同构，SQL 多一个 `account_id` 参数：

```python
    def record_run(self, ident: str, record: RunRecord) -> None:
        columns = ("account_id", *RUN_COLUMNS)
        with self._connect() as con:
            con.execute(
                f"INSERT OR REPLACE INTO paper_strategy_runs "
                f"({','.join(columns)}) VALUES ({','.join('?' * len(columns))})",
                (ident, *run_values(record)),
            )

    def strategy_runs(self, ident: str, session: date) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM paper_strategy_runs WHERE account_id=? AND session=? "
                "ORDER BY started_at", (ident, session.isoformat()),
            ).fetchall()
        return [decode_run(row) for row in rows]

    def run_sessions(self, ident: str, limit: int = 60) -> list[date]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT DISTINCT session FROM paper_strategy_runs WHERE account_id=? "
                "ORDER BY session DESC LIMIT ?", (ident, limit),
            ).fetchall()
        return [date.fromisoformat(row["session"]) for row in rows]
```

`paper/store.py` 顶部同样导入 `RUN_COLUMNS, RunRecord, decode_run, run_values`；
`decode_run` 对模拟盘的行会一并带出 `account_id`，页面不使用该列但不必特殊处理。

- [ ] **Step 8: 运行测试确认通过**

Run: `uv run pytest tests/live/test_live_store.py tests/paper/test_paper_store.py -k strategy_runs -v`
Expected: PASS（2 个用例）

- [ ] **Step 9: 提交**

```bash
git add src/qsys/live/store.py src/qsys/paper/store.py src/qsys/ops/strategy_log.py \
        tests/live/test_live_store.py tests/paper/test_paper_store.py
git commit -m "feat(live,paper): persist one strategy run row per event"
```

---

### Task 3: 策略上报接口

**Files:**
- Modify: `src/qsys/strategy/context.py:78-101`（构造函数与属性）
- Test: `tests/strategy/test_context.py`

**Interfaces:**
- Consumes: Task 1 的 `RunReport`
- Produces: `Context(..., report=None)` 新增关键字参数；`ctx.report(candidates=..., passed=..., reasons=..., note=...)` 可调用；未注入时也可调用（落在一个丢弃式 `RunReport` 上，回测与单测不必改）

- [ ] **Step 1: 写失败的测试**

在 `tests/strategy/test_context.py` 末尾追加（若该文件不存在则新建，并照抄本文件已有 fixture 的构造方式）：

```python
def test_report_reaches_the_injected_run_report():
    from qsys.ops.strategy_log import RunReport

    report = RunReport()
    ctx = _context(report=report)          # 本文件已有的 Context 构造辅助
    ctx.report(candidates=37, passed=2, reasons={"高开超过阈值": 12})
    assert report.candidates == 37 and report.passed == 2
    assert report.reasons == {"高开超过阈值": 12}


def test_report_without_injection_is_a_no_op():
    ctx = _context()
    ctx.report(note="回测与单测里没有记录器，也不能崩")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/strategy/test_context.py -k report -v`
Expected: FAIL，`TypeError: Context.__init__() got an unexpected keyword argument 'report'`

- [ ] **Step 3: 写最小实现**

`src/qsys/strategy/context.py`：构造函数签名加 `report: RunReport | None = None`，函数体加
`self._report = report if report is not None else RunReport()`，并新增方法：

```python
    def report(
        self,
        candidates: int | None = None,
        passed: int | None = None,
        reasons: Mapping[str, int] | None = None,
        note: str | None = None,
    ) -> None:
        """上报本次事件的筛选漏斗与不下单的原因，供运行日志展示。

        不影响任何交易判定；没有注入记录器时（回测、单测）落在丢弃式对象上。
        """
        self._report.report(candidates=candidates, passed=passed,
                            reasons=reasons, note=note)
```

顶部加 `from collections.abc import Mapping` 与 `from qsys.ops.strategy_log import RunReport`。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/strategy -v`
Expected: PASS（含原有用例）

- [ ] **Step 5: 提交**

```bash
git add src/qsys/strategy/context.py tests/strategy/test_context.py
git commit -m "feat(strategy): let strategies report their funnel through ctx.report"
```

---

### Task 4: 影子盘运行时写入

**Files:**
- Modify: `src/qsys/live/runtime.py:252-266`（`_context` 接受并传入 report）、`:303-395`（`_run_event` 写记录）、`:276-301`（`tick` 里「已过期」分支也要写）
- Test: `tests/live/test_live_runtime.py`

**Interfaces:**
- Consumes: Task 1 `RunReport`/`build_record`，Task 2 `LiveStore.record_run`，Task 3 `Context(report=...)`
- Produces: 每个事件结束后 `live_strategy_runs` 多一行；失败与过期同样有行

- [ ] **Step 1: 写失败的测试**

在 `tests/live/test_live_runtime.py` 末尾追加（构造方式照抄本文件
`test_live_runtime_executes_event_once_and_recovers`，它是内联构造，没有夹具）：

```python
def _runtime_with_store(warehouse, tmp_path):
    strategy = tmp_path / "strategy.py"
    strategy.write_text(SOURCE, encoding="utf-8")
    streams = StreamConfig()
    transport = MemoryTransport()
    now = datetime.now()
    transport.set_key(streams.account, AccountSnapshot(
        at=now, cash=100_000.0, total_asset=100_000.0, positions={}
    ).model_dump_json())
    for source in ("agent", "market"):
        transport.set_key(
            f"{streams.heartbeat}:{source}",
            Heartbeat(source=source, at=now).model_dump_json(),
        )
    store = LiveStore(tmp_path / "live.sqlite3")
    runtime = LiveRuntime(
        strategy, warehouse.root, AShareCalendar([date.today()]), Quotes(),
        transport, streams, store, local_kill_path=tmp_path / "K",
    )
    return runtime, store


def test_every_event_leaves_a_run_row_even_with_no_orders(warehouse, tmp_path):
    runtime, store = _runtime_with_store(warehouse, tmp_path)
    moment = datetime.combine(date.today(), time(9, 15, 10))

    runtime.tick(moment)

    rows = store.strategy_runs(moment.date())
    assert [row["phase"] for row in rows] == ["session_start"]
    assert rows[0]["orders"] == 0 and rows[0]["summary"]
    assert rows[0]["duration_ms"] >= 0


def test_a_failing_recorder_never_breaks_the_event(warehouse, tmp_path, monkeypatch):
    runtime, store = _runtime_with_store(warehouse, tmp_path)

    def boom(record):
        raise RuntimeError("磁盘满了")

    monkeypatch.setattr(store, "record_run", boom)
    moment = datetime.combine(date.today(), time(9, 15, 10))

    runtime.tick(moment)                      # 不得抛异常

    _, state, _, _ = store.load()
    assert state["starts"] == 1               # 策略照常跑完
    assert store.strategy_runs(moment.date()) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/live/test_live_runtime.py -k "run_row or failing_recorder" -v`
Expected: FAIL，`AssertionError: [] == ['session_start']`（表已由 Task 2 建好，但没有行）

- [ ] **Step 3: 写最小实现**

`_context` 增参：

```python
    def _context(self, schedule=None, report=None):
        ...
        return Context(
            self.clock, self.feed, view, api, params=self.params,
            state=self.state, market=self.market, schedule=schedule, report=report,
        ), api
```

`_run_event` 改造（保持现有流程，仅包裹记录）：

```python
        started = datetime.now()
        report = RunReport()
        ...
        ctx, api = self._context(report=report)
        ...
        # 事件末尾，commit_event 之后
        self._record_run(key, moment, phase, report, started, orders=len(orders),
                         rejects=rejects, error="")
```

`orders` 取 `_flush` 的返回值（把 `_flush` 改为 `return len(orders)`），`rejects` 取本次
`_record_results()` 新写入的拒单数（`store.record_results` 已返回或可新增返回值；若没有，
用 `len(rejects)` 就地传入）。新增方法：

```python
    def _record_run(self, key, moment, phase, report, started, *, orders, rejects,
                    error) -> None:
        """写一条运行记录。失败只打日志——记录绝不能影响交易。"""
        try:
            market_value = sum(
                position.total_qty * self.last_prices.get(symbol, position.avg_cost)
                for symbol, position in self.portfolio.positions.items()
            )
            self.store.record_run(build_record(
                report, event_key=key, session=moment.date(), phase=phase.value,
                started_at=started, finished_at=datetime.now(),
                cash=self.portfolio.cash, market_value=market_value,
                position_count=len(self.portfolio.positions),
                orders=orders, rejects=rejects, error=error,
            ))
        except Exception as exc:
            log.warning("strategy_run.record_failed", event=key, error=str(exc))
```

`tick` 的「已过期」分支在 `commit_event(..., detail="事件已过期，未补跑")` 之后加：

```python
                expired = RunReport()
                expired.report(note="事件已过期，未补跑")
                self._record_run(key, moment, phase, expired, now,
                                 orders=0, rejects=0, error="")
```

`_run_event` 的异常出口：用 `try/except Exception as exc:` 包住策略调用段，在 `except` 里
先写 `error=str(exc)` 的记录再 `raise`，保证失败也留痕。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/live -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/qsys/live/runtime.py src/qsys/live/store.py tests/live/test_live_runtime.py
git commit -m "feat(live): record a strategy run row for every shadow event"
```

---

### Task 5: 模拟盘运行时写入与结构化策略上报

**Files:**
- Modify: `src/qsys/paper/runtime.py:181-188`（`_context` 接受并传入 report）、`:223-259`（`_run_event` 写记录）
- Modify: `src/qsys/strategy/structural/selection.py:40-140`（新增 `SelectionFunnel` 并暴露 `funnel`）
- Modify: `src/qsys/strategy/structural/strategy.py:82-116`（`on_auction` 上报）
- Test: `tests/paper/test_runtime.py`、`tests/strategy/structural/test_structural_selection.py`、`tests/strategy/structural/test_structural_strategy.py`

**Interfaces:**
- Consumes: Task 1 的 `RunReport`/`build_record`，Task 2 的 `PaperStore.record_run`，Task 3 的 `Context(report=...)`
- Produces:
  - `qsys.strategy.structural.selection.SelectionFunnel`（frozen dataclass）：`pool: int`、`routed: int`、`allowed: int`、`reasons: dict[str, int]`
  - `StructuralSelection.funnel: SelectionFunnel | None`，每次 `select()` 后被赋值
  - 模拟盘每个事件在 `paper_strategy_runs` 留一行

- [ ] **Step 1: 写失败的测试（选股漏斗）**

在 `tests/strategy/structural/test_structural_selection.py` 末尾追加。`selector`、
`low_setup`、`TODAY`、`YESTERDAY` 都是本文件已有的辅助：

```python
def test_select_exposes_the_funnel_it_already_computed(warehouse, write_daily_bars):
    low_setup(warehouse, write_daily_bars)
    selection = selector(warehouse, require_auction_volume=False)

    picks = selection.select(TODAY, YESTERDAY)

    funnel = selection.funnel
    assert funnel is not None
    assert funnel.allowed == len(picks)
    assert funnel.routed == len(selection.decisions)
    assert funnel.pool >= funnel.routed
    rejected = [d for d in selection.decisions if not d.allow]
    assert sum(funnel.reasons.values()) == len(rejected)
    for decision in rejected:
        assert decision.reason in funnel.reasons


def test_funnel_does_not_accumulate_across_calls(warehouse, write_daily_bars):
    low_setup(warehouse, write_daily_bars)
    selection = selector(warehouse, require_auction_volume=False)

    selection.select(TODAY, YESTERDAY)
    first = selection.funnel
    selection.select(TODAY, YESTERDAY)

    assert selection.funnel == first          # 同一天重算，结果必须一样
    assert len(selection.decisions) == first.routed
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/strategy/structural/test_structural_selection.py -k funnel -v`
Expected: FAIL，`AttributeError: 'StructuralSelection' object has no attribute 'funnel'`

- [ ] **Step 3: 实现漏斗暴露**

`selection.py` 在 `Selection` 之后新增：

```python
@dataclass(frozen=True, slots=True)
class SelectionFunnel:
    """一次选股的漏斗。数据本来就在 decisions 里，这里只是不再丢掉。"""

    pool: int          # 候选池只数（已扣除直取停牌）
    routed: int        # 真正走到判定的只数（有完整历史与竞价价）
    allowed: int       # 通过的只数
    reasons: dict[str, int]
```

`StructuralSelection.__init__` 末尾加 `self.funnel: SelectionFunnel | None = None`。

`select()` 开头清空上次结果（`decisions` 原本跨调用累加，漏斗必须按本次统计，
否则同一进程连跑两天会把昨天的原因算进今天）：

```python
        self.decisions = []
        self.funnel = None
```

`if not pool.first_boards: return []` 之前：

```python
            self.funnel = SelectionFunnel(pool=0, routed=0, allowed=0, reasons={})
```

主路径 `return picks` 之前：

```python
        rejected: dict[str, int] = {}
        for decision in self.decisions:
            if not decision.allow:
                rejected[decision.reason] = rejected.get(decision.reason, 0) + 1
        self.funnel = SelectionFunnel(
            pool=len(pool.first_boards), routed=len(self.decisions),
            allowed=len(picks), reasons=rejected,
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/strategy/structural -v`
Expected: PASS（含原有用例；若有用例依赖 `decisions` 跨调用累加，改成按次断言并在提交信息里说明）

- [ ] **Step 5: 写失败的测试（策略上报）**

在 `tests/strategy/structural/test_structural_strategy.py` 里，先给本文件的
`FakeSelection` 加 `funnel`（真实 `StructuralSelection` 现在有这个属性，假实现必须
跟上，否则策略读到的不是同一套接口）：

```python
class FakeSelection:
    def __init__(self, picks=(), funnel=None):
        self.picks = list(picks)
        self.calls = []
        self.funnel = funnel          # 与 StructuralSelection 同名同义

    def select(self, day, previous):
        self.calls.append((day, previous))
        return self.picks
```

`FakeCtx` 加 `report=` 支持：构造函数加 `report=None` 存成 `self._report`，
并加转发方法，与真实 `Context` 一致（没传就丢弃）：

```python
    def report(self, **kwargs):
        if self._report is not None:
            self._report.report(**kwargs)
```

再在文件末尾追加：

```python
def test_auction_reports_the_funnel():
    from qsys.ops.strategy_log import RunReport
    from qsys.strategy.structural.selection import SelectionFunnel

    report = RunReport()
    funnel = SelectionFunnel(pool=37, routed=30, allowed=1,
                             reasons={"高开超过阈值": 12})
    sel = FakeSelection([Pick(str(SYM), "LOW_MAIN")], funnel=funnel)
    strategy = StructuralStrategy(sel)
    state = {}
    strategy.on_session_start(
        FakeCtx(datetime.combine(date(2024, 1, 3), time(9, 15)), state=state))
    ctx = FakeCtx(
        datetime.combine(DAY, AUCTION_AT),
        market=FakeMarket(opens={str(SYM): 10.0}),
        state=state, report=report,
    )

    strategy.on_auction(ctx)

    assert report.candidates == 37 and report.passed == 1
    assert report.reasons["高开超过阈值"] == 12


def test_auction_without_a_previous_session_says_so():
    from qsys.ops.strategy_log import RunReport

    report = RunReport()
    strategy = StructuralStrategy(FakeSelection([Pick(str(SYM), "LOW_MAIN")]))
    ctx = FakeCtx(datetime.combine(DAY, time(9, 15)), report=report)

    strategy.on_auction(ctx)

    assert report.note == "没有上一交易日，无从选股"
```

- [ ] **Step 6: 运行测试确认失败**

Run: `uv run pytest tests/strategy/structural/test_structural_strategy.py -k "reports_the_funnel or without_a_previous" -v`
Expected: FAIL，`AssertionError`（report 仍为空）

- [ ] **Step 7: 实现策略上报**

`structural/strategy.py` 的 `on_auction`：

```python
        previous = ctx.state.get("previous_session")
        if previous is None:
            ctx.report(note="没有上一交易日，无从选股")
            return

        picks = self._selection.select(today, previous)
        # 用 getattr：策略只依赖「有没有漏斗」，不强求每个选股器都实现它
        funnel = getattr(self._selection, "funnel", None)
        if funnel is not None:
            ctx.report(candidates=funnel.pool, passed=funnel.allowed,
                       reasons=funnel.reasons)
        if not picks:
            return
```

`budget.qty <= 0` 的跳过分支把状态一并计入原因（保留现有 `log.info`，不替换）：

```python
            if budget.qty <= 0:
                log.info("structural.skip", symbol=budget.symbol, status=budget.status)
                ctx.report(reasons={f"预算不足（{budget.status}）": 1})
                continue
```

- [ ] **Step 8: 运行测试确认通过**

Run: `uv run pytest tests/strategy -v`
Expected: PASS

- [ ] **Step 9: 写失败的测试（模拟盘运行时）**

在 `tests/paper/test_runtime.py` 末尾追加。`_runtime(warehouse, tmp_path, quotes)`
是本文件已有的辅助，返回 `runtime, store, account_id, today`；`quotes` 用本文件
其他用例同样的构造方式：

```python
def test_paper_events_leave_run_rows(warehouse, tmp_path):
    runtime, store, ident, today = _runtime(warehouse, tmp_path, Quotes())
    moment = datetime.combine(today, time(9, 15, 10))

    runtime.tick(moment)

    rows = store.strategy_runs(ident, today)
    assert [row["phase"] for row in rows] == ["session_start"]
    assert rows[0]["summary"]


def test_paper_recorder_failure_does_not_break_the_event(
    warehouse, tmp_path, monkeypatch,
):
    runtime, store, ident, today = _runtime(warehouse, tmp_path, Quotes())

    def boom(*args, **kwargs):
        raise RuntimeError("坏了")

    monkeypatch.setattr(store, "record_run", boom)
    moment = datetime.combine(today, time(9, 15, 10))

    runtime.tick(moment)                      # 不得抛异常

    events = store.rows(ident, "events")
    assert events and events[0]["status"] == "completed"
    assert store.strategy_runs(ident, today) == []
```

- [ ] **Step 10: 运行测试确认失败**

Run: `uv run pytest tests/paper/test_runtime.py -k "run_rows or recorder_failure" -v`
Expected: FAIL，`AssertionError: [] == ['session_start']`

- [ ] **Step 11: 实现模拟盘写入**

`_context` 增参（与 Task 4 同形）：

```python
    def _context(self, schedule=None, report=None):
        view = PortfolioView.of(self.portfolio, self.last_prices)
        api = OrderApi(view, self.last_prices)
        return Context(
            self.clock, self.feed, view, api,
            params=dict(self.store.account(self.id).config.get("params", {})),
            state=self.state, market=self.market, schedule=schedule, report=report,
        ), api
```

`_run_event` 开头加 `started = datetime.now()` 与 `report = RunReport()`，
`ctx, api = self._context(report=report)`；`_flush` 改为返回 `len(orders)`；
`skip_strategy` 分支在 `commit_event` 之后写一条记录：

```python
            skipped = RunReport()
            skipped.report(note="仅执行账户结算；策略事件未补跑")
            self._record_run(key, moment, phase, skipped, started, orders=0,
                             rejects=0, error="")
            return
```

末尾 `commit_event` 之后：

```python
        self._record_run(key, moment, phase, report, started, orders=orders,
                         rejects=rejects, error="")
```

新增方法（`rejects` 取本次 `_record_results()` 写入的拒单数；该方法改为返回
`len(rejects)`，没有调用它的分支传 0）：

```python
    def _record_run(self, key, moment, phase, report, started, *, orders, rejects,
                    error) -> None:
        """写一条运行记录。失败只打日志——记录绝不能影响交易。"""
        try:
            market_value = sum(
                position.total_qty * self.last_prices.get(symbol, position.avg_cost)
                for symbol, position in self.portfolio.positions.items()
            )
            self.store.record_run(self.id, build_record(
                report, event_key=key, session=moment.date(), phase=phase.value,
                started_at=started, finished_at=datetime.now(),
                cash=self.portfolio.cash, market_value=market_value,
                position_count=len(self.portfolio.positions),
                orders=orders, rejects=rejects, error=error,
            ))
        except Exception as exc:
            log.warning("strategy_run.record_failed", account_id=self.id,
                        event=key, error=str(exc))
```

策略调用段用 `try/except Exception as exc:` 包住，`except` 里先写 `error=str(exc)`
的记录再 `raise`，保证失败也留痕。

- [ ] **Step 12: 运行测试确认通过**

Run: `uv run pytest tests/paper tests/strategy tests/live -v`
Expected: PASS

- [ ] **Step 13: 提交**

```bash
git add src/qsys/paper/runtime.py src/qsys/strategy/structural/selection.py \
        src/qsys/strategy/structural/strategy.py tests/paper/test_runtime.py \
        tests/strategy/structural/test_structural_selection.py \
        tests/strategy/structural/test_structural_strategy.py
git commit -m "feat(paper,strategy): report the selection funnel and record paper runs"
```

---

### Task 6: 两个页面的运行日志

**Files:**
- Create: `src/qsys/web/templates/_strategy_runs.html`（共用片段）
- Create: `src/qsys/web/templates/strategy_runs.html`（影子盘页面，继承 base）
- Modify: `src/qsys/web/views.py`（新增 `MonitorService.strategy_runs(session)` 与 `run_sessions()`）
- Modify: `src/qsys/web/app.py:706-720`（新增 `/s/{session}/runs` 路由）、`:408-430`（模拟账户详情传入记录）
- Modify: `src/qsys/web/templates/base.html:137`（影子盘 subnav 加一项）
- Modify: `src/qsys/web/templates/paper_detail.html`（加「运行日志」区块，include 片段）
- Test: `tests/web/test_app.py`、`tests/web/test_paper_pages.py`

**Interfaces:**
- Consumes: Task 2 的 `LiveStore.strategy_runs/run_sessions`、`PaperStore.strategy_runs/run_sessions`
- Produces: `/s/{session}/runs` 页面；模拟账户详情页新增区块；两者共用片段 `_strategy_runs.html`，上下文变量 `runs`（list[dict]）与 `run_sessions`（list[date]）

- [ ] **Step 1: 写失败的测试（影子盘页面）**

在 `tests/web/test_app.py` 末尾追加。本文件的 `client` 夹具用
`MonitorService(journal, config=cfg, transport=transport, ...)` 构造，本任务给
`MonitorService` 新增 `store=` 参数，所以这里自己建一个带记录的 client：

```python
def test_run_log_page_lists_events_and_reasons(warehouse, tmp_path):
    from datetime import date as _date

    from qsys.live.store import LiveStore
    from qsys.ops.strategy_log import RunReport, build_record

    journal = SessionJournal(warehouse)
    journal.record_equity(D, cash=50_000.0, market_value=0.0)
    store = LiveStore(tmp_path / "live.sqlite3")
    report = RunReport()
    report.report(candidates=37, passed=2, reasons={"高开超过阈值": 12})
    store.record_run(build_record(
        report, event_key=f"{D}:auction:09:30:00", session=D, phase="auction",
        started_at=datetime.combine(D, time(9, 30)),
        finished_at=datetime.combine(D, time(9, 30, 1)),
        cash=50_000.0, market_value=0.0, position_count=0, orders=2, rejects=0,
    ))
    cfg = WebConfig()
    client = TestClient(create_app(
        MonitorService(journal, config=cfg, store=store), cfg))

    page = client.get(f"/s/{D}/runs")

    assert page.status_code == 200
    assert "运行日志" in page.text
    assert "候选 37 只 → 通过 2 只 → 下单 2 笔" in page.text
    assert "高开超过阈值" in page.text and "12" in page.text
    assert "竞价选股" in page.text                 # 阶段显示中文标签


def test_run_log_page_says_when_there_is_nothing(client):
    page = client.get(f"/s/{D}/runs")

    assert page.status_code == 200 and "当日没有运行记录" in page.text
```

（`D`、`time`、`datetime`、`WebConfig`、`create_app`、`MonitorService`、
`SessionJournal`、`TestClient` 本文件已导入；`time` 若未导入则补 `from datetime import time`。）

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/web/test_app.py -k run_log -v`
Expected: FAIL，`TypeError: MonitorService.__init__() got an unexpected keyword argument 'store'`

- [ ] **Step 3: 实现视图与页面**

`views.py` 加：

```python
    def strategy_runs(self, session: date) -> list[dict]:
        return self._store.strategy_runs(session) if self._store else []

    def run_sessions(self) -> list[date]:
        return self._store.run_sessions() if self._store else []
```

（`MonitorService` 若尚未持有 `LiveStore`，在构造函数里接受 `store: LiveStore | None = None`，
并在 `scripts/run_monitor.py` 传入 `LiveStore(args.state)`；该脚本新增 `--state` 参数，
默认 `var/shadow-state.sqlite3`，systemd 单元同步加该参数。）

`app.py` 加路由：

```python
    @app.get("/s/{session}/runs", response_class=HTMLResponse)
    def strategy_runs(request: Request, session: date):
        _require_session(session)
        return _page(request, "strategy_runs.html", session, "runs",
                     runs=service.strategy_runs(session),
                     run_sessions=service.run_sessions())
```

`base.html:137` 的 subnav 追加：

```html
<a href="/s/{{ session }}/runs" class="{{ 'active' if page == 'runs' }}">运行日志</a>
```

共用片段 `_strategy_runs.html`：

```html
{% if not runs %}
  <p class="muted">当日没有运行记录。</p>
{% else %}
<table>
  <tr><th>时刻</th><th>阶段</th><th>结论</th><th>下单/拒单</th><th>净值</th><th>持仓</th><th>耗时</th></tr>
  {% for run in runs %}
  <tr>
    <td>{{ run.started_at[11:19] }}</td>
    <td>{{ phase_labels.get(run.phase, run.phase) }}</td>
    <td>{{ run.summary }}
      {% if run.reasons %}
        <div class="muted" style="font-size:12px">
          {% for reason, count in run.reasons.items() %}{{ reason }} {{ count }}{% if not loop.last %} · {% endif %}{% endfor %}
        </div>
      {% endif %}
      {% if run.error %}<div class="pill bad">{{ run.error }}</div>{% endif %}
    </td>
    <td>{{ run.orders }} / {{ run.rejects }}</td>
    <td>{{ "%.2f"|format(run.total_value) }}</td>
    <td>{{ run.position_count }}</td>
    <td>{{ "%.1f"|format(run.duration_ms / 1000) }}s</td>
  </tr>
  {% endfor %}
</table>
{% endif %}
```

`strategy_runs.html`：继承 `base.html`，`{% block content %}` 里放标题「运行日志」、
交易日选择（`run_sessions` 渲染成 `/s/<day>/runs` 链接）与 `{% include "_strategy_runs.html" %}`。

`phase_labels` 由 `_page` 统一注入：在 `app.py` 的 `_page` 里加
`"phase_labels": PHASE_LABELS`（从 `qsys.ops.strategy_log` 导入）。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/web/test_app.py -k run_log -v`
Expected: PASS

- [ ] **Step 5: 写失败的测试（模拟盘页面）**

在 `tests/web/test_paper_pages.py` 末尾追加。`make_client(warehouse, tmp_path)` 是
本文件已有的辅助（返回 `client, job`），账户通过 `POST /api/paper-accounts` 建，
记录直接用同一个 sqlite 文件的 `PaperStore` 写：

```python
def _account_with_run(warehouse, tmp_path, *, reasons, candidates, passed):
    from datetime import datetime, time

    from qsys.ops.strategy_log import RunReport, build_record

    client, job = make_client(warehouse, tmp_path)
    ident = client.post("/api/paper-accounts", json={
        "backtest_id": job.id, "name": "运行日志", "cash": 100_000,
    }).json()["id"]
    if candidates is None:
        return client, ident
    day = date.today()
    report = RunReport()
    report.report(candidates=candidates, passed=passed, reasons=reasons)
    PaperStore(tmp_path / "workbench.sqlite3").record_run(ident, build_record(
        report, event_key=f"{day}:auction:09:30:00", session=day, phase="auction",
        started_at=datetime.combine(day, time(9, 30)),
        finished_at=datetime.combine(day, time(9, 30, 1)),
        cash=100_000.0, market_value=0.0, position_count=0, orders=0, rejects=0,
    ))
    return client, ident


def test_paper_detail_shows_the_run_log(warehouse, tmp_path):
    client, ident = _account_with_run(
        warehouse, tmp_path, candidates=12, passed=0, reasons={"估值不达标": 12})

    page = client.get(f"/paper/{ident}")

    assert "运行日志" in page.text
    assert "候选 12 只 → 通过 0 只，未下单" in page.text
    assert "估值不达标" in page.text


def test_paper_detail_run_log_empty_state(warehouse, tmp_path):
    client, ident = _account_with_run(
        warehouse, tmp_path, candidates=None, passed=None, reasons=None)

    page = client.get(f"/paper/{ident}")

    assert "当日没有运行记录" in page.text
```

- [ ] **Step 6: 运行测试确认失败**

Run: `uv run pytest tests/paper/test_paper_store.py -k strategy_runs -v`
Expected: FAIL，`AttributeError: 'PaperStore' object has no attribute 'record_run'`

- [ ] **Step 7: 实现模拟盘存储**

`src/qsys/paper/store.py` 建表处追加（注意主键含 `account_id`）：

```sql
                CREATE TABLE IF NOT EXISTS paper_strategy_runs (
                    account_id TEXT NOT NULL, event_key TEXT NOT NULL,
                    session TEXT NOT NULL, phase TEXT NOT NULL,
                    started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL, cash REAL NOT NULL,
                    market_value REAL NOT NULL, total_value REAL NOT NULL,
                    position_count INTEGER NOT NULL, candidates INTEGER,
                    passed INTEGER, orders INTEGER NOT NULL, rejects INTEGER NOT NULL,
                    summary TEXT NOT NULL, reasons_json TEXT NOT NULL DEFAULT '{}',
                    note TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(account_id, event_key)
                );
                CREATE INDEX IF NOT EXISTS paper_runs_session
                    ON paper_strategy_runs(account_id, session, started_at);
```

方法与影子盘同构，SQL 多一个 `account_id` 参数：

```python
    def record_run(self, ident: str, record: RunRecord) -> None:
        columns = ("account_id", *RUN_COLUMNS)
        with self._connect() as con:
            con.execute(
                f"INSERT OR REPLACE INTO paper_strategy_runs "
                f"({','.join(columns)}) VALUES ({','.join('?' * len(columns))})",
                (ident, *run_values(record)),
            )

    def strategy_runs(self, ident: str, session: date) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM paper_strategy_runs WHERE account_id=? AND session=? "
                "ORDER BY started_at", (ident, session.isoformat()),
            ).fetchall()
        return [decode_run(row) for row in rows]

    def run_sessions(self, ident: str, limit: int = 60) -> list[date]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT DISTINCT session FROM paper_strategy_runs WHERE account_id=? "
                "ORDER BY session DESC LIMIT ?", (ident, limit),
            ).fetchall()
        return [date.fromisoformat(row["session"]) for row in rows]
```

`paper/store.py` 顶部同样导入 `RUN_COLUMNS, RunRecord, decode_run, run_values`；
`decode_run` 对模拟盘的行会一并带出 `account_id`，页面不使用该列但不必特殊处理。

- [ ] **Step 8: 运行测试确认通过**

Run: `uv run pytest tests/live/test_live_store.py tests/paper/test_paper_store.py -k strategy_runs -v`
Expected: PASS（2 个用例）

- [ ] **Step 9: 提交**

```bash
git add src/qsys/live/store.py src/qsys/paper/store.py src/qsys/ops/strategy_log.py \
        tests/live/test_live_store.py tests/paper/test_paper_store.py
git commit -m "feat(live,paper): persist one strategy run row per event"
```

---

## 收尾（人工执行，不属于任务）

1. 按 `docs/development-governance.md` squash 进 `test` 并推送，开 `test -> main` 发布 PR；
2. 部署：生产快进到合并后的 SHA，`qsys-monitor` 单元若新增 `--state` 参数需同步安装并 `daemon-reload`，重启 `qsys-monitor`、`qsys-shadow-core`、`qsys-paper`；
3. 按设计文档第九节验证：收盘前确认影子盘每个检查点新增一行、模拟盘同样，打开两个页面确认渲染与空态；
4. 若结构化策略当天无候选，确认 `summary` 写的是「候选池为空，未下单」而不是空白。
