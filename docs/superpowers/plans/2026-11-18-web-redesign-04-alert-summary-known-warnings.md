# Web 改版 04：告警汇总与已知警告受管清单 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供两项领域层只读能力，供告警中心与数据质量页使用：
- 按稳定键归并告警事件，得到告警条目与近 14 日趋势；
- 已知警告受管清单，以及“新警告 / 已知警告 / 上游问题”的判定。

**Architecture:**
- **`qsys/ops/alert_summary.py`：** 读取计划 03 的事件存储，按 `(key, subject)` 归并为告警条目，给出首次、最近、次数、当前状态与最高级别，并按日统计趋势。纯只读，不写任何状态。
- **`qsys/data/catalog/known_warnings.py`：** 受管清单 `KNOWN_WARNINGS`，结构为“检查稳定 ID → Issue + 基线”。基线包含登记时的违规数与样本指纹。另含判定函数。登记与调整基线都走代码评审。
- **`scripts/known_warnings_baseline.py`：** 只读工具，从仓库里最近一次质检生成清单候选条目，由人审阅后提交。
- **“确认已知悉”存储移到计划 06。** 它是 Web 自有存储，只由告警中心写入，与页面、路由同在 Web 分支实施。

**Tech Stack:** Python 3.11、polars、pyarrow、pytest。

**Spec:**
- `docs/superpowers/specs/2026-11-17-web-redesign-design.md`：§7.2（已知警告与新警告）、§8.2（告警中心）、§13 第 1、2 项
- 总路线 v2 第 4 节计划 04
- 计划 03（事件模型、受管键）

## Global Constraints

- **只读。** 本计划不改任何生产组件的运行行为，也不改质检结论、ServiceRun 或事件记录。
- **判定规则逐字取自 §7.2：**
  - 违规数不超过基线且没有新样本 → 已知警告；
  - 违规数超过基线或出现新样本 → 新警告，并给出“比基线多 N”；
  - 数据层已有的 `known_issue` 标注 `上游数据自相矛盾`（#653）计为上游问题，不算新警告。
- **样本指纹：** 对违规样本 Parquet 的全部行做规范化（列名排序；行按全部列的字符串值排序）后计算 SHA-256。
  - 基线有指纹、本次没有样本文件：判为新警告，理由“无法证明样本未变”，不猜；
  - 基线没有指纹：只比较违规数。
- **阻断（blocking）失败不参与已知警告判定**，始终为失败（#482：真实失败不降级）。
- **告警条目状态：**
  - incident 类：最近一条为 firing → open，为 resolved → resolved；
  - notice 类：notice；
  - 不从时间推断恢复。
- **未登记键**（`unregistered:` 前缀）照常归并展示，并标记 `unregistered=True`。
- **时间带时区；缺失为 None。**
- 受管清单初始为空：实际条目必须由工具从生产数据生成并经评审，本计划不编造任何基线。

## Review Focus

1. **同一键交替发生、恢复多次：** 次数为发生次数（firing 条数）；状态取最后一条；首次、最近取 firing 的时间。归 Task 1 `test_flapping_incident_counts_and_state`。
2. **事件跨日：** 区间内多日事件合并到同一条目；趋势按事件发生日（上海时区）计数。归 Task 1 `test_incident_spans_days_and_trend_by_local_day`。
3. **违规数相同但样本内容变了：** 判为新警告。归 Task 2 `test_same_count_new_sample_is_new`。
4. **样本行顺序不同、列顺序不同：** 指纹相同。归 Task 2 `test_fingerprint_ignores_row_and_column_order`。
5. **阻断失败即使登记在清单中：** 仍为失败。归 Task 2 `test_blocking_never_known`。

---

### Task 1: 告警汇总

**Files:**
- Create: `src/qsys/ops/alert_summary.py`
- Test: `tests/ops/test_alert_summary.py`

**Interfaces:**
- `AlertIncident`（frozen dataclass）：
  - `key: str`、`subject: str`、`source: str`
  - `kind: Literal["incident","notice","unregistered"]`、`unregistered: bool`
  - `severity: Severity`：区间内 firing 或 notice 事件的最高级别
  - `state: Literal["open","resolved","notice"]`
  - `first_at`、`last_at`：`datetime`，取 firing 或 notice 事件
  - `resolved_at: datetime | None`
  - `count: int`
  - `title: str`、`detail: str`：最近一条
  - `events: tuple[AlertEvent, ...]`：按时间排序
- `DayTrend(day: date, critical: int, warning: int, info: int, resolved: int)`：按事件发生日计数；resolved 单独计，不计入级别。
- `summarize(events: Iterable[AlertEvent]) -> list[AlertIncident]`：排序依次为 open 在前 → 级别高在前 → `last_at` 新在前。
- `trend(events, days: list[date]) -> list[DayTrend]`
- `AlertSummary(roots: Sequence[Path])`：`events(start: date, end: date) -> list[AlertEvent]`，合并多个根目录，每个根各自含组件命名空间。

