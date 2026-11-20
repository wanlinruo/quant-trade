# Web 改版 06a：告警中心 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新页面 `/app/alerts`（告警中心）上线：
- 结论：未处理 N 条，其中严重 M 条；
- 近 14 日趋势；
- 告警表，分四个标签：未处理 / 已确认 / 已恢复 / 全部；
- 抽屉：来源、说明、时间线，以及“确认已知悉”；
- 顶栏的未处理告警数接入真实数据；
- 支持按日回看。

**Architecture:**
- **数据来源：** 计划 03 的告警事件存储（多个根目录，每个根目录各含组件命名空间），经计划 04 的 `alert_summary` 归并。
- **“确认已知悉”：** Web 自有的独立 SQLite（`webapp/stores/alert_acks.py`），只记录谁、何时确认了哪个（键, 对象），以及确认到哪一时刻。不改变告警原始记录或任何业务状态（设计文档 §13 第 2 项）。
- **后端：** `contracts/alerts.py`、`queries/alerts.py`、`routes/alerts.py`；外壳状态的 `alerts_open` 改由同一查询给出。
- **前端：** `pages/alerts/*`、`api/alerts.ts`，类型由契约生成。

**Tech Stack:** FastAPI、pydantic 2、sqlite3、React + antd + ECharts（沿用已有依赖）。

**Spec:**
- `docs/superpowers/specs/2026-11-17-web-redesign-design.md`：§8.2、§7.2、§13 第 1、2 项、§5.5（按日回看）
- 计划 03（事件模型、受管键 `alert_keys`）、计划 04（`alert_summary`）
- R1–R4 的统一约定

## Global Constraints

- **沿用 R1 统一约定：**
  - 时间带时区；缺失为 null；
  - 错误体统一；GET 一律 no-store；
  - 契约 `extra="forbid"`，带 `schema_version`，契约名全局唯一，登记进 `EXPORTED`。
- **条目状态：**
  - 未处理 = open 且未确认，或确认之后又有新的发生；
  - 已确认 = open 且确认时刻覆盖最近一次发生；
  - 已恢复 = resolved；
  - 通知类（notice）不进入“未处理”，只在“全部”中出现，并单独计数。
- **结论条：**
  - 只被未处理的 critical / warning 染色；
  - 未登记键（`unregistered:`）一律视为未处理的严重告警，并显示“未登记键”标记。
- **“确认已知悉”：**
  - 只记录 `{key, subject, by, acked_at, through}`，`through` 为确认时的最近发生时刻；
  - 不改变告警原始记录；
  - 写接口只在配置了存储时注册；
  - 按钮旁显示“只记录已知悉，不改变任何交易状态”（§5.6）；
  - `by` 取 HTTP Basic 认证的用户名；拿不到时记 `unknown`，不伪造；
  - 不回显或记录密码。
- **按日回看：** `?day=YYYY-MM-DD` 时，窗口为该日往前 14 天，数据截至该日 24:00（上海时间）；条目状态按窗口内事件计算；“已知悉”只取 `acked_at` 不晚于窗口结束的记录。
- **读取边界：**
  - 告警根目录列表与“已知悉”存储路径由配置给出，默认为 `var/alerts` 与 `var/web/alert-acks.sqlite3`；
  - 列表与汇总经 60 秒单飞缓存；确认后清除缓存。
- **生产权限（发布前实测，不在本计划内假设）：** Web 运行账号对各告警目录的只读访问、对“已知悉”存储目录的写入，按 AGENTS.md §2.2 用真实账号与 unit 沙箱实测。

## Review Focus

1. **确认之后同一问题再次发生：** 回到“未处理”，抽屉显示上次确认的人和时刻。归 Task 1 `test_refire_after_ack_is_unhandled_again`。
2. **未登记键：** 计入未处理严重告警，结论条染红。归 Task 1 `test_unregistered_counts_as_critical_unhandled`。
3. **告警目录不存在或不可读：** 返回 200，结论为“告警存储不可读”（状态 unknown），不显示“0 条未处理”。归 Task 1 `test_unreadable_roots_report_unknown_not_zero`。
4. **回看某日：** 不包含该日之后的事件与确认。归 Task 1 `test_day_view_excludes_later_events_and_acks`。
5. **确认请求格式错误或条目不存在：** 返回 422 或 404 统一错误体，不写入存储。归 Task 2 `test_ack_validation`。

