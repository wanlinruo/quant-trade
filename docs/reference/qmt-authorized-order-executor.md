# 大QMT授权单笔执行器（#601 第4/5/6阶段合并验收）

日期：2026-11-08。跟踪：[#601](https://github.com/wanlinruo/quant-trade/issues/601)。

## 为什么合并第4~6阶段

账户当前查询 POSITION/ORDER/DEAL 均为空，11-07 原生回调探针窗口内也没有自然回调。委托/成交
回报的字段形状、状态码、`m_strOrderSysID` 与投资备注关联只能由一笔真实委托产生，因此第4阶段
（回报形状）、第5阶段（unknown 真实对账）与第6阶段（最小真实闭环）以同一组极小委托合并取证。

维护者 2026-11-08 决定：当前唯一股票账户（账户值只在节点私有文件），沪深300 ETF（准备按 510300.SH），先一笔**不成交挂单+撤单**，
再一笔**小额可成交买单**（100 份）；窗口 2026-11-09 10:00–11:00；仅窗口内新建执行实例切到“实盘”，
验证实例 `QSYS601_THIN_VALIDATION` 保持模拟不动。持仓按 T+1 次一交易日卖出，卖出另行确认。

## 正式接口事实（迅投官方文档）

- `passorder(opType, orderType, accountid, orderCode, prType, price, volume, strategyName, quickTrade, userOrderId, ContextInfo)`，**无返回值**。
  股票/ETF 买 `23`、卖 `24`；`1101` 单股单账号按股/份；`prType=11` 指定价时 `price` 生效；
  `quickTrade=2` 不判断 bar 状态即下单；`userOrderId` 写入委托/成交对象 `m_strRemark`。
- `cancel(orderId, accountId, accountType, ContextInfo)` 返回“是否发出撤单信号”的 bool，`orderId` 用 `m_strOrderSysID`。
- `order_callback`/`deal_callback`/`orderError_callback` **仅实盘运行模式**生效，且需先在 `init` 调用 `set_account`。
- 委托状态 `m_nOrderStatus`：49 待报、50 已报、51 已报待撤、52 部成待撤、53 部撤、54 已撤、55 部成、56 已成、57 废单。
- 官方页面未写明：`schedule_run` 回调中 `quickTrade` 的行为、`userOrderId` 长度限制、ETF 最小价位。
  这些以首次现场证据为准，未证实前不写入映射。

## 组件

| 文件 | 位置 | 作用 |
|---|---|---|
| [执行器](qmt-authorized-order-executor.py) | QMT 策略源码 | 只执行与安装字面量计划逐字节相同的步骤 |
| [driver](qmt-authorized-order-driver.py) | Windows `C:\qsys\.venv` | 按券商事实逐步推进，日志先落盘 |
| [会话协调器](qmt-authorized-order-session.ps1) | Windows session 1 计划任务 | 私有映射、先写协调器标记、拉起 driver |
| `scripts/prepare_qmt_authorized_order.py` | 构建机 | 生成并校验私有 bundle |

共享内存 64KiB：请求 0、响应 4096、协调器标记 6144、scheduled 标记 7168（与验证候选同布局），
报告环自 8192 起，50 个 1KiB 槽，单写者（QMT）。

## 安全模型

- 检入候选 `ENABLED=False`、账户/密钥/计划为空；启用版只在私有 bundle 中以字面量绑定，**账户与
  密钥不进 Git、不打印**。
- 只执行与 `AUTHORIZED_PLAN` 逐字节相同的步骤；每步每 run 最多一次，步骤序号只前进（可跳过、
  不可回退）；同 attempt 重放返回缓存回复，不重复调用券商接口。下单前按备注查询 ORDER，已存在则
  报告而不再下单（覆盖模型重启）。
- 计划校验：沪深主板 `60/00` 与 ETF `51/15` 代码且后缀与交易所一致；北交所、科创、创业板拒绝
  （#589）；数量为 100 的正整数倍且 ≤100；限价至多 3 位小数，单笔名义金额 ≤1000 元；授权窗口 ≤3 小时，
  窗口外的任何消息（含快照）拒绝。
- HMAC 签名、5 秒消息期限、run 绑定；协调器先写 6144 标记，执行器 `init` 读不到即失败。
- 回调与查询只记录备注属于本计划的对象，白名单字段，不输出账户号，只给 `account_match` 布尔值。
- 回复只表示“已调用 passorder/已发撤单信号”，`broker_accepted=null`；券商事实只来自回调或查询。
- driver：发送前 fsync 日志；无认证回复即 `unknown`，停止序列，只用只读快照对账，**绝不重发**；
  挂单未在 30 秒内“已报”、挂单意外成交、废单或下单错误回调均停止，不再下后续单；可成交单 60 秒未
  “已成”则执行计划内撤单。

## 现场手册（11-09 窗口）

1. 09:40 前：从最终 main 生成新 run 与 bundle（`stage`，价格待定前不生成），Windows 最终形状验证。
2. 09:50 取 510300.SH 实时价，向维护者给出两笔限价：挂单约低于现价 3%（不成交），可成交单为卖一价
   加若干最小价位；维护者确认后生成 bundle。
3. 模型研究新建 `QSYS601_ORDER_EXECUTOR`（若尚未建），安装启用候选、回读、保存编译，不运行。
4. 模型交易新建该策略实例：当前股票账号、**不勾自动运行**；窗口内把该行切到“实盘”。
5. 写 `requested.json`（run、计划哈希、QMT PID），注册并启动会话任务；driver 就绪后点该行 ▶ 并确认。
6. driver 完成或停止后：停止实例、切回“模拟”、恢复默认关闭源码并编译；最终 0/0/0；脱敏摘要入库。

任何一步异常即停止，失败记录保留，不在同一授权内重试下单。

## 不在本项范围

Linux 意图流接入、真实 unknown 自动消解进入业务账本、持仓卖出、Live/Shadow 启动、KILL 修改、#137 激活。
