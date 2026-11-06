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
模拟实例订阅账户回调。候选现改为查询模式不调用`set_account`、不处理任何回调；只有
`CALLBACKS_ENABLED=True`且提供独立`OBSERVE_NATIVE_CALLBACKS_WITHOUT_TRADING`确认时才订阅。
专项测试覆盖查询模式零绑定/零回调和回调模式缺独立确认时零绑定。

## 当前完成与下一门禁

当前查询层已取得一次真实现场证据，但只证明该次客户端可见的当前形状；空结果不证明柜台完整性，
也没有非空委托/成交样本可定义稳定关联键、方向、状态或费用映射。现场模型已停止且自动运行关闭，
主窗口恢复1024×728，辅助任务disabled/idle；未重启Windows、未启动Live/Shadow、未改KILL。

本次查询授权已消费。任何再次读取真实账户都须重新明确授权；原生回调层仍须另一份明确授权，且
只能使用修正后的独立回调开关与确认。任何授权都不包含真实下单/撤单或Windows重启。
