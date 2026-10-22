# Live Core最后独立迁移（#497 / #330）

14个既有manifest目标组件已独立部署；Live Core另列且仍共享运行，不因14/14计数自动发布。
本计划先提供包内入口与隔离前提，生产激活、消费者/状态切换与控制面扩展另分层验收。

## 第一阶段：包内入口与默认兼容

将原scripts/run_live.py实现移到qsys.live.core_cli，旧脚本调用同一main；新增qsys-live-core
console entry。默认仍为旧state/workspace/lock、本地var/KILL、主warehouse journal与var/alerts；
mode=live且orders_enabled=true的原双条件门禁先于任何Redis与状态构造，三次连续失败的急停、
cancel_all和critical通知保持。显式--state/--workspace/--lock及新增--kill-switch/
--journal-warehouse/--alert-ledger为后续私有单元准备，不改策略、风险阈值或订单/成交协议。

预生产显式--validation-namespace复用全部9类传输地址映射，包括订单/成交/消费组/硬编码kill
与heartbeat。还必须给绝对--validation-root，六类可写路径均显式位于其内（解析symlink后也
不能越界），实际live/ops通知目标必须为空，守卫在构造Redis/LiveStore前拒绝不完整配置。
不能只加Redis前缀却写旧生产SQLite、主journal或本地急停；默认生产不传验证参数。
这是防误操作的软件边界，不是Unix/Redis权限隔离，实际unit仍须只读生产事实及精确写集合。

离线测试覆盖原入口兼容、五种错误交易姿态提前拒绝、默认参数、显式状态路径、隔离订单
stream及三次失败保持生产kill不变、文件/通知越界拒绝；这些测试不等于真实Runtime现场验收。
本阶段不新增Live生产发布目标，不切换当前生产unit/配置/交易开关；生产代码仍为共享98fff8b。

## 后续联合迁移门禁

1. 当前生产版本与main逐项比较策略/参数、Broker协议、schema、原state/kill与控制面；现场重新
   核验风险上限、委托/成交/持仓/heartbeat，不沿用旧零成交或空仓结果。
2. 盘点SessionJournal实际表目录和合法写者、当前Web/日报/observer/checks及kill/preflight路径；
   先读数据源迁移记录和历史裁决，不为组件迁移换市场真值源或切D-23分钟源。
3. 确定最小配置与私有身份/实际ACL/资源预算；原市场Warehouse只读，共享合法写者不得静默
   丢权限。消费者先验证真实新记录可发现/可读/不重复，不用main新reader代替当前生产版本。
4. 生产切换须先停旧生产者，再取最新一致性状态；保留旧库、原事件/订单/成交/消费组pending，
   不提前复制遗漏后续事实，不重放完成事件，不覆盖/删除生产历史或操作停机闸来凑验收。
5. 现有component-deploy统一要求Live身份不变。扩展受控Live目标必须限定仅目标身份可变、
   其他peer/源码/配置与preflight保护保持；不能借其他组件命令绕过。新增workflow/controller
   需要提前核对source/controller哈希与evidence bootstrap，按既有规范取得新可信证据。
6. 真正隔离Runtime现场验证必须使用一致性副本、专属namespace/root、关闭通知、只读真实输入
   原值/时间戳，不能读写真实委托流或使用真实账户标识。第一阶段CLI与Memory测试不冒充此项。
7. CI→测试部署→代行逐文件自审→最终制品→隔离证据→明确维护窗口切换；正式workflow、
   GitHub Deployment、实际validate_record三方success后再验实际订单/成交/心跳及维护权限连续。

仍保持原1万元上限/真实交易开关，不因迁移擅自变更。生产异常向前修复、安装器失败补偿保留；
不做生产legacy回切、故障注入或Windows重启。自然费用#137、残余Redis权限#488与Web组#492
继续独立跟踪，#330尚未整体完成。
