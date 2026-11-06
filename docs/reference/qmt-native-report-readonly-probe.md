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

正式文档同时明确`account_callback`、`order_callback`、`deal_callback`和
`position_callback`只在实盘运行模式、且`ContextInfo.set_account(account)`后生效。因此验收拆为：

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

## 当前完成与下一门禁

离线测试覆盖默认关闭零调用、精确四类查询、值脱敏、缺字段显式保留、回调有界和异常不重试；
源码AST确认没有任何下单或撤单函数调用。当前没有把候选复制到Windows、安装进QMT、绑定新实例
或读取账户。

下一次现场操作前必须由维护者明确授权“只读真实账户查询”；若要继续验证原生回调，还需明确
授权无交易函数的实盘运行模式探针。任何授权都不包含真实下单/撤单或Windows重启。
