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

端口在运行时只从自身同目录按精确名称加载`qmt-synthetic-intent-protocol.py`；相同字节若只以
`candidate-default.py`等其他名称存在，不能满足运行契约。准备新的空目录时使用
`python scripts/prepare_qmt_synthetic_runtime.py stage <target>`，其中`target`是只容纳这两个
运行时文件的新空目录。工具以不覆盖方式写入精确文件集，拒绝额外文件，逐字对照受审源，并从
最终目录真实导入port、核对protocol的实际加载路径。传输到
Windows后仍须对最终节点目录执行`verify`，不能以构建机收据或单独文件SHA替代；缺文件负例必须
在创建状态根、first-ready和发送门之前失败。

Windows整机重启或不重启彩排bundle必须使用
`python scripts/prepare_qmt_synthetic_runtime.py stage-reboot <bundle-root> --run-id <run> --validation-mode reboot|rehearsal`，
以不覆盖方式一次性安装受审`native.py`、受审协调器`coordinate.ps1`
（[源](qmt-windows-reboot-coordinator.ps1)，run无关、仅从同目录`fixture.json`/`requested.json`绑定）、
绑定run/mode/任务名的`fixture.json`及精确`runtime/`目录；节点侧使用相同参数的`verify-reboot`。
不再逐轮手写协调器或另行补写fixture（#601 r3/r7教训）。协调器创建私有映射后、启动driver之前
就写入并回读6144协调器标记，driver只校验不清零；因此开机后自动运行的QMT模型`init()`不再依赖
driver先完成最长45秒的冷启动安全检查（r6实测余量约46秒，属于未由设计保证的竞态）。
`rehearsal`模式要求开机时间与记录一致且QMT PID不变，结果如实记录`windows_restarted=false`，
只证明不重启动态链，不替代整机重启层。
协调器与driver之间以文件存在性作门，因此双方都必须原子发布：先在同目录`.staging-<名>`写完并
刷盘、关闭句柄，再以不覆盖方式（PowerShell `File.Move`、Python `os.link`）出现最终文件名；读取方
在原期限内对未关闭写入方或半写JSON有界重试，超时即“未发送”失败。2026-11-08不重启彩排
（run `49fa0fcd…`）正是在此处失败：协调器以独占句柄写`first-go.json`时driver已见文件存在并
读取，`PermissionError`后在dispatch前退出，发送0；r1~r6从未走到该步，整机重启无法提前暴露。QMT模型只有在正式
`schedule_run`返回后，才在共享内存保留区写入绑定当前run-id的scheduled marker。外部driver只读
有界等待该marker并创建`model-scheduled.json`，协调器将其SHA绑定进`first-go.json`后才允许发送。
不得再以FormulaOutput日志、窗口句柄或单次`Responding`值替代该marker；marker仅证明有限调度已
建立，不是消息ACK、券商受理或交易授权。

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
- NTFS准备默认关闭。状态根仍只允许在当前SID/SYSTEM私有父目录下创建；若现场既有审计根是
  精确Administrators/SYSTEM受保护DACL，先要求当前token实际属于Administrators，再在其下
  不覆盖地新建专属父目录并立即收紧为当前SID/SYSTEM，独立回读owner/DACL和空目录后，才在
  该专属父目录下创建状态根。不能直接把Administrators组父根当状态根父目录，不能修改既有
  父根权限，也不能复用/修复失败目录。新根owner必须为实际当前SID；ACL只允许该SID及SYSTEM完整访问，
  检查固定本地NTFS、所有祖先无reparse、状态文件无额外硬链接，拒绝未知文件。
  文件owner允许SID/SYSTEM/Administrators（Windows可能以默认组拥有新文件），这不授予
  Administrators额外DACL项；不声称管理员无法提权访问。准备失败保留新目录，不自动重放。
  r1后台启动未产出启动标记、状态根或输出且进程已退出，具体退出原因未捕获；保留记录，
  r2使用独立新根同步执行，exit0并取得验收。该差异不证明无人值守启动能力。
- 11-08整机r5中，运行时精确文件打包已越过导入，但审计父根实际为Administrators/SYSTEM，
  旧入口却直接要求当前SID/SYSTEM，因`unexpected NTFS trustee`在状态根创建前拒绝；发送门不存在、
  发送0。修复不放宽状态根ACL，而是新增上述两级准备：只把精确管理员私有根作为bootstrap，
  创建当前SID/SYSTEM专属空父目录，再沿用原状态根严格检查。现场原失败及默认关闭收尾保留。
  修复候选随后在同一Windows受保护审计根下使用全新隔离目录实测：专属父目录和状态根owner均为
  当前SID，DACL受保护且精确SID+SYSTEM、非reparse；三个状态文件逐项为单硬链接并通过既有
  `validate_windows_validation_root()`。该实测未重启Windows、未启动QMT模型或调用账户/交易接口，
  只证明ACL两级准备链，不冒称整机闭环通过。
- 11-08整机r6中，两级ACL链在真实重启冷启动路径通过并写出`first-ready`，但协调器随后仍在发送门
  前失败：它从无FormulaOutput文件的`userdata_mini`查scheduled marker，实际日志位于
  `userdata/log`且活动文件不允许并发`ReadAllText`；同时单次QMT UI就绪漂移被立即判为
  `QMT lost readiness`。验证模型虽自动运行，不能替代scheduled证据。最终发送门不存在、
  `dispatch_attempts=0`、发送0；模型停止、默认关闭源码恢复、现场0/0/0。修复改用共享内存中由
  `schedule_run`成功后发布的run-bound marker，并将受审重启driver纳入最终bundle形状；本地通过
  不等于整机复验通过，再次重启仍需新run-id和新的单次明确授权。
- 11-07整机重启后，driver在创建状态根前的首个只读父目录检查触发15秒`TimeoutExpired`；
  发送门未创建、合成意图发送0。修复候选仅将每个进程首个只读安全检查改为单次45秒窗口，
  不自动重试；ACL写入仍为单次15秒，失败保留新空目录。所有ACL、owner、NTFS、reparse、
  硬链接和文件白名单判定保持。下次协调器的ready截止时间必须覆盖该只读窗口及Python导入余量，
  仍须新run-id和新的明确整机重启授权取得现场证据。
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
