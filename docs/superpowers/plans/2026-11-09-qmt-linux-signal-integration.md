# Linux 策略信号接入大 QMT 执行器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Windows `ExecutionAgent` 的券商端从 `XtQuantAdapter` 换成“共享内存 + 大 QMT 内置薄执行器 v2”，Linux Core 与 Redis 协议不变，达到可做现场验证与授权小额真实运行的状态。

**Architecture:** 网关侧（`src/qsys/agent/qmt/`，Windows session 1 计划任务）沿用已有 `AsyncOrderDispatcher` + `dispatch.sqlite3`，新增 `QmtIpcPort`（发指令、收回执）、`QmtIpcAdapter`（读报告环，供 `FillTracker` 与账户快照）、`UnknownReconciler`（§4.4 自动消解）。QMT 内是一份只用标准库的执行器源码（`executor_source.py`），安装工具写入密钥、账户与上限字面量后经编辑器安装。两端共享一份帧/内存布局常量，测试断言两边一致。

**Tech Stack:** Python 3.11+（网关）、QMT 内置 Python（执行器，仅标准库）、`mmap`（Windows `tagname`）、SQLite、Redis Streams、pytest。

**Spec:** `docs/superpowers/specs/2026-11-09-qmt-linux-signal-integration-design.md`

## Global Constraints

- Linux 业务代码不改；Linux 只新增默认关闭的配置项（N-1 私有配置缺字段时等同关闭，#603）。
- 北交所（`.BJ`、4/8/92 开头）与科创板（68 开头）禁止买入；创业板（30 开头）允许；卖出、撤单不受白名单限制。
- 执行器字面量：单笔买入名义金额 ≤ 10,000 元；当日累计买入名义金额 ≤ 10,000 元；买入数量为 100 的整数倍。
- 买入只收有限正限价；卖出允许限价或市价；市价报价类型未经探针确认前执行器一律拒绝市价单。
- 状态不明不重发、不在未满足 §4.4 全部条件时释放预留。
- 账户号、密钥不进 Git、Issue、日志、异常消息或探针输出；仓库内执行器源码 `ENABLED=False` 且无密钥。
- 网关心跳超时不宽于 10 秒；指令传输有效期 5 秒。
- 每个现场步骤（Task F0/F2/F4/F5）都需维护者对具体窗口的明确授权；`继续` 不构成授权。
- 新增生产入口脚本加入 `tests/scripts/test_scripts_import.py`；提交遵循 Conventional Commits，正文中文。

## Review Focus

