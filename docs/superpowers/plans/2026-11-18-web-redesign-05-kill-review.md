# Web 改版 05：停机闸解除前核对的机器可读输出 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给受管入口 `scripts/kill.py` 增加只读的 `review live` 动作。它逐项核对实盘停机闸能否解除，并输出机器可读结果（JSON，带 `schema_version`），供实盘对账页的“停机闸解除前核对清单”展示，取代节点上的一次性复核脚本。

**Architecture:**
- **`qsys/live/kill_review.py`：**
  - `gather(...) -> ReviewInputs`：只读采集，读 Redis 键、本地 KILL 文件、实盘状态库（`LiveStore(read_only=True)`）；
  - `review(inputs, *, confirmed_by, now) -> KillReview`：纯函数，逐项判定；
  - 契约 `KillReview` / `ReviewItem`。
- **`scripts/kill.py review live [--json] [--confirmed-by NAME] [--record PATH]`：**
  - 打印人读或 JSON 结果；
  - `--record` 时把 JSON 原子写入指定路径，供页面读取，写入位置由计划 06 定；
  - 退出码：0 = ready，1 = blocked，3 = needs_confirmation。
- **`resume` 的现有行为不变。** 是否把核对结果作为解除的硬门槛，由维护者另行决定，不在本计划内。

**Tech Stack:** Python 3.11、pydantic 2、pytest。

**Spec:**
- `docs/superpowers/specs/2026-11-17-web-redesign-design.md`：§8.5 停机闸解除前核对清单、§13 第 6 项
- AGENTS.md §4“系统撤单只撤本系统委托”（#666）、§2.1 #670“先解闸、后发布”
- AGENTS.md §2.1“重复使用的预检/验证步骤应固化为仓库受管入口并带负例测试”

## Global Constraints

- **只读。** `review` 不写 Redis、不改 KILL、不发控制指令、不写状态库；实盘状态库用 `read_only=True` 打开并确定性关闭。`--record` 只写调用方指定的文件。
- **不推断：**
  - 拿不到的数据，该项判为 `unknown`，不当通过；
  - 券商活动委托多于本系统未终结委托时，判为 `confirm`，要求维护者确认来源（#666），不推断为人工委托。
- **项目状态与总体结论：**
  - 项目状态：`pass` / `fail` / `unknown` / `confirm` / `info`；
  - 总体结论：任一项 fail 或 unknown → `blocked`；否则任一项 confirm → `needs_confirmation`；否则 → `ready`；
  - `info` 不参与结论。
- **触发原因的判定只对已知原因做复查：**
  - 心跳超时 → 由心跳项决定；
  - 对账不一致 → 由对账项决定；
  - 其他原因（连续拒单熔断、tick 连续故障熔断、人工停止、未知）→ `confirm`。
- **时间带时区（Asia/Shanghai）。** 心跳新鲜度沿用 `preflight_cli.heartbeat_freshness`；账户快照新鲜度沿用盘前检查的 5 秒。
- 项目 ID 稳定（页面与历史记录按 ID 引用），新增项只追加不改名。
- 不改任何生产组件的运行行为。`kill.py` 属于运维脚本，`review` 为新增动作。

## Review Focus

1. **Redis 不可达：** 三道闸的远程部分、心跳、账户快照、券商委托各项为 unknown，总体 blocked；命令不抛异常，退出码 1。归 Task 1 `test_redis_unavailable_is_blocked_not_crash`。
2. **实盘状态库不存在或未初始化：** 对账与本系统委托项为 unknown，不当 0。归 Task 1 `test_missing_state_db_is_unknown`。
3. **券商活动委托多于本系统：** confirm，并写明差额；少于本系统 → fail。归 Task 1 `test_broker_vs_own_orders`。
4. **触发原因是 tick 熔断，且其余全部通过：** needs_confirmation，不是 ready。归 Task 1 `test_fuse_reason_requires_confirmation`。
5. **11-16 的复核缺陷类型（字段顺序依赖）：** 判定只使用结构化字段，不解析命令行输出的列顺序。归 Task 2 `test_review_uses_structured_inputs_only`：在 MemoryTransport 与真实状态库上端到端运行。

---

### Task 1: 核对模型与纯判定

**Files:**
- Create: `src/qsys/live/kill_review.py`
- Test: `tests/live/test_kill_review.py`

**Interfaces:**
- `ReviewItem(BaseModel, frozen, extra=forbid)`：
  - `id: str`、`label: str`
  - `state: Literal["pass","fail","unknown","confirm","info"]`
  - `detail: str`、`observed: dict[str, JsonValue]`
- `KillReview(BaseModel, frozen, extra=forbid)`：
  - `schema_version: Literal[1] = 1`、`target: Literal["live"]`
  - `reviewed_at: datetime`（带时区）
  - `overall: Literal["ready","blocked","needs_confirmation"]`
  - `engaged: bool`：任一闸已落
  - `items: list[ReviewItem]`
