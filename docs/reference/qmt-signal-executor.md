# 大 QMT 信号执行器 v2 与网关（#601）

设计：[2026-11-09 设计](../superpowers/specs/2026-11-09-qmt-linux-signal-integration-design.md)；
计划：[实施计划](../superpowers/plans/2026-11-09-qmt-linux-signal-integration.md)。

## 正式接口事实（迅投官方文档，2026-11-09 读取）

来源：<https://dict.thinktrader.net/innerApi/trading_function.html>（`passorder`）、
<https://dict.thinktrader.net/innerApi/enum_constants.html>（`prType`、`quickTrade`）。

- **市价类 `prType`**：
  - 沪市：`42` 最优五档即时成交剩余撤销、`43` 最优五档即时成交剩余转限价、`44` 对手方最优、`45` 本方最优。
  - 深市：`44`、`45`、`46` 即时成交剩余撤销、`47` 最优五档即时成交剩余撤销、`48` 全额成交或撤销。
  - 所有市价类均标注“不支持模拟交易中使用”，且集合竞价阶段不允许市价指令。
  - 沪市市价单的 `price` 为保护限价（0~9999，0 表示涨跌停价）；`passorder` 页又称非 11/49 时 `price`
    可填任意数。两处未统一，**以现场为准**。
- **其他价格类型**：`5` 最新价、`11` 指定价、`14` 对手价。
- **原系统口径**：原 `XtQuantAdapter` 把 `order_type='market'` 映射为 `xtconstant.LATEST_PRICE`
  （最新价），价格参数传 0（`src/qsys/agent/xtquant_adapter.py:66-71`）。v2 沿用“市价 = 最新价
  `prType=5`”以保持原系统设计不变；该类型在模拟模式下同样可用。现场 F0 确认其委托价格与成交行为
  后才在安装字面量中启用，未启用前执行器拒绝市价单。
- **`userOrderId`**：文档只说明为字符串“投资备注”，对应委托/成交对象的 `m_strRemark`；
  **未给出长度与字符集上限**。现有 `client_order_id` 为 22 字节 `q{yymmdd}{seq:03d}{12hex}`，
  11-09 实测 15 字节 `q601-…` 原样保留；22 字节待 F0 现场确认。
- **`quickTrade=2`**：不判断 bar 状态，模型调用即触发下单（11-09 已用）。

## 组件（2026-11-09 实现，默认关闭）

| 文件 | 作用 |
|---|---|
| `src/qsys/agent/qmt/executor_source.py` | QMT 内执行器 v2（仅标准库）；仓库版 `ENABLED=False`、无账户与密钥 |
| `src/qsys/agent/qmt/protocol.py` / `memory.py` | 帧与 HMAC、共享内存布局（`Local\qsys_qmt_signal_<run>`，256 KiB） |
| `src/qsys/agent/qmt/port.py` | `QmtIpcPort`：accepted/duplicate→传输 ACK；rejected→发送前拒单；无回执/调用异常→unknown |
| `src/qsys/agent/qmt/adapter.py` | 报告环→`BrokerOrder`/`BrokerTrade`/账户快照（按成交号去重） |
| `src/qsys/agent/qmt/reconcile.py` | unknown 自动消解（设计 §4.4） |
| `scripts/run_qmt_agent.py` | Windows 入口；`--validation-namespace` 用于 F2 |
| `scripts/prepare_qmt_signal_executor.py` | 安装 bundle：`stage`/`verify`；不打印账户与密钥 |

执行器硬上限：沪深白名单（SH 60/51/56/58、SZ 00/30/15/16）买入，北交所与科创板拒买；
单笔与当日买入各 ≤10,000 元（当日额按柜台本系统买单重算，QMT 重建不清零）；网关心跳超过
10 秒、KILL 字或报告积压时拒绝买入，卖出与撤单保留。52 部成待撤不视为终态。

## “确认未下单”自动释放的全部条件

已过 `expires_at`+60 秒；指令创建与当前在同一交易日；执行器本实例启动早于指令创建且纪元
未变；执行器心跳 5 秒内；快照无非本系统备注委托；两次相隔 ≥5 秒的新鲜查询都查不到。
任一不满足保持 unknown（存在 unknown 时网关拒绝新买入）。
新鲜度在本轮查询返回之后取时刻判断：执行器在下一次每秒定时回调中应答并刷新心跳，若用查询前的
时刻比较，心跳永远“晚于 now”而判为不新鲜，unknown 永不释放（2026-11-10 F4 现场发现，已修复）。

## 现场注意