1. 网关重启后 Redis 重投同一 `client_order_id`，而执行器此前已调用 passorder → 不得二次下单（账本 replay + 执行器按 remark 查单）。Task 4、Task 8 有测试。
2. 09:25 竞价后的指令在队列里滞留超过 `expires_at` 才到执行器 → 执行器回 `rejected:command_expired`，网关发拒单，不产生 unknown。Task 2、Task 4 有测试。
3. QMT 盘中自行重建（执行器实例纪元变化）时有在途指令 → 在途转 unknown、不重发，且该段时间内“查不到”不得用于自动释放。Task 7 有测试。
4. 部分成交后撤单（状态 52/53/55）→ 回报成交量与剩余量正确，Core 只释放未成交部分。Task 6 有测试。
5. 卖出市价单在 `PRICE_TYPE_MARKET` 未配置时 → 明确拒绝（`market_price_type_unverified`），不调用 passorder。Task 2 有测试。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/qsys/agent/qmt/__init__.py` | 包标记 |
| `src/qsys/agent/qmt/protocol.py` | 帧格式、HMAC、内存布局常量、回执状态；仅标准库 |
| `src/qsys/agent/qmt/executor_source.py` | QMT 内执行器 v2 源码（自包含、仅标准库、默认关闭） |
| `src/qsys/agent/qmt/memory.py` | 网关侧共享内存：请求/回执、心跳字、KILL 字、报告环读取与游标 |
| `src/qsys/agent/qmt/port.py` | `QmtIpcPort(AsyncDispatchPort)`、`ExecutorRejected` |
| `src/qsys/agent/qmt/adapter.py` | `QmtIpcAdapter`：报告 → `BrokerOrder`/`BrokerTrade`/`BrokerAccount`；`cancel_all` |
| `src/qsys/agent/qmt/reconcile.py` | `UnknownReconciler`（§4.4） |
| `src/qsys/agent/async_dispatch.py`（改） | 生产工厂 `open_qmt_dispatcher`、市价卖出、执行器拒绝、`resolve` |
| `src/qsys/agent/dispatch_store.py`（改） | schema v2：新状态与转换 |
| `src/qsys/agent/execution_agent.py`（改） | 异步模式禁用 commissioning；每轮心跳/KILL 同步与对账 |
| `src/qsys/live/config.py`、`src/qsys/live/runtime.py`、`src/qsys/live/live_broker.py`（改） | `[safety] dispatch_tracking` 默认关闭；开启时 Core 接收 `evt:dispatch_status` |
| `scripts/run_qmt_agent.py` | Windows 生产入口（替代写死 `XtQuantAdapter` 的 `run_agent.py`） |
| `scripts/prepare_qmt_signal_executor.py` | 安装工具：stage/verify，生成私有执行器与网关私有配置 |
| `deploy/deployment.toml`（改） | `windows-agent` 组件加入 `scripts/run_qmt_agent.py` |
| `tests/agent/qmt/test_*.py` | 各任务测试；`tests/agent/qmt/fake_qmt.py` 为 QMT 替身 |
| `docs/reference/qmt-signal-executor.md` | 运行手册与现场步骤记录 |

---

### Task 0（文档探针，无需授权）: 市价报价类型与 remark 长度的官方口径

**Files:**
- Create: `docs/reference/qmt-signal-executor.md`（“正式接口事实”一节）

- [ ] **Step 1:** 读迅投官方 `passorder` 文档（dict.thinktrader.net 内置 Python 交易函数页），记录：沪、深各自“市价”可用 `prType` 值及含义；`userOrderId` 长度/字符集说明；`m_strRemark` 对应关系。每条附 URL 与读取日期。
- [ ] **Step 2:** 对照现有 `client_order_id` 形状 `q{yymmdd}{seq:03d}{12hex}`（22 字节，`src/qsys/core/order_ids.py:9-13`），写明文档是否覆盖 22 字节；文档未写明的项标为“待 F0 现场确认”，不推定。
- [ ] **Step 3:** Commit `docs(qmt): 记录大 QMT 市价报价类型与委托备注口径`。

### Task 1: 协议模块 `protocol.py`

**Files:**
- Create: `src/qsys/agent/qmt/__init__.py`, `src/qsys/agent/qmt/protocol.py`
- Test: `tests/agent/qmt/test_protocol.py`

**Interfaces:**
- Produces:
  - 常量：`PROTOCOL = 'qsys.qmt-signal.v1'`，`MAP_SIZE = 262144`，`REQUEST_OFFSET = 0`，`RESPONSE_OFFSET = 4096`，`CONTROL_OFFSET = 8192`（网关心跳 u64、KILL u32、已消费报告序号 u64），`EXECUTOR_OFFSET = 8448`（执行器纪元 32hex、执行器心跳 u64、报告最新序号 u64、环满标志 u32），`COORDINATOR_MARKER_OFFSET = 8704`，`SCHEDULED_MARKER_OFFSET = 9216`，`RING_OFFSET = 16384`，`RING_SLOT_SIZE = 1024`，`RING_SLOTS = 240`，`FRAME_MAX = 3800`，`TRANSPORT_TTL_MS = 5000`。
  - `KINDS = ('place', 'cancel', 'cancel_all', 'snapshot', 'ping')`；`RECEIPTS = ('accepted', 'rejected', 'duplicate')`。
  - `canonical(value) -> bytes`、`sign(value: dict, key: bytes) -> str`、`verify(obj: dict, key: bytes) -> bool`、`build_request(*, run_id, seq, kind, command: dict, attempt_id, session_nonce, now_ms, key) -> bytes`、`parse_receipt(raw: bytes, key: bytes) -> dict`（签名不符抛 `ValueError('receipt_signature')`）。

- [ ] **Step 1: Write the failing test**

```python
def test_request_roundtrip_and_tamper_detection():
    key = bytes(32)
    raw = protocol.build_request(run_id='a'*32, seq=1, kind='place',
        command={'client_order_id': 'q261109001abcdef012345'}, attempt_id='b'*32,
        session_nonce='c'*32, now_ms=1_000, key=key)
    obj = json.loads(raw)
    assert obj['expires_at_ms'] - obj['issued_at_ms'] == protocol.TRANSPORT_TTL_MS
    assert protocol.verify(dict(obj), key)
    obj['seq'] = 2
    assert not protocol.verify(obj, key)