- [ ] **Step 1: 写失败测试：**
  - `test_incident_open_then_resolved`
  - `test_flapping_incident_counts_and_state`
  - `test_notice_kind_and_counts`
  - `test_unregistered_key_flagged`
  - `test_incident_spans_days_and_trend_by_local_day`
  - `test_sort_open_first_then_severity_then_recency`
  - `test_summary_reads_multiple_roots_and_namespaces`
- [ ] **Step 2:** 运行 `uv run pytest tests/ops/test_alert_summary.py -q`，确认失败。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 测试通过。
- [ ] **Step 5: Commit：** `feat(ops): 告警汇总：按稳定键归并告警条目与按日趋势（#662）`

---

### Task 2: 已知警告受管清单与判定

**Files:**
- Create: `src/qsys/data/catalog/known_warnings.py`
- Test: `tests/data/catalog/test_known_warnings.py`

**Interfaces:**
- `KnownWarning`（frozen dataclass）：
  - `check_id: str`、`metric_id: str | None`：metric_id 为 None 表示该检查的所有指标
  - `issue: str`：如 `"#594"`
  - `baseline_violations: int`、`baseline_sample_sha256: str | None`
  - `registered_on: date`、`note: str`
- `KNOWN_WARNINGS: tuple[KnownWarning, ...] = ()`
- `lookup(check_id, metric_id) -> KnownWarning | None`：优先精确匹配，其次匹配 metric_id 为 None 的条目。
- `sample_fingerprint(path: Path | None) -> str | None`：路径为 None 或文件不存在时为 None；读失败时抛出，由调用方处理。
- `Verdict(kind: Literal["failed","new","known","upstream","passed"], issue: str | None, over_baseline: int | None, reason: str)`
- `judge(row: Mapping, fingerprint: str | None, *, entries=KNOWN_WARNINGS) -> Verdict`：
  - `row` 为 `metric_checks` 的一行；
  - 判定顺序：
    1. passed → passed；
    2. blocking → failed；
    3. `known_issue == "上游数据自相矛盾"` → upstream，issue 为 `"#653"`；
    4. 有清单条目 → 按 §7.2 判为 known 或 new；
    5. 其余 → new，理由“未登记”。

- [ ] **Step 1: 写失败测试：**
  - `test_passed_and_blocking`
  - `test_blocking_never_known`
  - `test_upstream_self_contradiction`
  - `test_unregistered_warning_is_new`
  - `test_within_baseline_same_sample_is_known`
  - `test_over_baseline_is_new_with_delta`
  - `test_same_count_new_sample_is_new`
  - `test_baseline_sample_but_current_missing_is_new`
  - `test_baseline_without_fingerprint_compares_count_only`
  - `test_fingerprint_ignores_row_and_column_order`
  - `test_lookup_exact_then_wildcard`
  - `test_registry_entries_valid`：清单内 check_id 存在于 `CHECKS`；metric_id 为 None 或存在于 `REGISTRY`；issue 形如 `#数字`；基线 ≥ 0。
- [ ] **Step 2–4:** 先失败；实现后运行 `uv run pytest tests/data/catalog/test_known_warnings.py -q`，全部通过。
- [ ] **Step 5: Commit：** `feat(data): 已知警告受管清单与新/已知/上游判定（#662）`

---

### Task 3: 基线候选生成工具（只读）

**Files:**
- Create: `scripts/known_warnings_baseline.py`
- Modify: `tests/scripts/test_scripts_import.py`：加入清单
- Test: `tests/scripts/test_known_warnings_baseline.py`

**Interfaces：** `main(argv) -> int`：
- 参数：`--warehouse PATH`（必需）、`--sample-root PATH`（必需）、`--issue "#594"`（必需）、`--run-id`（可选，缺省取最近一次）。
- 只读 `metric_checks`；选出未通过、非阻断、且 `known_issue` 不是上游自相矛盾的行；计算样本指纹。
- 输出可直接粘贴进 `KNOWN_WARNINGS` 的 Python 字面量。
- 不写任何文件。

- [ ] **Step 1: 写失败测试：**
  - `test_baseline_tool_prints_entries_for_latest_run`
  - `test_baseline_tool_skips_upstream_and_blocking`
  - `test_baseline_tool_writes_nothing`：运行前后目录树字节一致。
- [ ] **Step 2–4:** 先失败；实现后运行 `uv run pytest tests/scripts -q`，全部通过。
- [ ] **Step 5: Commit：** `feat(scripts): 已知警告基线候选生成工具（只读）（#662）`

---

### Task 4: 收尾

- [ ] 全量 `uv run pytest -q`；运行 `check_repo_policy.py --head feat/live-structured-records --base test --check-commits`。
- [ ] 推送分支；在 #662 记录。
- [ ] **后续（另行请示）：** 用工具对生产仓库只读运行，生成 #594 条目候选，经评审提交到 `KNOWN_WARNINGS`。
