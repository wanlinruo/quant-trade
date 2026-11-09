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

## 现场注意

- 执行器 `init` 要求网关先创建映射并写协调标记；QMT 若先于网关启动，执行器保持未调度，
  须在网关就绪后重启该策略实例（F2 验证开机顺序与自启）。
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
