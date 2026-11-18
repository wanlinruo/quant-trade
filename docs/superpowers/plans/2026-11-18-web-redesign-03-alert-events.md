# Web 改版 03：结构化告警事件 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 告警改为结构化事件，作为唯一来源。事件包含稳定键、来源、对象、级别、发生/恢复/通知、证据和时间，写入带版本的事件存储。企业微信等通知文本由事件生成。全部生产者改发事件，日报改读事件存储，旧的文本告警接口与旧账本写入删除。

**Architecture:**
- **`qsys/ops/alert_keys.py`：** 受管键清单，每个键登记来源、允许级别、状态类别、标题模板和说明。
- **`qsys/ops/alert_events.py`：** `AlertEvent`（pydantic，`schema_version=1`）、`render_text(event)`，以及事件存储 `AlertEventLog`。
- **`qsys/ops/alerts.py`：**
  - `AlertBus.emit(key, …)` 是唯一的发布入口：构造事件 → 发送到各 sink → 写入事件存储，投递结果一并记入事件；
  - 删除 `info/warning/critical(title, detail)`、`Alert` 文本模型与 `AlertLedger.record`；
  - 保留只读的 `LegacyLedger.read`，供日报读取迁移前的历史与尚未升级的 N-1 生产者，删除时机见“发布说明”。
- **事件存储与旧账本同一目录：** 文件名为 `<YYYY-MM-DD>.events.jsonl`。各组件现有的可写路径、ACL 与 unit 沙箱都不变，不涉及控制面。

**Tech Stack:** Python 3.11、pydantic 2、pytest。

**Spec:**
- `docs/superpowers/plans/2026-11-18-web-redesign-roadmap-v2.md`（需求分支）：第 1 节第 1 项、第 4 节计划 03
- `docs/superpowers/specs/2026-11-17-web-redesign-design.md` §8.2（告警稳定键、受管清单、与 #592 稳定 ID 一致）
- #662 维护者 11-18 决定第 1 项

**范围：** 计划 03 拆为两份。
- **03（本计划）：** 告警事件，只改 Linux 侧。
- **03b（另写）：** 执行端心跳带 QMT 执行器状态，健康观察记入链路时间序列。涉及 Windows 网关与执行器，发布路径与风险不同。

## Global Constraints

- **告警仍是旁路：** 构造事件、任何一个 sink 或存储写入失败，都不得中断主流程，也不得影响其它 sink。原 `AlertBus` 的失败隔离语义保持不变。
- **键必须受管：**
  - `emit` 用未登记的键时，不抛异常；
  - 记日志 error，并以 `key="unregistered:<原键>"`、级别 critical 照常发送和留存，不丢告警；
  - 测试保证仓库内所有调用都使用已登记的键。
- **级别校验：**
  - 事件级别必须属于该键登记的允许级别，不属于时按上一条同样处理（级别取 critical）；
  - 状态（firing / resolved / notice）与登记的类别一致：incident 类只允许 firing / resolved，notice 类只允许 notice。
- **通知文本不变：**
  - `render_text` 输出与旧 `Alert.as_text` 逐字一致：`[级别] YYYY-mm-dd HH:MM:SS 标题` + 换行 + 详情；
  - 各生产者的标题与详情文字保持现状，由键模板与参数生成。
- **时间：** `occurred_at` 一律带时区（Asia/Shanghai）。
- **证据：** `evidence` 只放结构化事实（服务 ID、reason_code、计数、阈值等），不放密钥、完整配置、路径以外的敏感信息。原 detail 文本照常保留在 `detail`。
- **存储：** 每行一个事件 JSON，追加写入。读取时遇到半行或未知 schema_version，跳过该行并记日志，不能让整份文件不可读。
- **N-1 兼容：**
  - 日报同时读事件存储与旧账本，合并统计投递；
  - 旧账本只读不写；
  - 生产者按组件分别发布，期间新旧两种格式并存。
- 不改任何业务判断（何时告警、边沿触发、去重），只改告警的表示与留存。