- 启动顺序不再依赖网关先起（2026-11-10 现场发现：QMT 于 08:53 自行重启并自动运行策略实例，
  此时没有网关）。执行器 `init` 完成账户绑定与定时器注册后，先撤回映射中旧实例的 scheduled
  标记与心跳；若协调标记尚不存在则输出 `waiting_for_gateway`，由每秒定时器检查，网关写入
  协调标记后才创建执行器实例、发布 scheduled 标记（`scheduled`）。等待期间心跳为 0，网关发送前
  的 5 秒新鲜度检查会按 `executor_not_running` 拒单。停止实例时同样撤回标记与心跳，等待中停止
  不会再接入。
- 安装 bundle 的 `executor-enabled.py` 含密钥与账户：上传到 Windows 私有 DACL 目录并核对
  实际 NTFS ACL；QMT userdata 中的策略源码同样含密钥（F0 核查 DACL 与备份范围）。
- 审阅 minor 已于 2026-11-09 修复：每条指令前刷新网关心跳（09:25 突发不再误判失联）；回执等待
  改为 6 秒，长于 5 秒传输有效期。
- 验证命名空间的网关为 LIVE 姿态时须有核心心跳：`send_qmt_validation_intents.py
  --core-heartbeat-seconds N` 只在 `component-validation:` 命名空间模拟核心心跳。
- `windows-agent` 组件分类已加入 `src/qsys/live/validation_transport.py`（网关依赖）。

## F2 演练开关与合成信号（2026-11-09）

- 执行器新增 `DRY_RUN`，仓库默认 `True`：保留全部校验、硬上限、回执、报告与只读查询，但
  `place`/`cancel`/`cancel_all`/KILL 撤单一律不调用 `passorder`/`cancel`，回执结果为
  `dry_run`。安装工具只有显式 `--live-orders` 才写入 `DRY_RUN = False`（仅用于维护者授权的
  真实窗口），`run.json` 记录 `dry_run` 并由 `verify` 复核。
- `scripts/send_qmt_validation_intents.py`：只向 `component-validation:` 命名空间发布固定合成
  OrderCommand（`accept` 510300 限价买、`board_reject` 688001 买、`market_sell` 510300 市价卖），
  等待后汇总 `evt:dispatch_status` 与 `evt:rejects`。`RedisTransport.all_messages` 为只读有界
  XRANGE。
- 2026-11-09 已把 `C:\self\qsys-qmt-2.1.33.0\python` 收紧为只允许 SYSTEM 与 Administrators
  （见 #601），QMT 保存与编译验证正常。

## F4 不重启演练（2026-11-09 16:20–16:26，验证命名空间 `qmt-f4`，DRY_RUN）

| 演练 | 结果 |
|---|---|
| 同一指令重投 | 第二次只重放账本事实（`transport_received`），不再发给执行器 |
| 远程 KILL | 买入拒绝、卖出仍下达；网关同时落本地停机闸；清 KILL 后本地闸仍拦截，人工解除后恢复 |
| 核心心跳超时 | 约 10 秒后撤单并落本地闸（`core heartbeat timeout`），买入拒绝 |
| 回执丢失（执行器停止） | `unknown`（`adapter_error:TimeoutError`），不重发 |
| 同号冲突 | 不执行、不覆盖原意图 |

现场发现并已修复：
1. 模型停止后 QMT 保留映射，旧 scheduled 标记残留，新网关会误认执行器在运行（快照超时；若此时下单会
   造成不必要的 unknown）。现执行器 `stop` 撤回标记与心跳；网关发送前要求执行器心跳 5 秒内新鲜，否则
   不写请求、直接按发送前拒单（`executor_not_running`）。
2. 同号冲突消息只在 PEL 空闲回收时重现，夹杂成功循环使连续错误计数不到 3，未升级落闸。现检测到
   `DispatchConflictError` 立即本地落闸，消息保留不确认以便取证。

## 网关开机自启（2026-11-10 设计）

- 入口 `scripts/run_qmt_gateway_supervisor.py --config <toml> --state-dir <dir> -- <run_qmt_agent 参数>`：
  守护进程在 session 1 常驻，网关退出后 30 秒重启；15 分钟内退出 3 次视为重启风暴，写本地停机闸
  （已有闸不覆盖原因）并改为每 5 分钟重启——落闸下网关仍处理卖出与撤单。`<state-dir>\SUPERVISOR_STOP`
  存在时不再启动网关（维护入口）；守护进程单实例；日志 `<state-dir>\supervisor.jsonl`。
  重启不重发任何指令：在途意图转 unknown 后按对账条件消解。
- 计划任务：Administrator 登录触发、无延迟，交互令牌（与 QMT 同 session 1，`Local\` 命名映射
  要求）、最高权限、不限运行时长、已运行则不再启动新实例；动作通过 PowerShell 设置 `PYTHONPATH`
  与 `PYTHONDONTWRITEBYTECODE` 后启动守护进程。执行器与网关的先后顺序不再影响接入（#633）。
- 进程卡死由网关自身 `StallGuard`（默认 60 秒无主循环进展即退出）转为退出，再由守护进程重启。
