# 外部决策路线：异步发送隔离接线

承接 [#601](https://github.com/wanlinruo/quant-trade/issues/601)，路线依据是
[11-05可行性报告](qmt-external-signal-feasibility-report-20261105.md)。
本页描述首阶段代码候选；不是正式QMT交易端口、生产部署或实盘恢复证明。

## 本阶段行为

原同步 `BrokerAdapter.place_order() -> str` 保持默认。新接线由
`open_validation_dispatcher(enabled=True, ...)` 显式创建，默认关闭；
只接受 `ValidationTransport`、`validation:` 账户作用域、明确策略/会话及有专属标记的
私有 `qsys-async-validation-*` 目录。没有生产CLI开关，不读取真实账户配置。
工厂先取得单实例锁，再打开独立SQLite账本、执行恢复和创建发送端口。
Windows已增加只创建新夹具的准备函数和真实NTFS检查，11-05在维护身份下通过隔离工厂实测；
不是正式执行账号安装或生产服务验收，不能把POSIX权限位当ACL。

`AsyncDispatchPort` 定义接口和本地资源关闭约定；诊断准备件
`qmt-async-diagnostic-port.py`已把实际应用账本接到QMT共享内存ping接收器，
只允许固定synthetic-session/diagnostic-only、固定测试证券/数量/价格和synthetic-qsys601前缀。
它只发送固定文本ping与传输关联ID，**不发送意图JSON、证券、价格、数量或账户**。
其ACK证明ping被echo；command SHA/nonce只在外部本地关联，不证明QMT收到交易信号。
尚未实现正式QMT交易回调接线。现有Windows/QMT诊断原型的通过范围仍以
[诊断恢复记录](qmt-external-dispatch-recovery.md)为准。

#602在此基础上增加`qmt-synthetic-intent-protocol.py`与
`qmt-synthetic-intent-port.py`本地候选：共享内存帧携带完整但固定的合成OrderCommand，
接收端逐项验证规范JSON、字段/类型、公开fixture MAC、run/sequence、attempt/nonce、
意图SHA及传输/意图双层有效期。只允许固定沪市测试证券、buy、100股、10.0限价和
synthetic身份；北交所、真实策略/会话/身份形状及任何额外字段均拒绝。
接收端只使用标准库，没有账户、Broker或交易函数，也不接受codec/authenticator扩展；
回执显式固定`executed=false`与`authorizes_trading=false`。外部端口对回执签名、原始
attempt/nonce/SHA及计数再次校验后，才生成现有`DispatchAcknowledgement`。该ACK仍只是
合成内容校验收据，不是券商受理或交易授权；当前尚未取得Windows/QMT现场运行证据。

## 尝试账本与消息含义

每个业务ID只保存一份规范化原始意图、SHA、attempt_id、原会话nonce和真实传输收据。
同ID内容冲突直接失败，保留原事实和待确认消息，不覆盖或伪装成拒单。

| 事实 | 持久状态 | Core行为 |
| --- | --- | --- |
| 首次发送前已提交尝试 | dispatching | 保持pending及资金/数量预留 |
| 端口返回与原尝试绑定的真实传输ACK | transport_received | 仅记录收到，不当券商受理 |
| 超时、无ACK或ACK绑定不符 | unknown | 不重发，不释放预留 |
| 冷启动发现dispatching/transport_received | unknown | 保留原attempt/nonce/收据，等待对账 |
| 确认从未发送的新单被门禁拦截 | rejected_before_send | 持久保存后发布正常拒单 |

先提交账本，再发布发送状态，最后才调用端口；最终状态提交和发布成功后才确认意图消息。
账本、状态发布或确认失败均不转换为拒单。重投先查询历史，再检查新单急停和有效期，
因此旧unknown不会因后来过期或急停被错误清除。已保存的发送前拒绝也不能在重投时重新开单。
unknown/未完成dispatching阻止本作用域新开仓；本阶段尚无券商对账清除入口。

新单要求显式schema_version=2、当前策略/会话、明确有效期和有限正限价；
保留现有安全门禁和北交所禁止开仓规则，不改变行情范围、数据真值或冻结阈值。

## Core持久化

独立 `DispatchStatusEvent` 不含券商单号，禁止额外字段。`LiveBroker` 仅在显式开启且
使用隔离传输时消费 `dispatch_status`；收到事件不改变pending、剩余数量或资金预留。
`LiveStore.commit_reports()` 将发送事实写入现有订单转换表，与本轮组合状态同事务提交；
不会改成rejected/filled，冷启动仍恢复原pending。未知订单事件使整个事务失败。
数据库提交后才ACK回报；提交失败或ACK失败保留本进程事件缓存，重试不丢发送证据。
格式错误的事件保留待处理，不在下轮顺带ACK。新接线发出撤单请求也继续保留pending，
直到真实终态回报到达；发出撤单请求不等于柜台已撤。

测试配置模板和独立演练流已补齐新字段的隔离前缀，配置校验继续拒绝与生产地址重合。
既有入口默认不开启新流，不新增生产服务、定时任务或运行数据库迁移。

## 验证范围和后续

- 首次集成SHA`025f5e9`的CI成功，但测试部署41972979833在受限gateway回滚：节点N-1
  私有配置没有新`dispatch_status`字段，加载后生产默认值被测试前缀门禁判为显式配置。
  #603候选保持新通道默认关闭，仅在配置明确出现该可选字段后把它纳入前缀、唯一性和
  跨环境碰撞验证；既有流规则不变。原失败run-id及rolled_back节点记录保留，未直接改节点
  配置或重放部署。部署契约专项181项、异步链路相关71项及全量4621项通过。集成SHA
  `8e2754a`的CI 41973969725成功；新run-id`github-41974151636-1`对应测试部署41974151636、
  节点record与GitHub Deployment 7566032206三方success。#603已关闭；原失败记录保持。
  这只证明test-runtime部署兼容，不是QMT现场运行或生产组件激活。

- #602本地候选新增38项协议/端口测试；与既有异步账本、QMT ping端口及Core流联合专项
  共86项通过。全量4618项通过、7项Linux凭据条件跳过、3项既有弃用警告；secret scan、
  提交策略和diff检查通过。实际账本保存合成校验ACK后，冷启动仍转unknown、保留原收据
  且零重发。此结果没有Windows/QMT源码回读、编译、schedule_run或响应收据，不能替代
  #601既有ping现场证据，更不能视为完整意图现场送达或交易能力。

- #602后续为同一标准库接收器补齐QMT模型调度入口：必须先存在协调器marker，绑定当日、
  2.1.33.0客户端和新run-id，最多24次正式`schedule_run`回调且只处理1包，结束后主动cancel。
  内存双端回归使用实际`SyntheticIntentPort`发送完整规范OrderCommand并取得绑定原尝试的
  非执行ACK；marker缺失在调度前拒绝。全量4624项通过、7项条件跳过、3项既有警告。
  11-05 14:18现场只读预检确认QMT仍Responding，但其session1为Disconnected、console在
  session3，不满足既有前台UI守卫，因此本轮没有安装、编译或运行现场模型。待session1恢复
  可交互后另取源码回读、编译、正式调度和响应收据；本地包装器通过不替代这些证据。

- 11-05 11:00：实际异步工厂、NTFS账本与QMT编辑器模拟环境完成两段接线。
  A收到固定ping，提交ACK；B使用新wire会话和新Receiver空缓存，10:58:24重投旧意图，
  当时其11:04:07有效期尚未结束，保留原attempt/nonce/ACK并转unknown，发送帧头仍为零。
  独立新账本的对照ping随后成功，B实际只收到该对照消息，排除了仅靠过期拦截的解释。
  两段各1次正式schedule_run回调，cancel均true；最终callback_bound最大约5.7446/1.0682ms，
  不是性能承诺，不使用finished中尚未更新的0ms。两组响应SHA/HMAC已独立重建一致。
  11:00 session1实际检查两映射不存在、两锁独占可开，全部新辅助任务disabled/idle，
  失败终态保留；四业务任务XML与本轮客户端身份保持。未重启QMT/Windows。
  [机器摘要](qmt-async-qmt-probe-20261105.json)绑定源码、模型准备副本及辅助失败。
- 固定合成ping诊断端口新增15项测试，本轮最新全量4580通过、7项Linux凭据条件跳过，
  3项既有弃用警告。Windows两个独立外部接收进程也完成同协议跨会话对照；该结果与
  上述真实QMT编辑器结果分开记录，不互相冒充。
- 11-05 10:16：Windows现有外部Python3.11.9以候选源码ZIP运行实际异步工厂，
  专属NTFS根内发送一次合成意图并记录ACK；重新打开后保留原attempt/nonce/收据，
  转为unknown且端口累计仍只调用一次。没有调用QMT接口或券商接口。
  Users可读根在打开状态库/创建端口之前被拒，空库SHA保持；状态文件硬链接被拒。
  测试临时增加的Users权限已从新负例夹具恢复，独立检查三根均为受保护DACL且无额外受托人。
  四业务任务XML和本轮QMT进程身份保持。机器摘要见
  [NTFS实测](qmt-async-ntfs-probe-20261105.json)。
- 最新全量4565项通过、7项Linux凭据条件跳过、3项既有弃用警告，包含17项新增NTFS
  策略/准备边界测试和前一阶段真实子进程退出测试。无新增生产入口脚本。
- NTFS准备默认关闭，只对已为当前SID/SYSTEM私有的父目录创建新根，拒绝复用/覆盖旧根，
  不修改父目录权限。新根owner必须为实际当前SID；ACL只允许该SID及SYSTEM完整访问，
  检查固定本地NTFS、所有祖先无reparse、状态文件无额外硬链接，拒绝未知文件。
  文件owner允许SID/SYSTEM/Administrators（Windows可能以默认组拥有新文件），这不授予
  Administrators额外DACL项；不声称管理员无法提权访问。准备失败保留新目录，不自动重放。
  r1后台启动未产出启动标记、状态根或输出且进程已退出，具体退出原因未捕获；保留记录，
  r2使用独立新根同步执行，exit0并取得验收。该差异不证明无人值守启动能力。
- 专项36项通过：持久领取、冷恢复、重复/冲突、故障后的消息保留、提交后ACK、
  Core预留和恢复、发送前拒绝防重，以及既有配置隔离。
  包含两项真实子进程在发送标记提交后/ACK提交后直接退出，冷打开保留原尝试和收据，
  转为unknown且不再次发送；不是生产故障注入。
- Windows实际Python3.12.10完成精确临时锁测试：同进程及独立子进程均拒绝重复持锁，
  释放后可重获，锁文件保持1字节；没有调用业务Runtime。
  r1辅助探针因PowerShell传递`python -c`引号失败，业务未执行且未创建锁；保留该记录，
  r2改为独立探针文件后成功。该结果只覆盖锁实现，不替代应用NTFS工厂验收。
- 首阶段当轮全量4546项通过、7项Linux凭据条件跳过；两项子进程退出测试在该轮收集后新增，
  已在最终36项专项内通过，不冒称该轮全量包含新增测试。首次全量发现旧测试模板缺少新流前缀，
  已同时修复模板与夹具，隔离校验未放宽。

下一阶段另验正式执行身份与安装生命周期、客户端重启和双端恢复。
本轮有效期内跨接收会话只覆盖ACK后恢复，其他故障组合仍须按层验收。
正式原生回报关联、部分成交/撤单终态、
券商允许的运行方式和人工unknown对账仍是实盘前置。#598已于11-05通过exact lease人工
对齐及新SHA CI收口；本候选仍须独立走正常CI、测试部署和发布流程，不能将本地通过表述为上线。