## Review Focus

1. **sink 或存储写入失败：** 主流程继续；其它 sink 照常发送；事件的投递结果如实记录失败的 sink 名；存储写失败只记日志。归 Task 2 `test_emit_isolates_sink_and_store_failures`。
2. **未登记的键或不允许的级别：** 不抛异常；以 `unregistered:` 键、critical 级别发送并留存。归 Task 2 `test_unregistered_key_still_delivered_as_critical`。
3. **事件文件里有半行或未来版本：** 其余行可读。归 Task 1 `test_read_skips_partial_and_unknown_version_lines`。
4. **迁移期新旧格式并存：** 日报的投递条数等于两种来源之和，不重复、不遗漏。归 Task 5 `test_digest_merges_events_and_legacy_ledger`。
5. **通知文本回归：** 每个生产者改造后，sink 收到的文本与改造前逐字相同。归各生产者任务的 `*_text_unchanged` 用例。

---

### Task 1: 事件模型、受管键清单、事件存储

**Files:**
- Create: `src/qsys/ops/alert_events.py`、`src/qsys/ops/alert_keys.py`
- Test: `tests/ops/test_alert_events.py`

**Interfaces:**
- **`alert_events.py`：**
  - `Severity`：沿用 `alerts.Severity` 的值 INFO / WARNING / CRITICAL，移到本模块，`alerts` 再导出。
  - `AlertEvent(BaseModel, frozen, extra="forbid")`：
    - `schema_version: Literal[1] = 1`
    - `event_id: str`（uuid4 hex）
    - `key: str`、`source: str`、`subject: str`（对象，如服务 ID、账户、检查 ID；无对象为空串）
    - `severity: Severity`、`state: Literal["firing","resolved","notice"]`
    - `title: str`、`detail: str`
    - `evidence: dict[str, JsonValue]`
    - `occurred_at: datetime`（带时区）
    - `delivered: int = 0`、`failed: tuple[str, ...] = ()`
  - `render_text(event) -> str`：与旧 `Alert.as_text` 相同。
  - `AlertEventLog(root)`：
    - `append(event) -> None`：失败只记日志；
    - `read(day: date) -> list[AlertEvent]`：读 `root/<day>.events.jsonl` 与 `root/<ns>/<day>.events.jsonl`，`ns` 取自 `SERVICE_LEDGER_NAMESPACES`。
- **`alert_keys.py`：**
  - `AlertKey(id, source, kind: Literal["incident","notice"], severities: frozenset[Severity], title: str, description: str)`：`title` 为 `str.format` 模板。
  - `KEYS: dict[str, AlertKey]`
  - `lookup(key) -> AlertKey | None`
  - 键命名为 `<来源>.<对象类>.<事件>`。首批键见 Task 3–4 列表，在本任务一次登记完毕。

- [ ] **Step 1: 写失败测试：**
  - `test_event_roundtrip_and_aware_time`
  - `test_render_text_matches_legacy_format`：用旧 `Alert` 的同一组输入比较。
  - `test_read_skips_partial_and_unknown_version_lines`
  - `test_append_failure_is_swallowed`：根目录不可写。
  - `test_key_registry_ids_unique_and_templates_format`：每个模板用登记的参数名 `format` 不报错。
- [ ] **Step 2:** 运行 `uv run pytest tests/ops/test_alert_events.py -q`，确认失败。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 测试通过。
- [ ] **Step 5: Commit：** `feat(ops): 结构化告警事件模型、受管键清单与事件存储（#662）`

---

### Task 2: AlertBus 改为发布事件

**Files:**
- Modify: `src/qsys/ops/alerts.py`
- Test: `tests/ops/test_alerts.py`（改写既有用例）

**Interfaces:**
- `AlertSink.send(event: AlertEvent)`、`accepts(event)`：
  - `WebhookAlertSink` 发送 `render_text(event)`；
  - `LogAlertSink` 记录 key / subject / state；
  - `MinSeveritySink` 按 `event.severity` 过滤；
  - `RecordingAlertSink.events: list[AlertEvent]`。
