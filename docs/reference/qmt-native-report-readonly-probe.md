# 大QMT原生账户与回报只读探针准备

日期：2026-11-06。跟踪：[#601](https://github.com/wanlinruo/quant-trade/issues/601)。

## 目标与边界

本探针只为唯一主线第4阶段确认当前江海大QMT `2.1.33.0` 的账户、持仓、委托、成交查询形状，
以及正式回调对象的字段形状。候选文件为
[qmt-native-report-readonly-probe.py](qmt-native-report-readonly-probe.py)。检入状态
`ENABLED=False`，没有下单、撤单、历史查询、文件、网络或IPC操作，不生成
`OrderStatusEvent`/`FillEvent`，更不据字段名猜状态、方向、时间或手续费口径。

探针即使只读，也会访问真实账户事实，因此离线测试通过不授权现场执行。Windows整机重启、
真实委托、撤单、KILL和Live/Shadow继续不在本项范围。

## 已确认的正式接口边界

迅投正式文档给出当前查询入口：

```text
get_trade_detail_data(accountID, accountType, datatype)
```

本候选只准备一次性查询`ACCOUNT`、`POSITION`、`ORDER`、`DEAL`，固定账号类型`STOCK`；每类
只调用一次，异常不换签名、不重试。历史接口与当前客户端可见性尚未现场确认，因此本轮不调用
`get_history_trade_detail_data`。

正式文档说明`account_callback`、`order_callback`、`deal_callback`和
`position_callback`在实盘运行模式、且`ContextInfo.set_account(account)`后生效。11月6日现场却在
正式模拟实例调用`set_account`后自然收到一次`account_callback`，所以不能再用“模拟模式”作为回调
不会发生的保护条件，验收仍拆为两个独立授权层：

1. **当前查询层**：明确授权读取真实账户事实后，可在正式容器中验证四类查询字段；不发送委托。
2. **原生回调层**：须另行明确授权仅运行无交易函数的实盘模式探针，等待自然回调；不通过制造
   委托触发回调。模拟实例或编辑器函数存在性不能冒充这一层通过。

两层在配置和调用上严格互斥。查询层必须且只能设置`QUERIES_ENABLED=True`与
`READ_REAL_ACCOUNT_FACTS_WITHOUT_TRADING`，不调用`set_account`；回调层必须且只能设置
`CALLBACKS_ENABLED=True`与`OBSERVE_NATIVE_CALLBACKS_WITHOUT_TRADING`，不调用任何当前查询。
两模式同时启用、同时关闭、开关不是严格布尔值或携带另一模式的确认串时，均在账户绑定和查询前拒绝。

正式依据：[交易查询函数](https://dict.thinktrader.net/innerApi/trading_function.html)、
[原生回调函数](https://dict.thinktrader.net/innerApi/callback_function.html)、
[交易数据结构](https://dict.thinktrader.net/innerApi/data_structure.html)。

## 脱敏与事实规则

- 不输出账号、账号key、证券代码/名称、委托号、成交号、投资备注、价格、数量、金额或状态值。
- 只输出对象类型、白名单字段的`none/partial/all`覆盖状态、Python类型、缺失/部分覆盖字段，以及对象账号是否与
  正式容器绑定账号一致的布尔值。
- 空结果记录`empty_unverified`，不解释成未登录、无持仓、无委托或无成交。
- 字段缺失保持缺失；`m_dCommission`、`m_dComission`、`m_dComssion`分别观察，不做兼容填充。
- 委托状态、买卖方向、时间、数量推进和ID关联在实际原值确认前不映射到qsys业务事件。
- 不输出持仓/委托/成交行数；单类超过5000行只报拒绝、不报具体数量。回调形状最多记录4次，
  防止日志噪音。异常只记录类型，不记录消息。

## 11月6日现场结果

维护者明确授权“只读真实账户查询”，且明确排除实盘回调、下单、撤单和Windows重启。候选以
`QSYS601_REPORT_READONLY`安装、编译和源码回读，绑定当前股票账户的正式模拟实例；自动运行关闭，
启动前执行中策略为0。脱敏结果见
[qmt-native-report-readonly-probe-20261106.json](qmt-native-report-readonly-probe-20261106.json)：

- `ACCOUNT`返回非空`AccountDetail`形状，账号匹配布尔值为true；不记录账户值或资产值。
- `POSITION`、`ORDER`、`DEAL`均为`empty_unverified`，不能解释成柜台完整空仓、无委托或无成交。
- 四类当前查询各调用一次，`account_reads=4`；历史读取、交易和撤单调用均为0。
- 查询scope输出之后自然收到一次`account_callback`，对象仍为脱敏`AccountDetail`形状。它没有由委托
  触发，但超出本次授权，不能冒充“回调层验收通过”。实例在有界窗口内停止，未继续等待其他回调。

现场同时暴露候选边界缺陷：查询模式无条件调用`ContextInfo.set_account(account)`，而该调用实际会在
模拟实例订阅账户回调。候选现改为查询模式不调用`set_account`、不处理任何回调；回调模式只在
独立开关与确认匹配时订阅，且不顺带执行`ACCOUNT/POSITION/ORDER/DEAL`当前查询。
专项测试覆盖查询模式零绑定/零回调、回调模式零查询，以及缺确认、混合模式和交叉确认的零副作用拒绝。

## 11月7日原生回调模式现场结果

维护者另行明确授权安装并运行无交易函数的原生回调模式，绑定当前账户且只记录脱敏回调；明确排除
当前查询、下单、撤单、Windows重启、Live/Shadow和KILL修改。修正后的callback-only候选以新
run-id安装、回读、保存并编译，`QUERIES_ENABLED=False`、`CALLBACKS_ENABLED=True`。正式模拟
实例经QMT“策略已修改”确认后进入运行中约12秒，随后停止。脱敏结果见
[qmt-native-callback-probe-20261107.json](qmt-native-callback-probe-20261107.json)：

- 配置被正式接受，`callback_subscription_requested=true`；`query_calls=account_reads=0`，历史读取、
  交易和撤单调用均为0，未输出账户或交易原值。
- 有界窗口内没有自然账户、持仓、委托或成交事件，`callback_observations=0`。这只证明独立回调模式
  的配置、绑定入口和实例启动/停止可用，不能证明任何回调对象字段形状，更不能定义稳定委托/成交映射。
- 计划停止前QMT UI短暂报告不响应，保护分支只执行一次停止点击；随后独立截图确认实例“终止”、
  执行中策略为0，客户端恢复并持续响应。没有重启客户端或Windows。
- 验收后将目标源码恢复为仓库默认关闭版；编辑器规范化回读与候选SHA一致，保存、编译成功且未运行。
  主窗口恢复1024×728，80个本路线审计辅助任务全部disabled。

## 当前完成与下一门禁

当前查询层已取得一次真实现场证据，但只证明该次客户端可见的当前形状；空结果不证明柜台完整性，
也没有非空委托/成交样本可定义稳定关联键、方向、状态或费用映射。原生回调层完成了一次独立配置、
绑定和启动/停止现场验证，但因自然回调为0，字段形状仍未验收。现场模型已停止、自动运行关闭且源码
恢复默认关闭，主窗口恢复1024×728，辅助任务全部disabled；未重启Windows、未启动Live/Shadow、
未改KILL。

两次现场授权均已消费。任何再次读取真实账户或运行原生回调模式都须重新明确授权；回调模式只能
使用独立开关与确认，不顺带重放当前查询。任何授权都不包含真实下单/撤单或Windows重启。