- `ReviewInputs`（dataclass，全部可为 None 表示读不到）：
  - `local_kill_reason`（本地 KILL：None 为未落；`""` 为已落但无原因）
  - `redis_kill_reason`
  - `redis_ok: bool`
  - `agent: Heartbeat | None`、`market: Heartbeat | None`
  - `account: AccountSnapshot | None`
  - `portfolio: Portfolio | None`
  - `own_pending: int | None`
  - `state_error: str | None`
- **项目（ID 固定，按此顺序）：**

| ID | 说明 |
|---|---|
| `gates.state` | info，observed 为三道闸 `{linux_local, redis, windows_gateway}`，取值 true / false / null |
| `trigger.cleared` | 按触发原因判定，见 Global Constraints |
| `link.agent_heartbeat` | agent 心跳新鲜度 |
| `link.market_heartbeat` | market 心跳新鲜度 |
| `link.agent_posture` | trading_mode=live、orders_enabled、account_protected 三者都为真才 pass |
| `account.snapshot` | 账户快照存在且新鲜 |
| `reconcile.positions_cash` | `reconcile_positions(portfolio, account, cash_decides=True)` 一致才 pass |
| `orders.own_pending` | 本系统未终结委托：0 → pass，> 0 → confirm |
| `orders.broker_vs_own` | `active_order_count` 与 `own_pending` 比较：相等 → pass，券商多 → confirm（#666），券商少 → fail，任一为 None → unknown |
| `maintainer.confirmation` | 给出 `confirmed_by` → pass，否则 confirm |

- `review(inputs, *, confirmed_by: str | None, now: datetime) -> KillReview`

- [ ] **Step 1: 写失败测试：**
  - `test_all_clear_with_confirmation_is_ready`
  - `test_without_confirmation_needs_confirmation`
  - `test_heartbeat_timeout_reason_cleared_by_fresh_heartbeats`
  - `test_heartbeat_timeout_reason_not_cleared_when_stale`
  - `test_reconcile_reason_follows_reconcile_item`
  - `test_fuse_reason_requires_confirmation`
  - `test_broker_vs_own_orders`
  - `test_missing_state_db_is_unknown`
  - `test_redis_unavailable_is_blocked_not_crash`
  - `test_item_ids_stable_and_ordered`
  - `test_gates_state_reports_three_gates`
- [ ] **Step 2:** 运行 `uv run pytest tests/live/test_kill_review.py -q`，确认失败。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 测试通过。
- [ ] **Step 5: Commit：** `feat(live): 停机闸解除前核对的结构化判定（#662）`

---

### Task 2: 采集与 `kill.py review`

**Files:**
- Modify: `src/qsys/live/kill_review.py`：`gather(transport, cfg, *, local_kill_path, state_path) -> ReviewInputs`
- Modify: `scripts/kill.py`：新增 `review` 动作
- Test: `tests/live/test_kill_review.py`（采集）、`tests/scripts/test_kill_cli.py`（命令）

**Interfaces:**
- `gather`：
  - Redis 读取 `control:kill`、`{heartbeat}:agent`、`{heartbeat}:market`、`cfg.streams.account`；任一读取异常 → `redis_ok=False`，相应字段为 None。
  - 本地 KILL 用 `state_binding.local_kill_path`。
  - 状态库 `LiveStore(state_path(path), read_only=True)`：`load()` 得到 portfolio，`pending_orders()` 得到 own_pending；异常写入 `state_error`。
- `kill.py review live`：
  - 参数：`--json`、`--confirmed-by NAME`、`--record PATH`、`--state PATH`（默认 `var/live-state.sqlite3`）；
  - `review shadow` 输出“只支持 live”，返回 2；
  - `--record` 先写临时文件，再 `os.replace`。

- [ ] **Step 1: 写失败测试：**
  - `test_gather_reads_memory_transport_and_state_db`
  - `test_review_uses_structured_inputs_only`
  - `test_kill_review_cli_json_and_exit_codes`：0 / 1 / 3。
  - `test_kill_review_cli_record_atomic`
  - `test_kill_review_is_read_only`：运行前后 Redis 键、KILL 文件、状态库字节都不变。
  - `test_review_shadow_not_supported`
- [ ] **Step 2–4:** 先失败；实现后运行 `uv run pytest tests/live tests/scripts -q`，全部通过。
- [ ] **Step 5: Commit：** `feat(ops): kill.py 增加只读的 review 动作，输出机器可读核对结果（#662）`

---

### Task 3: 收尾

- [ ] 全量 `uv run pytest -q`；运行 `check_repo_policy.py`。
- [ ] 在 `docs/reference/live-commissioning-runbook.md` 的解除步骤中，加入 `kill.py review live --confirmed-by <维护者>` 作为解除前必看项。文档改动，不改变门禁。
- [ ] 推送分支；在 #662 记录，并请维护者决定：是否把 `review` 的 ready 作为 `resume live` 的硬门槛。