- `AlertBus(sinks, log: AlertEventLog | None = None, *, source: str, clock=now_shanghai)`：`emit(key, *, subject="", params=None, detail="", evidence=None, severity=None, state=None) -> AlertEvent`。
  - `severity`、`state` 缺省时取该键允许级别中的唯一值，以及按类别取默认状态：incident 默认 firing，notice 默认 notice。
  - 允许级别不唯一时，必须显式传入。
- `ledger(root)` 改为返回 `AlertEventLog`；`LegacyLedger(root).read(day) -> list[dict]` 只读，`SERVICE_LEDGER_NAMESPACES` 保留。
- 删除：`Alert`、`AlertLedger.record`、`AlertBus.info/warning/critical`。

- [ ] **Step 1: 写失败测试：**
  - `test_emit_builds_event_from_registry`
  - `test_emit_isolates_sink_and_store_failures`
  - `test_unregistered_key_still_delivered_as_critical`
  - `test_disallowed_severity_falls_back_to_unregistered_critical`
  - `test_min_severity_sink_filters_on_event`
  - `test_strategy_sinks_unchanged`
- [ ] **Step 2–4:** 先失败；实现后运行 `uv run pytest tests/ops -q`。此时生产者尚未改造、会导入失败，只运行本文件。
- [ ] **Step 5: Commit：** 与 Task 3 合并提交（Task 2 单独提交会让仓库处于不可导入的状态）。

---

### Task 3: 交易侧生产者改发事件（实盘核心、tick 熔断、策略通知、模拟盘、影子核心）

**Files:**
- Modify:
  - `src/qsys/live/runtime.py`
  - `src/qsys/live/tick_guard.py`
  - `src/qsys/live/core_cli.py`
  - `src/qsys/ops/notify.py`
  - `src/qsys/paper/runtime.py`
  - `src/qsys/live/shadow_cli.py`
- Test: 既有对应测试改为断言事件（键、subject、级别、状态），并新增 `*_text_unchanged`。

**首批键（交易侧）：**

| 键 | 类别 | 级别 | 来源位置 |
|---|---|---|---|
| `live.reconcile.drift` | incident | warning | 影子盘与券商账户不一致（不停机） |
| `live.reconcile.failed` | incident | critical | 实盘账户对账失败 |
| `live.heartbeat.timeout` | incident | critical | subject 为心跳超时的组件列表 |
| `live.reject.streak` | incident | warning | runtime 716 |
| `live.reject.fuse` | incident | critical | runtime 723 |
| `live.tick.fuse` | incident | critical/info | tick_guard 的熔断与恢复（恢复为 resolved） |
| `live.core.fatal` | incident | critical | core_cli 141 |
| `strategy.run.error` | notice | critical | notify |
| `strategy.run.rejects` | notice | warning | notify |
| `strategy.run.report` | notice | info | notify，subject 为引擎名 |

- [ ] **Step 1:** 改写测试，确认失败。
- [ ] **Step 2:** 实现：Task 2 与本任务一起完成，保证仓库可导入。
- [ ] **Step 3:** 运行 `uv run pytest tests/live tests/ops tests/paper -q`，全部通过。
- [ ] **Step 4: Commit：** `refactor(ops): 告警总线改发结构化事件，交易侧生产者迁移（#662）`

---

### Task 4: 运维侧生产者改发事件（健康观察、基础设施巡检、重启演练、服务质检）

**Files:**
- Modify:
  - `src/qsys/ops/health_observer_cli.py`：`BusinessAlertTracker`、`ProcessAlertTracker`、`InfraAlerts`，以及 #677 网关单侧落闸告警
  - `src/qsys/ops/infra_watch.py`：`Finding.key` 映射到事件键
  - `src/qsys/ops/reboot_drill_cli.py`
  - `src/qsys/ops/service_checks_cli.py`
- Test: 对应既有测试改写，并新增 `*_text_unchanged`。

