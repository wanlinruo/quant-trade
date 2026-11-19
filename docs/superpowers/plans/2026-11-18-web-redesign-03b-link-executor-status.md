# Web 改版 03b：执行器状态进心跳与链路时间序列 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 两项改动：
- 执行网关的 agent 心跳带上 QMT 内置执行器的状态：epoch、最近心跳时刻、启动时刻、请求序号、环形缓冲是否已满；
- 健康观察每轮把 agent、market、执行器的心跳年龄与新鲜度写入链路采样表，保留 48 小时，供今日运行与链路页的时间轴使用。

**Architecture:**
- `messages.Heartbeat` 增加可选字段，默认 None 表示未知。pydantic 默认忽略未知字段，所以旧读者不受影响（N-1 安全）。
- 适配器新增可选能力 `executor_status() -> ExecutorStatus | None`：只有 `QmtIpcAdapter` 实现，读共享内存；其他适配器没有这个方法。
- `ExecutionAgent.beat()` 用 `getattr` 探测这个能力。任何异常都只把字段置 None 并记日志，不影响心跳发布。
- `HealthStore` 新增表 `link_samples` 与写入、读取、清理方法；健康观察主循环每轮采样一次。

**Tech Stack:** Python 3.11、pydantic 2、sqlite3、pytest。

**Spec:**
- 设计文档 §8.1 链路时间轴、§12 新增数据需求（健康观察链路状态时间序列）
- 总路线 v2 第 4 节计划 03（执行端心跳带 QMT 执行器状态，健康观察记入链路时间序列）
- AGENTS.md：§2.1“新增默认关闭配置字段须兼容 N-1”、§2.1 #666“共享隧道大键读取须节流”

## Global Constraints

- **不改交易行为：** 执行器新鲜度判定（`EXECUTOR_FRESH_MS`、发送前拒绝）不变；心跳只增加观测字段。
- **时间口径：**
  - 执行器心跳与启动时刻按网关本机时钟换算为本地时刻，与心跳 `at` 同一时钟，读者用 `at - executor_heartbeat_at` 得到执行器心跳年龄，不跨机器比较；
  - 共享内存里为 0 的时刻视为未知，记为 None，不当 1970 年；
  - epoch 为空串时同样记为 None。
- **读不到即未知：** 执行器状态读取异常或适配器不支持时，全部字段为 None，不写 0 或 False。
- **链路采样：**
  - 每轮每个来源一行，来源为 `agent`、`market`、`executor`；
  - 字段：采样时刻（UTC）、来源、最近心跳时刻（UTC，可空）、年龄秒（可空）、是否新鲜（可空）、阈值秒（可空）、细节 JSON；
  - 新鲜度沿用 `preflight_cli.heartbeat_freshness`；
  - 执行器新鲜阈值取 `EXECUTOR_FRESH_MS / 1000`；
  - 读不到心跳的来源记一行，年龄与新鲜度为 None，不能缺行（缺行会被误读为“没有采样”）。
- **Redis 读取量：** 每轮只读两个小键（agent、market 心跳），频率与现有采样循环相同，不读大键（#666）。
- **留存：**
  - 写入时清理 48 小时前的采样，只删本表中健康观察自己派生的采样；
  - 清理与写入在同一事务中；
  - 写失败只记日志，不影响主循环。
- **只读读者：** `HealthStoreReader.link_samples(since)` 只读；表不存在时（N-1 旧库）返回空列表，不报错。

## Review Focus

1. **旧版 Linux 读者解析带新字段的心跳：** 能解析，新字段被忽略。归 Task 1 `test_old_reader_ignores_new_heartbeat_fields`。
2. **执行器从未启动**（共享内存全零）：字段为 None，不是 1970 年或 0。归 Task 1 `test_zeroed_executor_memory_is_unknown`。
3. **读取执行器状态抛异常：** 心跳照常发布，执行器字段为 None。归 Task 1 `test_executor_status_failure_never_blocks_heartbeat`。
4. **Redis 不可达：** 三个来源各记一行未知，主循环不中断。归 Task 2 `test_redis_down_records_unknown_rows`。
5. **旧库没有新表：** 只读读者返回空列表。归 Task 2 `test_reader_on_old_db_returns_empty`。