def test_layout_regions_do_not_overlap():
    assert protocol.RING_OFFSET + protocol.RING_SLOTS * protocol.RING_SLOT_SIZE <= protocol.MAP_SIZE
    assert protocol.FRAME_MAX < protocol.RESPONSE_OFFSET - protocol.REQUEST_OFFSET - 8
```

- [ ] **Step 2:** `uv run pytest tests/agent/qmt/test_protocol.py -v` → FAIL（模块不存在）。
- [ ] **Step 3:** 实现；`canonical` 与已有执行器相同（`sort_keys`、紧凑分隔符、`ensure_ascii`、`allow_nan=False`）。
- [ ] **Step 4:** 同上命令 → PASS。
- [ ] **Step 5:** Commit `feat(qmt): 新增大 QMT 信号通道协议`。

### Task 2: QMT 内执行器 v2 `executor_source.py`

**Files:**
- Create: `src/qsys/agent/qmt/executor_source.py`
- Create: `tests/agent/qmt/fake_qmt.py`
- Test: `tests/agent/qmt/test_executor_source.py`

**Interfaces:**
- Consumes: Task 1 常量（本文件复制同名常量，不 import `qsys`；测试断言逐一相等）。
- Produces:
  - 安装字面量：`RUN_ID=''`、`CLIENT_BUILD=''`、`ACCOUNT_ID=''`、`KEY_HEX=''`、`ENABLED=False`、`MAX_ORDER_NOTIONAL=10000.0`、`MAX_DAILY_BUY_NOTIONAL=10000.0`、`PRICE_TYPE_MARKET_SH=None`、`PRICE_TYPE_MARKET_SZ=None`、`GATEWAY_STALE_MS=10000`。
  - `class Executor(run_id, key, api, ring, control, clock_ms)`；`receive(C, raw: bytes) -> bytes`（签名回执，`status ∈ RECEIPTS`，含 `reason`、`attempt_id`、`command_sha256`、`epoch`）；`record_order/record_deal/record_error`（回调入口）；`epoch: str`（`init` 时生成的 32hex）。
  - 拒绝原因字符串（网关与测试共用）：`signature`、`transport_expired`、`command_expired`、`market_blocked`、`qty_lot`、`order_notional`、`daily_buy_limit`、`market_price_type_unverified`、`gateway_stale`、`kill_engaged`、`ring_full`、`attempt_conflict`、`cancel_target_absent`。
  - 模块级 `init/on_timer/order_callback/deal_callback/orderError_callback/stop/handlebar`，结构沿用 `docs/reference/qmt-authorized-order-executor.py`。

- [ ] **Step 1: Write the failing tests**（`fake_qmt.py` 提供 `FakeQmtApi`：记录 `passorder/cancel` 调用，`get_trade_detail_data` 返回可设置的 ORDER/DEAL/POSITION/ACCOUNT 对象）

```python
@pytest.mark.parametrize('symbol,side,reason', [
    ('688001.SH', 'buy', 'market_blocked'), ('920001.BJ', 'buy', 'market_blocked'),
    ('430001.BJ', 'buy', 'market_blocked')])
def test_board_whitelist_rejects_buys(symbol, side, reason): ...