**首批键（运维侧）：**

| 键 | 说明 |
|---|---|
| `observer.chain.down` / `observer.chain.state_unreadable` / `observer.chain.degraded` | 交易链路状态；恢复为对应键的 resolved |
| `observer.process.down` | 进程停止；恢复为 resolved |
| `observer.schedule.inactive` / `observer.schedule.failed` | 调度状态；恢复为 resolved，标题保留现有三种措辞 |
| `infra.<finding 前缀>` | 按 `Finding.key` 前缀映射：`disk`、`system_disk`、`cert`、`unit`、`kill`、`redis`、`heartbeat`、`order_book` 等。实现时逐个核对 `survey()` 的全部 Finding 并一次登记；subject 取 key 中冒号后的部分 |
| `gateway.local_kill.unilateral` | #677 |
| `drill.reboot.passed` / `drill.reboot.failed` | 重启演练（notice） |
| `checks.service.passed` / `checks.service.problems` | 服务质检（notice，级别为 warning/critical） |

subject 取服务的稳定 ID（`SERVICE_BY_ID` 的键），与 #592 的稳定 ID 一致。

- [ ] **Step 1:** 改写测试，并新增 `test_every_emit_key_is_registered`：扫描 `src/qsys` 中 `emit("…"` 的字面量键，均在 `KEYS` 中；`infra.*` 由映射表单测覆盖。
- [ ] **Step 2–3:** 先失败，实现后运行 `uv run pytest tests -q -k "observer or infra or drill or service_checks or alert"`，全部通过。
- [ ] **Step 4: Commit：** `refactor(ops): 运维侧告警生产者改发结构化事件（#662）`

---

### Task 5: 日报改读事件存储

**Files:**
- Modify: `src/qsys/ops/daily_digest_cli.py`、`src/qsys/ops/digest.py`（投递统计的输入改为事件，旧账本经 `LegacyLedger` 合并）
- Test: `tests/ops/test_digest*.py`

- [ ] **Step 1: 写失败测试：**
  - `test_digest_merges_events_and_legacy_ledger`
  - `test_digest_counts_failed_deliveries_from_events`
  - `test_digest_section_text_unchanged`：迁移前后同样的投递，日报文字相同。
- [ ] **Step 2–4:** 先失败；实现；运行全量 `uv run pytest -q`，全部通过。
- [ ] **Step 5: Commit：** `refactor(ops): 日报改读结构化告警事件，兼容旧账本（#662）`

---

### Task 6: 收尾与发布说明

- [ ] `grep -rn "\.critical(\|\.warning(\|\.info(" src/qsys/live src/qsys/ops src/qsys/paper`：除 structlog 外，不再有告警旧接口调用；`grep -rn "AlertLedger\b\|class Alert\b" src`：无结果。
- [ ] 全量 `uv run pytest -q`；新增生产入口脚本时补 `tests/scripts/test_scripts_import.py`（本计划预计不新增）。
- [ ] 在计划文末“发布说明”核对受影响组件与发布顺序；推送分支 `feat/live-structured-records`；在 #662 记录。**不发布**，发布另行请示维护者。

## 发布说明（发布时另行请示，不在本计划内执行）

- **受影响组件：** daily-digest、health-observer、service-checks、reboot-drill、paper、live-core（交易组件，收盘后发布）、shadow-core（当前未运行，随代码合入）。
- **顺序：**
  1. 先发布 daily-digest（新旧格式都能读）；
  2. 再逐个发布生产者；
  3. live-core 收盘后发布，并按 AGENTS.md §2.1 #670 的顺序处理急停。
- **N-1 组合：** 新 daily-digest + 旧生产者，读旧账本；新生产者 + 旧 daily-digest，旧日报读不到新事件，当日投递统计偏少。所以 daily-digest 必须最先发布。
- **旧账本读取的删除时机：** 全部生产者发布满 14 天后，在合入计划 02 中删除 `LegacyLedger`。历史文件不删除。
