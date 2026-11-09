# Linux 策略信号接入大 QMT 执行器：设计

日期：2026-11-09。承接 [#601](https://github.com/wanlinruo/quant-trade/issues/601)。
前置证据：[11-09 授权真实窗口](../../reference/qmt-authorized-order-20261109-live.json)、
[r10 整机重启合成闭环](../../reference/qmt-windows-reboot-probe-20261108-r10.json)、
[异步发送隔离接线](../../reference/qmt-async-dispatch.md)。

## 1. 意图

维护者要求：**系统原先的设计不变，信号都由原系统下发；大 QMT 只保留执行器。**
原实盘链路的最后一段是 Windows `ExecutionAgent` → `XtQuantAdapter` → MiniQMT。
江海新版停用了外部 xtquant，这一段失效，但大 QMT 内置 Python 的 `passorder`/`cancel`/
查询/回调仍可用（11-09 真实挂单撤单与小额成交已证实）。本设计只替换这一段。

### 1.1 接入的策略（现场只读核对，2026-11-09）

- Linux 节点私有配置 `[safety] mode=live, orders_enabled=true`；`[strategy]` 承袭模拟账户
  `29056f15…`（`StructuralStrategy-模拟-09:25入场`，`structural_v1_2`）。该模拟账户状态为
  `ended`（10-31）；另有运行中的模拟账户 `3c33f0fa…`（同策略）。
- 维护者决定：Live Core 改为承袭运行中的模拟账户 `3c33f0fa…`（同策略 `structural_v1_2`）。
  这是节点私有配置变更，在第 5 步小额真实运行前实施并核对，不提前改动。
- 交易形态（原 `29056f15…` 模拟账户 10-19~10-21 记录）：09:25:10 竞价后对 3 只沪深主板股票各下限价买单
  （约 2000 股、每笔约 3.3 万元，合计约用满 10 万模拟资金；实盘按 §4.1 小资金上限由 Core
  仓位计算自然缩小）；卖出在 11:25 或 14:50 检查点以
  市价单下达，持有 1~2 个交易日。
- `qsys-live-core` 当前未运行；`qsys-shadow-core` inactive；`qsys-paper` 运行中。

## 2. 目标、非目标、反目标

**目标**

- Linux Core、`LiveBroker`、风控、账本、Redis 协议（`cmd:orders`、`cmd:control`、
  `evt:fills`/`evt:rejects`/`evt:order_status`/`evt:dispatch_status`、`state:*`、
  `control:kill`）保持原样；Linux 看到的仍是原来的 agent。
- Windows `ExecutionAgent` 保留全部门禁（北交所、KILL、过期、SafetyPolicy、账户白名单、
  心跳、幂等、`FillTracker` 回报格式），只把券商端换成大 QMT 通道（commissioning 见 §4.5）。
- 大 QMT 内只有一个通用薄执行器：收签名指令、调用原生接口、回写原生事实；不做策略计算。
- 无人值守：QMT 自重建、Windows 重启后执行器与网关自动恢复，不依赖 RDP 或界面点击。

**非目标**

- 不改策略、阈值、数据真值、风控规则；不恢复 xtquant/MiniQMT；不做多账户。
- 不跑影子并行阶段（维护者 2026-11-09 决定）。
- 不在本设计内决定生产激活（仍按 #137 单独审批）。

**反目标（即使跑通也算失败）**

- 状态不明的单被重发，或在未满足 §4.4 判定条件时被当作拒单释放资金/持仓预留。
- 执行器在网关失联、KILL 生效或超限时仍下新开仓单。
- 账户号、密钥进入 Git、Issue、日志或探针输出。
- 生产运行需要人登录 Windows 点按钮。

## 3. 总体结构

```
Linux Core（不改）
  LiveBroker ──cmd:orders / cmd:control──► Redis ◄──evt:* / state:*── 
                                                          │
Windows（session 1，计划任务，开机自启）                   │
  ExecutionAgent（保留全部门禁）                           │
    └─ AsyncOrderDispatcher + dispatch.sqlite3（已有）     │
         └─ QmtIpcPort（新）── 共享内存 ──┐               │
    └─ QmtIpcReports（新）◄── 报告环 ─────┤               │
                                          ▼
大 QMT（实盘模式，终端启动后自动运行）
  QSYS601_EXECUTOR v2（新，通用薄执行器）
    passorder / cancel / get_trade_detail_data / order/deal/orderError 回调
```

## 4. 组件

### 4.1 大 QMT 执行器 v2（`docs/reference` 候选 → 编辑器安装）

由 11-09 已验证的授权执行器演进，变化是把“写死的计划”换成“签名指令 + 硬上限”。

- **指令类型**：`place`（证券、方向、数量、限价、`client_order_id`、`expires_at`）、
  `cancel`（`client_order_id`）、`cancel_all`、`snapshot`（委托/成交/持仓/资金）、`ping`。
- **验证**：HMAC-SHA256（安装时写入执行器字面量的密钥，与网关私有配置相同）、
  单调递增序号、5 秒传输有效期 + 指令自身 `expires_at`、attempt 去重。
- **订单类型**：策略买入是限价单，卖出是市价单（见 §1.1）。执行器须支持市价卖出；
  沪深市价单在 `passorder` 中对应的报价类型须先探针确认（§8 第 0 步），不按文档推定。
  已有 `AsyncOrderDispatcher` 只收有限正限价，需放开市价卖出（买入仍只收限价）。
- **硬上限（执行器内独立执行，不依赖网关）**：
  - 只允许沪深 A 股/ETF 代码段，北交所及 4/8/92 开头一律拒绝买入（#589）；
    账户**无科创板权限**（68 开头拒绝买入），**有创业板权限**（30 开头允许）——维护者
    2026-11-09 确认。卖出、撤单不受白名单限制（合法减仓路径保留）。
  - 买入数量为 100 的整数倍；限价单；单笔名义金额与当日累计买入名义金额上限为安装字面量。
    小额真实运行沿用系统原有“小资金实盘”定义（`docs/deploy/live-trading.md` 第一阶段、
    `RiskConfig` 默认值）：启用资金 `enabled_capital=10,000` 元、`max_order_value=10,000` 元、
    单票 `max_position_weight=5%`、总敞口 `max_gross_exposure=10%`；人工盯盘至少 5 个交易日、
    每日核对券商资金/持仓/委托/成交后才评估扩容。执行器字面量取单笔 ≤10,000 元、
    当日累计买入 ≤10,000 元，与 Core 门禁一致，作为独立第二道防线。
  - 网关心跳（共享内存中网关每秒写入的序号）超过 10 秒未更新：拒绝新开仓，
    已在途单不动；卖出与撤单仍允许（与原 KILL 语义一致）。
  - KILL 字：网关把 Redis `control:kill`/本地 KILL 映射为共享内存中的停止字；
    置位时拒绝新开仓并执行 `cancel_all`。
- **账户所有权**：维护者确认该账户没有人工或其他系统的委托（2026-11-09），本系统是唯一交易
  所有者；`cancel_all` 与 `active_order_count` 沿用原 MiniQMT 的全账户语义。
- **防重**：下单前先按 `m_strRemark == client_order_id` 查 ORDER，已存在则只回报不重下
  （11-09 已用）。`client_order_id` 最长 24 字节，需先探针确认 `userOrderId` 长度与字符集
  （§8 第 0 步）。
- **回执语义**（实施者决定）：执行器对每条指令同步给出三种回执之一：`accepted`（已调用
  passorder/cancel）、`rejected`（硬上限、心跳、KILL、环满、过期等，未调用券商接口）、
  `duplicate`（同 attempt 已处理，返回原回执）。`rejected` 由网关按“发送前拒绝”处理，
  不产生 unknown；只有无回执或回执不符才是 unknown。
- **回报**：委托/成交/错误回调和 `snapshot` 结果写入报告环；只回写本系统
  `client_order_id` 形状的单（与原 `XtQuantAdapter` 的 remark 过滤一致），账户只记
  `account_match`。
- **报告环**：改为真正的环形缓冲——每槽带序号，网关写入“已消费到的序号”，执行器只
  覆盖已消费的槽；环满时执行器停止新开仓（不丢回报）。
- **调度**：`schedule_run` 周期回调（如 1 秒）处理请求与心跳；`init` 中 `set_account`。
- **默认关闭**：仓库中的版本 `ENABLED=False`、无密钥；只有安装工具生成的私有版本带密钥与上限。

### 4.2 Windows 网关：`QmtIpcPort` 与 `QmtIpcReports`（`src/qsys/agent/`）

- `QmtIpcPort` 实现已有 `AsyncDispatchPort.send()`：写请求帧、等执行器回执（绑定 attempt
  与 SHA）→ `DispatchAcknowledgement`；超时或回执不符 → 由已有账本转 `unknown`。
  回执只表示“执行器收到并已调用 passorder”，**不是券商受理**；受理以委托状态回报为准。
- `QmtIpcReports` 读报告环，转成已有 `BrokerOrder`/`BrokerTrade`，交给原 `FillTracker`
  （按 trade id 去重、终态不再处理、`SQLiteReportLedger`），产出原 `evt:*`。
- `cancel_all`/control（kill/resume/query）走 IPC；账户快照与 `active_order_count`
  由 `snapshot` 提供，写 `state:account`。
- 新生产 CLI（替代 `run_agent.py` 中写死的 `XtQuantAdapter`），复用 agent 主循环、
  StallGuard、单实例锁、心跳。
- 运行在交互 session 1（`Local\` 共享内存按 session 隔离），计划任务 InteractiveToken、
  开机登录自启；共享内存名绑定安装 run，网关创建映射并先写协调标记（r10 已验证的次序）。

### 4.3 Linux：不改业务代码，只开配置

- `LiveBroker(receive_dispatch_status=True)` 目前只允许 `ValidationTransport`；需放开到
  生产传输，但仍要求显式配置开启（N-1 私有配置兼容，#603 规则）。
- Core 行为沿用异步语义：`dispatching`/`transport_received`/`unknown` 都保持 pending 与预留。

### 4.4 状态不明（unknown）的对账消解（新增，原设计缺口）

- 触发：网关冷启动、发送超时、回执不符。
- 规则：用 `snapshot` 按 `client_order_id` 查委托：
  - 查到 → 按真实状态推进（已报/成交/撤/废），不重发。
  - 查不到，且已过指令 `expires_at` + 宽限（执行器拒收过期指令，所以过期后不可能再被下单）
    且连续两次查询一致 → 记为“确认未下单”，此时才允许 Core 释放预留并发布拒单。
- 存在未消解 unknown 时，本账户不开新仓（已有门禁）。
- “查不到”只在执行器自上次 `init` 起持续运行、覆盖指令有效期全程时才可信；期间 QMT 自重建、
  回调中断或查询失败，一律保持 unknown，不进入自动判定。
- 维护者决定：满足上述条件即**自动**释放预留并发布拒单，不需逐笔人工确认（2026-11-09）。
  判定过程与依据（两次查询摘要、QMT 运行连续性、有效期）完整写入账本与告警流水。

### 4.5 首单验证（commissioning）

异步模式下原 `find_order`/`query_active_order_count()==0` 检查不可用。实施者决定：
异步模式禁用 commissioning 路径，首次真实运行改由 §8 第 5 步的授权小额窗口承担。

## 5. 安全与权限

- 密钥：安装时生成，执行器字面量 + 网关私有配置（Windows 私有 DACL 目录）；不进 Git/日志。
  轮换 = 重新安装执行器。安装与轮换属于维护操作，允许使用编辑器（AGENTS §3.3.1：编辑器
  只用于安装、编译和配置）；生产运行本身不依赖界面。QMT 在 userdata 中保存的策略源码含
  密钥，其目录 DACL 与备份范围在第 0 步核查。
- 原 `SafetyPolicy`、本地 `AGENT_KILL`、Redis KILL、北交所门、过期门全部保留；执行器上限是
  第二道独立防线。
- 审计目录沿用 #600 已收紧的私有 DACL。

- 券商许可：主线第 2 阶段“正式运行模式与许可”已完成（[主线文档](../../reference/qmt-external-execution-mainline.md)）；
  第 0 步复核许可范围是否覆盖长期运行。

## 6. 已决定的事

- 维护者决定：原系统设计不变，信号由原系统下发；大 QMT 只做执行器。（2026-11-09）
- 维护者认可：保留 `ExecutionAgent` 及其门禁，替换 `XtQuantAdapter` 为大 QMT 通道；
  执行器改收签名指令；`passorder` 无同步返回，走已有异步账本与 unknown 不重发语义。（2026-11-09）

- 维护者决定：unknown 满足 §4.4 条件后自动释放；账户无人工/其他系统委托；接入现有实盘链路
  配置的策略并改承袭运行中模拟账户 `3c33f0fa…`；不跑影子，故障组合通过后直接小额真实运行，
  小额按系统原“小资金实盘”定义；无科创板、有创业板权限。（2026-11-09）

## 7. 留给实施者的事

- 帧格式、序号与环形缓冲的具体布局（在已有 64KiB 映射内或扩容）。
- 网关轮询频率、`snapshot` 频率、心跳超时的具体秒数（不得宽于原 agent 的 10 秒）。
- 测试替身：扩展已有 fake QMT 与 `FakeAdapter`，覆盖回调丢失、重复、乱序、环满。

## 8. 分阶段落地（每阶段独立验收，失败即停）

0. **探针**：`userOrderId`/`m_strRemark` 长度与字符集；沪深市价单的 `passorder` 报价类型；实盘模式下 `schedule_run` 长时运行
   稳定性；回调在 QMT 自重建后的恢复。只读或合成，不下单。
1. **离线实现与测试**：执行器 v2、`QmtIpcPort`、`QmtIpcReports`、unknown 对账、CLI；
   fake QMT 端到端测试；全量测试、CI、测试部署。
2. **Windows 验证命名空间**：`ValidationTransport` + 合成意图，执行器在模拟模式（不调用
   passorder，只回执与回报“would place”）；含 QMT 自重建、网关重启、一次不重启全链彩排。
3. （取消）影子并行：维护者决定不跑，故障组合通过后直接进入小额真实运行。
4. **故障组合（原第 5 阶段）**：丢回执、回报延迟、网关/QMT/Windows 重启、重复与冲突消息、
   unknown 对账，全部在验证命名空间完成。
5. **小额真实运行**：维护者授权具体窗口、策略、额度后，Live Core 下发真实信号，执行器
   实盘模式，上限按授权字面量。
6. **生产激活**：按 #137 审批；完成单一交易所有者、KILL、监控告警、回滚/停止证据。

## 9. 待维护者确认

无。§1.1、§4.1、§4.4 的维护者决定已于 2026-11-09 写入。