def test_chinext_buy_allowed_and_star_sell_allowed(): ...   # 300750.SZ buy → accepted；688001.SH sell → accepted
def test_order_notional_cap_10000(): ...                     # 100×100.01 → rejected order_notional
def test_daily_buy_cap_accumulates_and_resets_next_day(): ...
def test_market_sell_rejected_until_price_type_configured(): ...  # passorder 未被调用
def test_expired_command_rejected_not_placed(): ...         # Review Focus 2
def test_existing_remark_reports_instead_of_reordering(): ...     # Review Focus 1
def test_same_attempt_returns_duplicate_receipt(): ...
def test_gateway_heartbeat_stale_blocks_buy_allows_sell_and_cancel(): ...
def test_kill_word_blocks_buy_and_cancels_all(): ...
def test_ring_full_blocks_new_buys_without_dropping_reports(): ...
def test_only_own_remark_shape_reported_and_no_account_value(): ...
def test_constants_match_protocol_module(): ...
def test_checked_in_source_is_inert(): ...   # ENABLED False, KEY_HEX '', ACCOUNT_ID ''
```

- [ ] **Step 2:** `uv run pytest tests/agent/qmt/test_executor_source.py -v` → FAIL。
- [ ] **Step 3:** 实现。要点：先验签与传输有效期，再查 attempt 去重，再按指令类型检查；`place` 在调用 `passorder` 前先查 ORDER 中 `m_strRemark == client_order_id`；当日累计买入在调用 `passorder` 前扣减（异常也不回补，宁可少开）；KILL 字置位时本 tick 先 `cancel` 全部非终态委托再处理请求；报告环写入槽 `seq % RING_SLOTS`，若 `最新序号 - 已消费序号 >= RING_SLOTS` 置环满标志并拒绝买入；市价单用 `PRICE_TYPE_MARKET_SH/SZ`，为 `None` 时拒绝。
- [ ] **Step 4:** 同上命令 → PASS。
- [ ] **Step 5:** Commit `feat(qmt): 新增通用薄执行器 v2（默认关闭）`。

### Task 3: 网关共享内存 `memory.py`

**Files:**
- Create: `src/qsys/agent/qmt/memory.py`
- Test: `tests/agent/qmt/test_memory.py`

**Interfaces:**
- Produces: `class SharedMemory(buffer)`，`open_named(run_id: str) -> SharedMemory`（Windows `mmap.mmap(-1, MAP_SIZE, tagname='Local\\qsys_qmt_signal_' + run_id)`；非 Windows 抛 `RuntimeError`）；方法 `write_coordinator_marker(run_id)`、`scheduled(run_id) -> bool`、`beat(now_ms)`、`set_kill(engaged: bool)`、`executor_state() -> ExecutorState(epoch, heartbeat_ms, latest_seq, ring_full)`、`exchange(raw: bytes, timeout_s: float, sleep) -> bytes | None`（写请求代、等同代回执）、`read_reports() -> list[dict]`（从已消费序号+1 读到最新序号并推进游标）。测试用 `SharedMemory(bytearray(MAP_SIZE))`。

- [ ] **Step 1:** 测试：`test_exchange_returns_only_matching_generation`、`test_read_reports_advances_cursor_once`、`test_reports_overwritten_beyond_ring_are_detected`（序号跳跃 → 抛 `ReportGap`，网关停新开仓并告警）、`test_kill_and_beat_words_visible_to_executor_reader`。
- [ ] **Step 2:** 运行 → FAIL。
- [ ] **Step 3:** 实现（u32/u64 小端；读写顺序与执行器一致：先数据后代号）。
- [ ] **Step 4:** 运行 → PASS。
- [ ] **Step 5:** Commit `feat(qmt): 网关侧共享内存与报告环游标`。

### Task 4: 发送端口 `QmtIpcPort` 与账本状态扩展

**Files:**
- Create: `src/qsys/agent/qmt/port.py`
- Modify: `src/qsys/agent/dispatch_store.py`（schema v2）、`src/qsys/agent/async_dispatch.py`
- Test: `tests/agent/qmt/test_port.py`，扩展 `tests/agent/test_async_dispatch.py`

**Interfaces:**
- Consumes: Task 1 `build_request/parse_receipt`，Task 3 `SharedMemory.exchange`。
- Produces:
  - `class ExecutorRejected(Exception)`，属性 `reason: str`。
  - `class QmtIpcPort`：`__init__(memory, *, run_id, key, clock_ms, sleep, receipt_timeout_s=3.0)`；`send(envelope) -> DispatchAcknowledgement`（`accepted`/`duplicate` → ACK，`receiver_instance = 'qmt:' + epoch`；`rejected` → 抛 `ExecutorRejected`；无回执/签名不符 → 抛 `TimeoutError`/`ValueError`，由 dispatcher 记 unknown）；`request(kind, command) -> dict`（供 cancel_all/snapshot/ping）；`close()`。
  - `DispatchStore` 绑定 `schema_version=2`；状态新增 `resolved_present`、`resolved_absent`；`update()` 允许 `dispatching → rejected_before_send`；新增 `resolve(client_order_id, *, outcome: Literal['present','absent'], reason: str, at) -> dict`（仅 `unknown → resolved_*`）。`has_unresolved()` 不变。
  - `AsyncOrderDispatcher.dispatch` 捕获 `ExecutorRejected` → `store.update(state='rejected_before_send', reason='executor:'+reason)` → 发 `RejectEvent`；买入仍只收有限正限价，卖出允许 `market`。
  - `AsyncOrderDispatcher.resolve(client_order_id, outcome, reason)`：`absent` 发 `RejectEvent(reason='reconciled_absent:'+reason)`；`present` 只发 `DispatchStatusEvent`（真实状态由回报推进）。

- [ ] **Step 1:** 测试：`test_executor_rejection_becomes_reject_event_not_unknown`、`test_missing_receipt_becomes_unknown_and_not_resent`、`test_duplicate_receipt_is_ack`、`test_market_sell_dispatched_market_buy_rejected`、`test_redelivered_command_after_restart_replays_without_send`（Review Focus 1，端口调用计数为 1）、`test_v1_store_is_refused`、`test_resolve_only_from_unknown`。
- [ ] **Step 2:** 运行 → FAIL。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** `uv run pytest tests/agent -v` → PASS（含既有异步账本测试）。
- [ ] **Step 5:** Commit `feat(qmt): 发送端口与执行器拒绝、对账状态`。

### Task 5: 生产工厂与 Linux 默认关闭开关

**Files:**
- Modify: `src/qsys/agent/async_dispatch.py`（`open_qmt_dispatcher`）、`src/qsys/live/config.py`、`src/qsys/live/live_broker.py:66`、`src/qsys/live/runtime.py:165-172`
- Test: `tests/agent/test_async_dispatch.py`、`tests/live/test_live_broker.py`、`tests/live/test_config.py`

**Interfaces:**
- Produces:
  - `open_qmt_dispatcher(*, enabled: bool, root: Path, transport: Transport, streams: StreamConfig, port: AsyncDispatchPort, account_scope: str, session_id: str, strategy_id: str, clock) -> AsyncOrderDispatcher`：`enabled` 非 True 抛错；`account_scope` 形如 `qmt:` + 账户号 SHA256 前 16 位；取单实例锁后打开 `root/dispatch.sqlite3` 并 `recover()`；目录须私有（沿用已有 `_private_root` 检查）。
  - `SafetyConfig.dispatch_tracking: bool = False`（`[safety] dispatch_tracking`；缺省 False）。
  - `LiveBroker(receive_dispatch_status=True)` 允许非验证传输，但要求 `strategy_id` 非空；`runtime.py` 用 `cfg.safety.dispatch_tracking` 传入。

- [ ] **Step 1:** 测试：`test_qmt_factory_requires_explicit_enable`、`test_qmt_factory_rejects_shared_root`、`test_missing_dispatch_tracking_defaults_off_for_n_minus_1_config`、`test_live_broker_production_transport_accepts_dispatch_status_when_enabled`、`test_dispatch_status_never_releases_pending`（既有语义回归）。
- [ ] **Step 2:** 运行 → FAIL。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** `uv run pytest tests/agent tests/live -v` → PASS。
- [ ] **Step 5:** Commit `feat(live): 默认关闭的异步发送跟踪开关与 QMT 生产工厂`。

### Task 6: 回报适配器 `QmtIpcAdapter`

**Files:**
- Create: `src/qsys/agent/qmt/adapter.py`
- Test: `tests/agent/qmt/test_adapter.py`

**Interfaces:**
- Consumes: Task 3 `read_reports`，Task 4 `QmtIpcPort.request`。
- Produces: `class QmtIpcAdapter(memory, port)` 实现 `BrokerAdapter` 的 `query_trades/query_orders/query_account/query_account_details/query_active_order_count/cancel_all/find_order`；`place_order` 抛 `RuntimeError('async_only')`。映射：`m_strRemark→client_order_id`、`m_strOrderSysID→broker_order_id`、`m_strTradeID→traded_id`、`m_nOrderStatus→status`、`m_nVolumeTotalOriginal→ordered_qty`、`m_nVolumeTraded→traded_qty`、方向由 `m_nOffsetFlag`（48 买 / 49 卖，F0 现场核对）；成交按 `traded_id` 去重。账户快照来自 `snapshot` 的 ACCOUNT/POSITION 行。

- [ ] **Step 1:** 测试：`test_callback_and_query_same_trade_counted_once`、`test_partial_fill_then_cancel_maps_52_53`（Review Focus 4，与 `FillTracker` 联测剩余量）、`test_foreign_remark_ignored`、`test_place_order_is_async_only`、`test_account_snapshot_has_no_account_id`。
- [ ] **Step 2:** 运行 → FAIL。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 运行 → PASS。
- [ ] **Step 5:** Commit `feat(qmt): 报告环到券商回报的适配器`。

### Task 7: 状态不明自动消解 `UnknownReconciler`

**Files:**
- Create: `src/qsys/agent/qmt/reconcile.py`
- Test: `tests/agent/qmt/test_reconcile.py`

**Interfaces:**
- Consumes: Task 4 `DispatchStore`/`AsyncOrderDispatcher.resolve`，Task 6 `QmtIpcAdapter.query_orders`，Task 3 `executor_state()`。
- Produces: `class UnknownReconciler(dispatcher, adapter, memory, *, grace_s=60.0, clock)`；`run_once() -> list[tuple[str, str]]`（`(client_order_id, outcome)`）。

规则（逐字来自 spec §4.4）：查到 → `present`；查不到且 `now > expires_at + grace`、执行器纪元自该指令 `created_at` 前至今未变、执行器心跳连续、两次相隔 ≥5 秒的查询均查不到 → `absent`；任一条件不满足 → 保持 unknown。判定依据写入 `reason`（两次查询时刻、纪元、有效期）。

- [ ] **Step 1:** 测试：`test_present_order_resolves_without_resend`、`test_absent_requires_expiry_plus_grace`、`test_absent_requires_two_consistent_queries`、`test_epoch_change_keeps_unknown`（Review Focus 3）、`test_heartbeat_gap_keeps_unknown`、`test_query_failure_keeps_unknown`、`test_unresolved_blocks_new_buys_until_resolved`。
- [ ] **Step 2:** 运行 → FAIL。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 运行 → PASS。
- [ ] **Step 5:** Commit `feat(qmt): 状态不明委托的自动对账消解`。

### Task 8: Agent 集成、生产入口与安装工具

**Files:**
- Modify: `src/qsys/agent/execution_agent.py`（异步模式下 `strategy_id == 'commissioning'` 直接拒绝，原因 `commissioning_disabled_in_async_mode`；每轮 `run_once` 前 `memory.beat()`、按 Redis `control:kill` 与本地 KILL 同步 `memory.set_kill()`、调用 `reconciler.run_once()`）
- Create: `scripts/run_qmt_agent.py`、`scripts/prepare_qmt_signal_executor.py`
- Modify: `deploy/deployment.toml`（`windows-agent.paths` 加 `scripts/run_qmt_agent.py`）、`tests/scripts/test_scripts_import.py`
- Test: `tests/agent/test_execution_agent.py`、`tests/scripts/test_prepare_qmt_signal_executor.py`、`tests/agent/qmt/test_end_to_end.py`

**Interfaces:**
- Produces:
  - `run_qmt_agent.py --config <deploy.local.toml> --qmt-private <dir>`：私有目录含 `key.hex`、`account.txt`、`run.json`（run_id、client_build）；复用 `run_agent.py` 的主循环、StallGuard、单实例锁、心跳与 `SafetyPolicy`；先创建映射并写协调标记，再等待 scheduled marker（有界），之后才消费 `cmd:orders`。
  - `prepare_qmt_signal_executor.py stage <dir> --run-id --account-file --market-price-type-sh --market-price-type-sz` / `verify <dir>`：生成 `executor-enabled.py`（填字面量）、`executor-default.py`（仓库原样）、网关私有文件；不打印账户与密钥；市价类型参数缺省为空（保持拒绝市价）。

- [ ] **Step 1:** 端到端测试（`test_end_to_end.py`）：内存 Redis 替身 + `LiveBroker(receive_dispatch_status=True)` → `ExecutionAgent` → `QmtIpcPort` → 同进程 `Executor`（`FakeQmtApi`）→ 回调写环 → `QmtIpcAdapter` → `FillTracker` → Core 收到 `FillEvent` 且 pending 结清；另测 `test_commissioning_rejected_in_async_mode`、`test_kill_sync_sets_word_and_executor_cancels`、`test_stage_never_prints_secrets`、`test_checked_in_executor_equals_default_artifact`。
- [ ] **Step 2:** 运行 → FAIL。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** `uv run pytest -q` 全量 → PASS；`python3 scripts/check_secrets.py` → ok。
- [ ] **Step 5:** Commit `feat(qmt): 大 QMT 信号通道接入执行代理与安装工具`。
- [ ] **Step 6:** squash 到 `test` → push → CI 与测试部署三方一致 → 建 `test -> main` PR（代行自审如实标注）→ 合并 → 对齐核验。`windows-agent` 生产组件**不**在本步发布。

---

## 现场任务（每项需维护者对具体窗口单独授权）

### Task F0: 授权探针窗口（remark 长度、市价类型）

- 准备：扩展 `docs/reference/qmt-authorized-order-executor.py` 的计划格式，允许 22 字节 `q…` 委托号与卖出步骤 `order_type='market'`（带 `price_type` 字面量），并补负例测试；其余语义不变。
- 用 11-09 授权执行器机制（不使用 v2）：① 510300.SH 限价挂单 100 份 + 撤单，`userOrderId` 用 22 字节 `q…` 形状，核对回报 `m_strRemark` 原样；② 卖出 11-09 买入的 510300.SH 100 份，`prType` 用 Task 0 记录的沪市市价类型，核对成交与状态。
- 结论写入 `qmt-signal-executor.md` 并更新 Task 2 的 `PRICE_TYPE_MARKET_SH`；深市市价类型另需一笔深市卖出确认，未确认前深市卖出只走限价（在执行器中保持拒绝深市市价）。
- 同时核查 QMT userdata 中策略源码目录的 DACL 与备份范围（spec §5）。

### Task F2: Windows 验证命名空间

- 安装执行器 v2（模拟模式实例，`ENABLED=True`，不切实盘），`run_qmt_agent.py` 走 `ValidationTransport`；合成意图全链：accepted/rejected/duplicate、报告环、对账；QMT 自重建与网关重启各一次；一次不重启全链彩排（AGENTS §2.1）。执行器实例设为“终端启动后自动运行”。

### Task F4: 故障组合

- 验证命名空间内：丢回执、回报延迟、网关/QMT/Windows 重启、重复与冲突消息、unknown 自动消解（present/absent 各一）、环满、心跳超时、KILL。Windows 整机重启需单独授权。

### Task F5: 小额真实运行

- 授权后：节点私有配置 `[strategy] follows_paper_account = "3c33f0fa…"`、`[safety] dispatch_tracking = true`、`[risk]` 保持默认（`enabled_capital=10000`）；执行器实盘实例字面量按 Global Constraints；启动 Live Core 与 `run_qmt_agent.py`；人工盯盘 ≥5 个交易日，每日核对券商资金/持仓/委托/成交。