---

### Task 1: “已知悉”存储、告警查询与只读路由

**Files:**
- Create:
  - `src/qsys/webapp/stores/__init__.py`、`src/qsys/webapp/stores/alert_acks.py`
  - `src/qsys/webapp/contracts/alerts.py`、`src/qsys/webapp/queries/alerts.py`、`src/qsys/webapp/routes/alerts.py`
- Modify:
  - `src/qsys/webapp/app.py`：`WebappDeps` 增加 `alert_roots: tuple[Path, ...] = ()`、`alert_acks: Path | None = None`；挂载路由
  - `src/qsys/webapp/queries/shell.py`、`contracts/shell.py`：`alerts_open` 取未处理条数；拿不到时为 null 并在 `missing` 说明原因
  - `src/qsys/web/monitor_cli.py`：传入告警根目录（`var/alerts`，以及 live-core 独立告警目录参数，如已配置）与“已知悉”存储路径
  - `registry.py`：登记契约；重新生成前端类型
- Test: `tests/webapp/test_webapp_alerts.py`、`tests/webapp/test_webapp_alert_acks.py`

**Interfaces:**
- `AlertAckStore(path)`：
  - `ack(key, subject, *, by, through: datetime, at: datetime) -> AckRecord`
  - `latest(until: datetime | None = None) -> dict[tuple[str, str], AckRecord]`
  - 表 `alert_acks(id, key, subject, by, acked_at_utc, through_utc)`，只追加。
- **契约：**
  - `AckInfo{by, acked_at: Timestamp, through: Timestamp}`
  - `AlertItem`：
    - `key`、`subject`、`source`
    - `kind: Literal["incident","notice","unregistered"]`
    - `severity: Literal["INFO","WARNING","CRITICAL"]`
    - `status: Literal["unhandled","acknowledged","resolved","notice"]`
    - `first_at`、`last_at`：`Timestamp`；`resolved_at: Timestamp | None`
    - `count: int`、`title`、`detail`
    - `description: str | None`：取自受管键清单
    - `ack: AckInfo | None`
  - `AlertTrendDay{day: date, critical, warning, info, resolved}`
  - `AlertCenter`：
    - `schema_version`、`enabled: Literal[True]`
    - `state: Literal["ok","unknown"]`、`day: date`
    - `window_start: date`、`window_end: date`
    - `unhandled`、`unhandled_critical`、`acknowledged`、`notices`：`int | None`（state 为 unknown 时为 None）
    - `items: list[AlertItem]`、`trend: list[AlertTrendDay]`
    - `missing: dict[str, str]`
  - `AlertEventInfo{event_id, occurred_at, severity, state, title, detail, evidence: dict[str, JsonValue], delivered, failed: list[str]}`
  - `AlertDetail{schema_version, enabled, item: AlertItem, events: list[AlertEventInfo]}`
  - `AlertsDisabled{schema_version, enabled: Literal[False]}`
- **查询：** `AlertCenterQuery(roots, acks: AlertAckStore | None, *, clock)`：
  - `center(day: date | None) -> AlertCenter`
  - `detail(key, subject, day) -> AlertDetail | None`
  - `unhandled_count() -> int | None`
- **只读路由：**
  - `GET /api/app/alerts?day=` → `AlertCenter | AlertsDisabled`
  - `GET /api/app/alerts/detail?key=&subject=&day=` → `AlertDetail`，不存在返回 404