---

### Task 1: 执行器状态进心跳

**Files:**
- Modify:
  - `src/qsys/live/messages.py`：`Heartbeat` 增加 `executor_epoch: str | None`、`executor_heartbeat_at: datetime | None`、`executor_started_at: datetime | None`、`executor_latest_seq: int | None`、`executor_ring_full: bool | None`，默认均为 None
  - `src/qsys/agent/qmt/adapter.py`：`ExecutorStatus`（dataclass，字段同上）与 `QmtIpcAdapter.executor_status()`
  - `src/qsys/agent/execution_agent.py`：`beat()` 填入执行器字段
- Test: `tests/agent/qmt/test_qmt_adapter.py`、`tests/agent/test_execution_agent.py`、`tests/live/test_messages.py`（如无则新建 `tests/live/test_heartbeat_executor_fields.py`）

- [ ] **Step 1: 写失败测试：**
  - `test_executor_status_reads_shared_memory`
  - `test_zeroed_executor_memory_is_unknown`
  - `test_beat_carries_executor_status`
  - `test_beat_without_executor_capability_leaves_fields_none`
  - `test_executor_status_failure_never_blocks_heartbeat`
  - `test_old_reader_ignores_new_heartbeat_fields`：用不含新字段的旧模型副本解析新心跳 JSON。
- [ ] **Step 2:** 运行，确认失败。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 运行 `uv run pytest tests/agent tests/live -q`，全部通过。
- [ ] **Step 5: Commit：** `feat(agent): 执行网关心跳带上 QMT 执行器状态（#662）`

---

### Task 2: 链路采样表与健康观察采样

**Files:**
- Modify:
  - `src/qsys/ops/health.py`：`HealthStore` 建表 `link_samples`（含索引 `(source, sampled_at_utc)`），新增 `record_link_samples(rows, *, now)`（写入并清理 48 小时前数据）与 `link_samples(since)`；`HealthStoreReader` 只读读取，表不存在时返回空列表
  - `src/qsys/ops/health_observer_cli.py`：新增 `link_rows(transport, prefix, now) -> list[LinkSample]`，主循环每轮写入
- Test: `tests/ops/test_health_link_samples.py`

**Interfaces:**
- `LinkSample`（dataclass）：
  - `source: Literal["agent","market","executor"]`
  - `beat_at: datetime | None`
  - `age_s`、`threshold_s`：`float | None`
  - `fresh: bool | None`
  - `details: dict`：例如 agent 的 trading_mode / local_kill_engaged、market 的 order_book_ratio、executor 的 epoch / latest_seq / ring_full
- `link_rows(transport, prefix, now)`：
  - 读 agent、market 心跳，派生 executor 行；
  - 读取异常时三行都为未知；
  - executor 行在 agent 心跳缺失或执行器字段为 None 时为未知。

- [ ] **Step 1: 写失败测试：**
  - `test_link_rows_from_heartbeats`
  - `test_executor_age_uses_agent_clock`
  - `test_redis_down_records_unknown_rows`
  - `test_store_records_and_prunes_48h`
  - `test_reader_on_old_db_returns_empty`
  - `test_record_failure_does_not_raise`
- [ ] **Step 2–4:** 先失败；实现后运行 `uv run pytest tests/ops -q`，全部通过。
- [ ] **Step 5: Commit：** `feat(ops): 健康观察记录链路采样时间序列（#662）`

---

### Task 3: 收尾

- [ ] 全量 `uv run pytest -q`；运行 `check_repo_policy.py`。
- [ ] 推送分支 `feat/link-executor-status`；在 #662 记录。
- [ ] **发布说明（另行请示）：**
  - `messages.py` 被多个组件共享，按 deployment.toml 会联动发布；
  - Windows 执行网关收盘后发布，不在交易时段重启网关；
  - health-observer 可单独发布，新表由其自身建立；
  - Linux 侧读者无须先发布：新字段被旧读者忽略。