- [ ] **Step 1: 写失败测试：**
  - `test_ack_store_append_and_latest`
  - `test_center_counts_and_statuses`
  - `test_refire_after_ack_is_unhandled_again`
  - `test_unregistered_counts_as_critical_unhandled`
  - `test_unreadable_roots_report_unknown_not_zero`
  - `test_day_view_excludes_later_events_and_acks`
  - `test_trend_14_days`
  - `test_detail_timeline_and_404`
  - `test_shell_alerts_open_from_query`
  - `test_alert_responses_validate_against_contracts`
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 运行 `uv run pytest tests/webapp tests/web -q`，全部通过。
- [ ] **Step 5: Commit：** `feat(web): 告警中心查询、只读接口与“已知悉”存储（#662）`

---

### Task 2: “确认已知悉”写接口

**Files:**
- Modify: `contracts/alerts.py`（`AckRequest{key, subject, through: Timestamp}`、`AckResult{schema_version, ack: AckInfo}`）、`routes/alerts.py`
- Test: `tests/webapp/test_webapp_alert_acks.py`

**行为：** `POST /api/app/alerts/ack`：
- 只在配置了存储时注册；
- `through` 必须等于该条目当前的 `last_at`，防止在旧页面上确认到新的发生，不一致时返回 409；
- 条目不存在返回 404；
- 成功后清除缓存，返回 201。

- [ ] **Step 1: 写失败测试：**
  - `test_ack_records_who_and_through`：Basic 认证用户名；无认证时为 unknown。
  - `test_ack_validation`：多余字段或非法时间返回 422；不存在返回 404；through 过期返回 409。
  - `test_ack_route_absent_without_store`
  - `test_ack_never_logs_password`
- [ ] **Step 2–4:** 先失败；实现后运行 `uv run pytest tests/webapp -q`，全部通过。
- [ ] **Step 5: Commit：** `feat(web): 告警“确认已知悉”只记录接口（#662）`

---

### Task 3: 告警中心前端

**Files:**
- Create: `frontend/src/api/alerts.ts`、`frontend/src/pages/alerts/AlertsPage.tsx`、`AlertDrawer.tsx`、`AlertTrend.tsx`、`labels.ts`、测试与样例（由后端夹具导出）
- Modify: `frontend/src/pages/registry.ts`（登记 `alerts`）、导航（若尚无入口）；顶栏告警徽标读取 `alerts_open`
- Modify: `frontend/e2e`：告警页在 390、1280、1600 宽度下无横向溢出

**页面要求：**
- **结论条：**
  - 未处理 N 条（其中严重 M 条）；
  - state 为 unknown 时显示“告警存储不可读”，不显示 0。
- **趋势：** 近 14 日堆叠柱：严重、警告、通知，另有已恢复。
- **告警表：**
  - 标签：未处理（默认）/ 已确认 / 已恢复 / 全部；
  - 列：级别、标题、对象、来源、首次、最近、次数、状态；
  - 未登记键带红色“未登记键”标记。
- **抽屉：**
  - 显示说明（受管键描述）、来源、对象、时间线（事件列表，含投递失败通道）、最近一次确认；
  - “确认已知悉”按钮旁写“只记录已知悉，不改变任何交易状态”，确认后刷新。
- **回看：** 日期选择写进 URL（`?day=`），回看时隐藏确认按钮。
- 时间一律用市场时区格式化。

- [ ] **Step 1:** 写测试：
  - `alerts page shows unhandled conclusion and unknown state`
  - `tabs filter by status`
  - `drawer acks with note and refreshes`
  - `day view hides ack button`
  - `unregistered badge`
- [ ] **Step 2:** 实现；运行 `npm test && npm run typecheck && npm run build && npx playwright test`，全部通过。
- [ ] **Step 3: Commit：** `feat(web): 告警中心页面（#662）`

---

### Task 4: 收尾

- [ ] 全量 `uv run pytest -q`；前端全套检查；本地只读预览冒烟，包括写入一条“已知悉”。
- [ ] 推送需求分支；在 #662 记录。
- [ ] **发布前另行核对（不在本计划内）：** Web 账号对告警目录的只读访问、对 `var/web` 的写入，按 AGENTS.md §2.2 实测。
