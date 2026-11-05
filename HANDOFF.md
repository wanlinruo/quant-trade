# 项目交接入口

## 11-05 13:12：GitHub test对齐恢复，#598完成

- 写操作前重新核验远端`main=b82bd88`、`test=a2ffd4a`且tree同为`50ad9c4`，PR#597直接记录
  `merged=true/merge_commit_sha=b82bd88`，原push/PR CI与测试部署成功；随后只对test执行一次
  exact `--force-with-lease`，expected a2ffd4a、target b82bd88，main未写入或force-push。
- 后验main/test均为b82bd88；新test push CI 41972310297绑定该SHA，policy、secret scan、
  package与full suite全部成功。`commits/b82bd88/pulls`仍空，未补生成历史Align/Direct push
  audit；以PR直接绑定、exact lease收据、双ref一致和新SHA CI作为明确人工对账，不冒称自动事件。
- #598已按completed关闭；当前功能分支已从同树旧基线重放到最新test，尚未集成或取得候选CI。
  此恢复不授权生产部署、交易激活、真实账户/委托、清KILL或Live/Shadow启动。

## 11-05：完整合成指令校验与非执行回执本地候选（#602）

- 新增独立固定协议与共享内存端口，只接受`synthetic-session/diagnostic-only`、固定测试证券/
  方向/数量/价格和`synthetic-qsys-*`身份；逐项绑定规范意图、SHA、attempt、nonce、run、
  sequence与双层有效期。错签、乱序、过期、字段/类型变化、真实身份形状及北交所样本均拒绝。
- QMT侧标准库Receiver没有账户、Broker、交易函数或可注入codec/authenticator；回执固定
  `executed=false`、`authorizes_trading=false`，外部端口同时核对回执签名及原尝试绑定。
  回执只表示固定合成内容通过校验，不是券商受理、成交或交易授权。
- 实际`AsyncOrderDispatcher`账本专项确认：诊断ACK保存后冷启动转unknown，保留原收据且
  不再次发送；现有pending/预留和真实回报边界不变。相关专项86项通过；全量4618通过、
  7项Linux凭据条件跳过、3项既有弃用警告；secret scan、提交策略和diff检查通过。
- 当前仅为本地候选，尚未取得Windows/QMT现场源码回读、编译、正式schedule_run与响应收据，
  也没有客户端重启、正式身份/模式、券商许可或原生回报证据。未接真实意图/账户/委托，
  未清KILL或恢复Live/Shadow；#598已完成，但本候选尚无CI、测试部署或发布证据。

## 11-05 11:00：实际应用账本接通QMT诊断端口，补齐有效期内ACK恢复（#601 / #599）

- 新诊断端口仅允许固定合成意图，向QMT只发固定ping文本与关联ID，不发证券/价格/数量/
  账户或意图JSON。ping ACK不代表收到交易信号；无生产CLI或正式交易接口，公开HMAC
  fixture不作生产身份。实际运行是Windows工厂直调，Core/Agent消费集成仍是本地测试证据。
- QMT编辑器模拟环境两段源码回读/SHA一致并编译成功：A接收1条后提交ACK；B新wire
  会话及新Receiver空缓存，10:58:24恢复旧意图（11:04:07才到期）转unknown且帧头零发布，
  原attempt/nonce/ACK保持。另起独立账本的新对照ping成功，B仅收到该对照1条。
- 两段正式schedule_run组均主动cancel=true，最终callback_bound最大约5.7446/1.0682ms，
  不是性能承诺；两组响应SHA/HMAC独立重建一致。11:00在session1查两映射已释放，
  两账本锁可独占打开；所有新辅助任务disabled/idle，四业务任务XML/客户端身份保持。
- 辅助失败保留：r4/r7界面守卫拒绝；r9长命令未送达（目录/任务/源码收据均无），新r10
  改文件载入；首次协调器ACL构造失败且驱动未启动，新first-r2使用既有AccessRuleFactory；
  r12在业务成功后截图辅助变量冲突失败，独立收据/日志复核且未重跑业务。无清失败历史。
- 新增15项端口测试，全量4580通过、7项Linux凭据条件跳过、3项既有弃用警告。
  [接线说明](docs/reference/qmt-async-dispatch.md)与
  [机器摘要](docs/reference/qmt-async-qmt-probe-20261105.json)记录源码绑定和实际验收层级。
- 本轮仅补齐ACK后的有效期内跨会话组合，未重启QMT/Windows、未接真实意图/账户/委托、
  未清KILL或恢复Live/Shadow。正式执行身份、运行模式/券商许可、客户端重启、原生交易
  回报及unknown对账仍待验；#598关联API仍空，整体Issue不关闭，不将本地/诊断通过冒称CI/发布完成。

## 11-05 10:18：Windows NTFS私有根与异步隔离工厂实测通过（#601）

- 新增`windows_validation.py`：默认关闭，只在已私有父目录新建夹具；不覆盖已有根或改父ACL。
  核验当前SID/root owner、受保护DACL及SID/SYSTEM精确权限、本地固定NTFS、祖先reparse、
  状态文件硬链接和文件白名单。Windows工厂改为检查真实NTFS后允许隔离使用，仍无生产CLI。
- 使用现有外部Python3.11.9和绑定SHA的候选ZIP，在新私有目录运行实际异步工厂。
  合成意图仅发送一次，重开SQLite后unknown保持原attempt/nonce/收据；没有调用QMT接口。
  Users可读负例在状态/端口初始化前被拒、空库SHA保持；硬链接拒绝。负例临时Users权限
  已恢复，独立复核三根均protected且无额外受托人，四业务任务XML/本轮QMT进程身份保持。
- r1后台启动返回PID后未产出启动标记、状态目录或输出，进程已退出，具体原因未捕获；
  保留该记录。确认现场后，r2使用新根同步执行，真实exit0和验收收据齐全，不重放r1。
  维护SID/外部会话夹具通过不证明正式执行身份、QMT桌面会话或无人值守启动通过。
- 本地专项初轮51项通过；补充既有目录/不可信父目录零变更测试后，全量4565通过、
  7项Linux凭据条件跳过、3项既有弃用警告。secret scan、提交规范与可信main/候选路径分类通过。
  [设计与实测范围](docs/reference/qmt-async-dispatch.md)及
  [机器摘要](docs/reference/qmt-async-ntfs-probe-20261105.json)已落盘。
- 仍未接真实意图/账户、下单/撤单、清KILL或启动Live/Shadow；#598关联API仍空，远端
  main=b82bd88/test=a2ffd4a保持，本地候选不冒称CI/测试部署/生产发布。下一步为无账户
  QMT诊断端口接线、有效期内跨会话与客户端重启，正式回报/unknown对账和执行身份另验。

## 11-05：外部决策路线首阶段异步应用接线（#601）

- 按维护者“按照这个方向继续进行改造与推进”的新指示，实现默认关闭的隔离候选，
  [设计与边界](docs/reference/qmt-async-dispatch.md)。ExecutionAgent增加异步发送端口，
  首次发送前持久领取；真实传输ACK独立于券商受理，无券商单号时不伪造OrderStatusEvent。
- SQLite独立账本保存原意图/attempt/nonce/收据；重投先查历史，超时及恢复unknown不重发，
  不因后来过期/急停改判拒单；发送前拒绝也持久化防重。unknown阻止本作用域新开仓。
  Core同事务保存发送事实后才ACK，提交/ACK失败保留证据；unknown和撤单请求不释放预留，
  冷启动保留pending。保留北交所禁止新开仓，数据源和冻结阈值不变。
- 工厂仅接受ValidationTransport、显式validation作用域及专属私有状态根，先锁后初始化。
  没有生产CLI开关或正式QMT下单实现；Windows应用工厂仍拒绝激活，待真实NTFS安装契约。
  Windows仅完成精确临时锁专项：同进程/子进程抢锁均拒绝，释放后可重获，锁文件1字节。
  r1辅助探针引号失败保留，确认零执行/无锁后，r2独立文件探针通过；未调用业务Runtime。
- 全量4546通过、7项Linux凭据条件跳过；随后补充两项真实子进程退出测试，最终专项36项通过。
  实际在发送标记提交后/ACK提交后退出，重开均保留原尝试并转unknown且零重发。首次全量仅
  旧测试配置缺新流前缀失败，已同时补齐模板、夹具和演练流，保持配置隔离校验。
  未接真实意图/账户、下单/撤单、清KILL或启动Live/Shadow。
- 可信main和候选manifest均覆盖新增路径，无unknown路径；#598的main提交关联API仍为空，
  本地候选尚未完成CI、测试部署或生产发布，不绕过该前置，不关闭#599/#533等接入任务。
  下一阶段：Windows身份/ACL安装、无账户端口接入QMT诊断、有效期内跨会话与完整重启，
  再补正式回报关联和unknown对账；当前代码没有解除unknown的生产入口。

## 11-05：外部决策与大QMT薄执行器可行性报告已完成（#599）

- 维护者授权继续完成分析并要求报告，已形成[完整报告](docs/reference/qmt-external-signal-feasibility-report-20261105.md)：
  建议有条件采用外部决策+薄执行器作为下一阶段主线，暂缓扩大完整原生移植；不代表实盘可恢复。
- 新审查明确两个接入缺口：passorder不返回券商单号，不能直接接同步BrokerAdapter；
  尚无单号的发送unknown不能伪造OrderStatusEvent.broker_order_id，也不能变成会释放pending的拒单。
  建议增加异步发送状态事件，保留unknown的资金/数量预留，依真实回报对账。
- 报告区分session1/Local对象、前台诊断控制面与无人值守运行，给出Issue范围调整建议及
  正式模式/账户许可、生产身份、完整重启/回报和正常发布的剩余门禁。未联系券商或替用户确认权限。
  #599报告里程碑完成，整体接入尚未验收；其他Issue不据此关闭，真实账户/委托/交易姿态保持。

## 11-05：Windows持久账本与QMT接收器两阶段恢复接线（#599）

- 使用已有Python3.12.10/SQLite3.49.1，Windows私有NTFS夹具持久库与编辑器诊断接收器
  已接通。真实fixture独占锁被第二次打开拒绝（错误32），结束后释放；不是生产全局锁验收。
- 第一阶段：A正常诊断完成；B发布后子进程退出、C提交传输ACK后退出、E在接收器结束后
  未获回报，冷打开均保留unknown。三次有效期内重领被阻止；ACK不当柜台受理。
- 第二阶段重建独立接收模型/新wire会话，内存缓存为空，原账本A/B/C/E逐行保持且未重发，
  只处理新D；最终A/D诊断完成、B/C/E unknown。旧消息第二阶段已过期，不单独证明
  有效期内跨接收会话防重；这一组合仍待补验。未重启QMT/Windows或生产故障注入。
- Qt两段处理3/1条，主动取消均true，最终回调最大约1.2955/2.5620ms；独立重建4组
  响应HMAC/SHA一致。签名仅公开fixture key，生产身份及正式实盘模式/券商许可仍待验。
- 01:20两映射不存在、锁释放、驱动exit0、q0~q10全exit0/disabled/idle；q11审计随后同样
  禁用。四业务任务XML、QMT5480同启动身份/Responding=true保持。103新文件权限符合私有
  DACL；冷库quick_check=ok/delete模式、5行与收据一致，核验前后SHA保持。#600旧权限仍开放。
- [恢复接线](docs/reference/qmt-external-dispatch-recovery.md)与[机器证据](docs/reference/qmt-external-recovery-probe-20261105.json)
  已更新，专项65项通过。首次建库辅助汇总失败保留，独立冷读核对且未重跑；两阶段新记录成功。
  未接真实意图、绑定账户、下单/撤单、清KILL或启动Live/Shadow；#533/#541/#542未关闭。
  候选仍本地，#598关联/对齐前置未恢复，不能宣称CI/测试部署或生产发布完成。

## 11-04 23:20：有界回调、多消息守卫与离线恢复原型（#599）

- 新独立QUEUE模型在编辑器模拟环境调用正式schedule_run：实际8次回调处理8条固定
  合成消息，3条不同有效消息被接受；重复返回原字节，错签/乱序/过期/ID冲突拒绝。
  主动取消本组成功，最大回调约3.5119ms；只是一轮样本，不是实盘运行或性能承诺。
- 外部5740/session1核验8组响应签名，维护端独立重建HMAC/SHA一致。测试key公开固定，
  不代表生产身份认证。原生内存去重非持久；无账户/交易/市场数据输入，Live未恢复。
  [机器证据](docs/reference/qmt-external-queue-probe-20261104.json)。
- [发送状态原型](docs/reference/qmt-external-dispatch-recovery.md)以私有临时SQLite验证
  原子单次领取、两处提交后进程退出→unknown、保留尝试/ACK且禁止重领；尚未接QMT
  或生产网关，transport ACK不当券商受理。三项新增准备件无生产入口。专项共62项通过。
- 23:25映射释放/外部退出，q1~q5全exit0/disabled/idle，审计q6随后同样正常禁用；
  四业务任务XML、QMT5480同启动身份/Responding=true保持。新目录35文件仅当前SID/
  SYSTEM允许访问，旧审计继承Users读取的问题新开#600，未递归改旧权限或删历史。
- 下一步仍是隔离双端恢复接线、实际身份/租约与正式运行模式/券商许可。#533/#541/#542
  不关闭，未接真实意图流、绑定账户、下单/撤单或清KILL。候选仍本地，#598前置未解除。

## 11-04 22:24：QMT内标准共享内存双向诊断通过（#599）

- 实际2.1.33.0编辑器模拟运行Python3.12.10；json/socket/ctypes/mmap导入成功，
  WinDLL属性存在，win32pipe/win32file未安装。未调用DLL、文件或网络。旧批次编译
  日志已确认成功；旧运行辅助失败保留，新批次独立采证，不重放送达未知操作。
- 外部session1进程2792与QMT独立模型QSYS599_MMAP_20261104_A，经标准mmap命名
  共享内存完成固定合成文本ping/echo：QMT读取248字节、写回216字节；双方编号、
  字段及独立重建响应SHA一致。[分层机器证据](docs/reference/qmt-external-signal-probe-20261104.json)。
- 可继续推进外部策略+大QMT薄执行器原型；这不是正式运行回调、账户许可、应用认证
  或持久去重/重连验收。未传市场数据/订单/账户，未启动Live/Shadow、清KILL或真实交易。
- 22:26同session1只读确认映射释放、2792退出、14辅助任务disabled/idle、四业务任务
  XML保持、QMT5480同启动身份/Responding=true；审计a24随后也正常禁用。m1的.NET
  规则类错误及a21就绪拒绝保留，修正AccessRuleFactory后用m2成功，未降低DACL要求。
- 两组专项共41项通过。下一步是无交易正式回调/有界队列与恢复协议原型，再独立确认
  券商许可和真实受理回报；#533/#541/#542仍开放，候选本地准备不等于已发布。

## 11-04晚间：外部决策与大QMT执行可行性（#599）

- 维护者授权继续评估新路线；AGENTS§3.3.1精确记录诊断范围，原内置策略规则保留。
  [分层证据与下一步](docs/reference/qmt-external-signal-feasibility.md)区分OS、QMT与账户许可。
- Windows两独立外部进程命名管道5帧/5181字节逐字节双向通过、对端PID互证；
  外层汇总失败保留，另取独立收据对账成功，原因未证实，不重发已完成消息。
  这不是QMT内通道验证。离线诊断协议28项专项通过，无交易/账户调用。
- QMT20:54自然换新PID5480，按新身份重验桌面，正常关闭公告并新建独立模型
  QSYS599_CAP_20261104_A，源码回读一致且已保存。未取得编译/运行输出；运行辅助任务
  failed且精确步骤不明，送达未知先查本批次输出，不盲目再次点击运行。最新前台句柄0，
  已请维护者保持活跃RDP后继续采证；模型不含任何账户/交易/I/O调用。
- 尚未证明模块能力、QMT内IPC、当前账户许可或异步回报闭环；#533/#541/#542不关闭，
  Live/Shadow、KILL、真实委托流及账户保持。#598的GitHub合并关联/写入故障独立保留，
  本地候选不能宣称CI/测试部署/生产发布成功。

## 11-04傍晚：Issue收口与实盘恢复评估

- 全量37项逐一复核后，按实际已完成范围关闭#550（正式本地分钟读取）和#478（显式参数
  与原值保留），余35：7推进、3待验收、1就绪、24阻塞/阶段冻结。完整逐项原因及顺序见
  [#540审计矩阵](https://github.com/wanlinruo/quant-trade/issues/540#issuecomment-6627396544)。
- 今日自然daily-archive-154107-1342779：15:41:07–15:58:52 succeeded，真实
  get_local_data/fill_data=false下载5572/5572股日、7文件/1342852行，空/失败0；
  分钟校验0阻断/0已知差异、canonical写后逐键逐字段对账通过。实际业务PID1342779/UID982
  与Invocation fc49009cc64e4ee49b412b5ce3863a52的journal绑定；外层flock PID1342776
  单独记录，unit success/exit0/PID0，不把二者相等或父子关系当作已证明。
- 17:00:35–17:05:53自然tushare-sync-170035-1367079已succeeded；两项关闭不宣称
  全历史/独立真值已过，#583仍等待19:40 catalog/20点checks与19点补同步分层采证，
  #580盘前审计未发布，#586数量差异、#594的28字段warning及D-23回测源禁令保留。
  关闭依据和输入文件身份/SHA见两Issue评论，未重放业务、改市场值或旧失败。
- 真实Web UID990只读SQLite聚合：1运行/3结束模拟账户，11-04当日7事件全部completed。
  Paper/Web/observer PID3298434/2975679/3282489、NRestarts0保持；Live/Shadow inactive/PID0。
- 16:43根盘29243408384 bytes（27.24GiB）、数据盘16994512896 bytes（15.83GiB）；
  16:50 Windows C5453557760（5.08GiB）、E103288426496 bytes，QMT4968从08:53启动、
  Responding=true。当前供数与容量缓解不替代长期稳定/历史崩溃归因。
- 实盘只读恢复评估已完成；可做必需能力核验与离线准备，正式激活仍缺#541业务状态恢复、
  #542最小原生输入、#533完整运行适配/#537调度、#535未知撤单/故障安全及#589生产拦截。
  不必等所有研究/非消费历史Issue关闭，但必须有实际消费/当前版本/时效的必要证据。
  #588实际接线与#589生产联合安装均未完成，容量障碍解除不豁免维护门禁。
- [实盘检查清单](docs/reference/live-readiness.md)已纠正原“当前=10-13实盘运行中”表述，
  历史券商验证保留并明确不代表新版就绪。未启动Live/Shadow、清KILL、恢复账户、运行新
  原生交易模型、发委托/撤单或改变资金上限；本轮询问不当作正式激活授权。

## 11-04 12:40：获批21个旧制品已清理，根盘恢复至约27.30GiB

- 维护者本轮明确回复“确认”，授权固定清单21目录；清单SHA256保持
  `55294c7694c5a6e262786081accfaf42db06ab64d88e9f0fad0994491b4ddf3f`。
  执行前重盘59版本：21候选、38保护、扫描错误0，21项manifest/元数据均无漂移。
- 正式清理21项全部成功、跳过0；逐项保留requested/deleted收据；使用防符号链接攻击的
  fd相对删除，业务路径只读，38个保护版本额外只读挂载。UID0/PID1244297、
  Invocation `3bdcffef96a5407491553958e18baf4b`与journal/实际资源及终态success绑定。
- 根盘可用11064283136→29318385664 bytes，实际增加18254102528 bytes（17.000458GiB）；
  数据盘可用17033863168 bytes（约15.86GiB），其容量warning继续保留。不外推耗尽时间。
- 保留38个release完整元数据（nlink允许获批unlink自然变化）及manifest，全部current、
  最近成功补偿目标、12peer、Live主库/KILL/锁绑定前后保持。Paper/Web/observer仍为
  3298434/2975679/3282489、NRestarts0；Live/Shadow inactive/PID0，/services HTTP200。
  四个数据timer active/enabled，未停timer、未启动交易/采集、未安装新生产组件。
- 额外并行只读引用检查未取得有效证据，保留其exit1/journal/失败记录，不用于验收；
  清理执行器完整后验成功，另一次独立只读对账确认剩余38版本、获批剩余0、扫描错误0。
  全部辅助unit已停止，未reset-failed或清旧失败；详见[执行方案及验收](docs/plans/component-release-storage-review-20261104.md)。
- 永久节点审计`/data/qsys/var-deployments/issue126-release-cleanup-20261104-r1/acceptance.json`。
  容量这一直接障碍已解除，#126长期趋势治理仍开放。#588/#589源码已随PR#595合入
  ca3ca96，原始CI/测试部署/PR CI/Align/审计/最终CI均成功，生产尚未激活。
  后续仍独立满足组件维护门禁；Paper有运行账户，不能使用仅supervisor升级例外。
  28历史质量warning及今天收盘自然验收仍保留，不因清理判全系统全绿。

## 11-04：北交所边界与行情覆盖完成本地集成，容量清理待明确审批

- #589：订单、Paper/Shadow/Live及Windows执行边界统一禁止北交所新开仓；合法历史
  风险处置、撤单、回报与已受理命令去重保持。账户权限未改变，未启动停用交易组件。
- #588：网关保留全A含北交所请求集合，沪深京分别展示覆盖及源时间；旧快照显示待核验。
  空响应/全部无效/仅指数时拒绝刷新概览，保留旧字节和发布时间，由Web按原TTL提示过期。
  消费输入核对器绑定显式字段/频率/范围/证券/日期/版本/时效；仅离线诊断，不授权交易。
  实际消费者接线及自然证据尚未完成，#588/#589均不因代码合入提前关闭。
- 本地全量4449 passed、7既有skipped、2依赖warning；专项与secret scan/diff检查通过。
  现网Web current d50592a的旧协议读取器，以真实UID990、现有venv与资源/沙箱约束读取
  两组合成新快照，原字段逐值相同；未发布生产消息、未启动业务。辅助unit已停止。
  审计`/data/qsys/var-deployments/issue588-reader-compat-20261104-r1/verified.json`；
  首次环境文件守卫/错误venv路径均在启动前拒绝，修正后的r2使用独立Invocation采证。
- #126：只读盘点59个release，保留38个，候选21个；current、最近成功补偿、进程、
  unit、cron、外部软链接与挂载均核验，扫描错误0。扣除外部硬链接后预计释放17.000835GiB。
  [精确审批方案](docs/plans/component-release-storage-review-20261104.md)及机器清单已固定；
  清单SHA256为`55294c7694c5a6e262786081accfaf42db06ab64d88e9f0fad0994491b4ddf3f`。
  **尚未删除，维护者“继续推进”不是删除许可。** 执行前须重新核对引用及元数据，漂移项退出。
- 系统盘约10.31GiB、数据盘约15.86GiB；批准清理全部候选后的根盘静态预计27.31GiB。
  容量解决前不安装新生产制品；之后仍须独立满足各组件的账户/活动实例等发布门禁。
  Paper有运行账户，不能据此使用仅supervisor停用态升级例外。
- 生产数据组件仍5c2037f、Web仍d50592a；Live/Shadow inactive，Paper/Web/observer活动。
  今日盘前三次任务成功；归档15:40、同步17点/19点、catalog19:40及质检20点仍待自然运行，
  本轮不提前重放。28既有字段warning与#594分组修复计划保留。
- 本节记录代码准备与只读验收；CI、测试部署及后续合入证据以#588/#589最新评论为准，
  不能将测试部署当作上述全部生产组件已激活。

## 11-03 23:25：服务管理实际复验通过，容量失败解除

- 维护者复核发现看板仍“质检未过”和“启动/执行失败”。实际20点服务质检已执行29项，
  因旧前导零量/旧catalog结果返回1；不是没有启动。上轮只重跑catalog，旧20点质检仍会
  覆盖新catalog页面状态，遗漏了整条用户页面验收。本轮已补齐，旧记录保持。
- 同时发现/opt系统盘实际约8.0 GiB、低于既有10 GiB失败线；三组件新制品占用预算不足。
  不降阈值、不删除旧制品/回退副本。#126按#540直接依赖恢复最小容量处理。
- 仅将`/home/ubuntu/.cache/uv`、`.cache/pip`、`.npm`、`scratch`、`node-sync-backup`
  可恢复迁至`/data/qsys/var-prod/host-maintenance-20261103-r2/retained/`，原路径保留软链接。
  85518条文件/目录基线及字节SHA、owner/group/mode/ACL/mtime逐项核验，11个旧Git工作树
  HEAD/status保持；安装制品/current/补偿目标、4558个仓库Parquet版本及KILL/主库/12peer保持。
  root空闲8591892480→11061198848 bytes，实际回收2469306368 bytes；数据盘余
  17035988992 bytes。Ubuntu真实uv cache/pip list及离线idna安装探针通过，无业务启动。
- r1因浏览器正在使用`.cache/ms-playwright`被引用守卫拒绝、零迁移；r2缩小到明确空闲路径，
  新Invocation成功，未重放r1/停其他应用。审计`issue126-system-space-20261103-r2`的
  requested/actual-running/terminal/journal/verified齐全，辅助unit已停止，旧失败保留。
- 实际`qsys-service-checks.service`重跑新记录`service-checks-232553-967304`：29检查、
  数据失败0、unit success/exit0/PID0，ServiceRun warning；实际UID993/PID967304与
  Invocation=`fb0bcaa6005e4449a69fe2bee6e551c8`的journal绑定核验。4558个仓库/QA文件、
  其他11peer、KILL/主库与旧ServiceRun字节保持，未重放采集或恢复交易。
- 本次人工验收用临时只读配置绑定仅关闭对外通知；逐项证明其他配置值与原件相同、原件SHA
  不变，并提前设置节点恢复保护。运行结束已移除临时绑定、恢复原通知，保护timer inactive；
  三个业务timer仍active/enabled。原始私有配置/副本不入Git/Issue/公开日志。
- 真实生产`/services`页面：今日归档“成功”，catalog与service-checks“有警告×2”，
  三项进程均“正常”，本日启动/执行失败提示消失。28既有字段warning、两盘容量warning及
  留存策略warning保留；Live事件1条failed属于策略域/affects_run=false，主动停用告警
  语义由#482承接，不伪报交易恢复或全部全绿。Live/Shadow保持停用。
- 系统盘仅约10.3 GiB，下一次独立制品安装前必须先核算新增物理空间；不得再连续发布使其
  跌破10 GiB。缓存/工作区新绑定属于永久保留运行资产，不按旧路径/目录名删除。
  展示名称的历史修复关联和启动/执行失败精度另由[#592](https://github.com/wanlinruo/quant-trade/issues/592)
  承接，不夹带新组件发布。#583继续等待自然任务验收；#588/#589候选仍独立保存。
  服务验收收据`/data/qsys/var-deployments/issue583-service-management-20261103-r1/verified.json`。

## 11-03 22点：指标字典修复已发布并实际通过，既有warning保留

- PR [#590](https://github.com/wanlinruo/quant-trade/pull/590)合入`5c2037fdd4afd427e7d43594d2f09026177c68fa`，
  原始CI41634877096、测试部署41635091913/Deployment7518565556/节点github-41635091913-1、
  PR CI41650280489、Align41650433043、审计41650432974、最终CI41650505269均success；
  测试节点实际validate_record及SHA/tree/run-id核验，Codex代行自审已如实记PR正文。
- catalog-verify/service-checks正式安装同一5c2037f：workflow41651261215/41651795823、
  Deployment7521021097/7521102439及节点record实际validate_record三方success。
  两timer恢复active/enabled，节点侧恢复保护收尾；未启动/恢复交易或重放采集。
- 按维护者明确要求启动实际`qsys-catalog-verify.service`，新ServiceRun
  `catalog-verify-221822-942857`：638结果，610 passed/0 blocking/28既有warning；
  B2的10字段全部passed、检查5570标的日、违规0。表11绿1黄、关系7绿且缺证据0；
  实际unit Result=success/exit0/PID0，InvocationID=`53d635ca136d413c82bf416f197c7995`。
  ServiceRun如实warning，不能宣称全历史或整个页面全绿，也未重跑20点service-checks任务。
- QA三表原65773/1140/126行逐值保持，分别新增638/60/7行；旧ServiceRun字节保持。
  4555个市场Parquet物理版本、KILL/Live主库、其他11个peer及current绑定保持。
  事实与反例见[一致性契约](docs/reference/minute-zero-consistency.md)，实测回执在
  `/data/qsys/var-deployments/issue583-zero-consistency-20261103-r1/task-verified.json`。
- 必要消费者tushare-sync也正式安装5c2037f：workflow41653138392/Deployment7521307183/
  节点record实际validate_record三方success。原timer恢复active/enabled，原19点unit终态、
  12peer、全部4558个Parquet版本及KILL/主库保持；未运行收盘采集。实际UID983的
  安装模块正反夹具及增量摘要探针通过（仅内存，不代表自然同步完成）。
  #583等待自然任务采证，#586数量差异、D-23历史禁令及28警告保持。
  #588/#589未发布候选单独保留，不夹带本次修复；Live/Shadow继续停用。

## 11-03晚：当日原生对照通过，成交快照量差由#586承接

- #583当天12个异常样本及3个正对照完成45次原生缓存只读调用，15组False/True
  各241行相同；15组tick缓存为空，不能推定没有成交。QMT288身份保持，未下载/订阅/交易。
  原生与raw/canonical的09:30–09:40片段30组逐值相同、两分区SHA前后保持，
  不外推全日独立市场真值。取回结果SCP超时后按原执行SHA独立只读取回，没有重跑探针。
- 腾讯沪深成交快照给出首正量窗口观察，300205/300555/600107量额原样相同；
  688089.SH 09:32这根原生/仓库volume151，腾讯09:31:01及09:31:46快照量115+37=152，
  金额均115795，差异保留由[#586](https://github.com/wanlinruo/quant-trade/issues/586)核验。
  不按快照最新价重建分钟OHLC，不换单位/舍入放行；北交所公开成交证据仍不足。
- 本轮候选修复同步警告只显示字段总数的问题：新ServiceRun将绑定实际增量批次，分别列
  blocking字段数、规则数、每规则原始违规/检查数和样本路径，不相乘或求和冒充独立问题数。
  质量判据、失败结果及同步warning/exit0契约保持。候选尚未生产安装，Tushare仍7d5e2c4，
  catalog/checks仍072240f；不能称当日数据质量通过或#583完成。
- 证据、辅助首次边界聚合失败及临时元数据覆盖限制见
  [成交快照核验](docs/reference/native-minute-trades-review-20261103.md)。
  #583继续in-progress，#586 blocked等数量/快照语义证据；不启动Live/Shadow或重放生产任务。

## 11-03 17点：收盘同步完成，增量质检命中12个前导零量标的日

- 本节覆盖下文“catalog/checks尚未发布”和“模拟待11-02验收”的历史状态。
  当前主线仍是[数据→模拟→实盘 #540](https://github.com/wanlinruo/quant-trade/issues/540)，
  分钟质量裁决由[#583](https://github.com/wanlinruo/quant-trade/issues/583)承接。
- 今天17点自然运行`tushare-sync-170128-843932`：17:01:28–17:06:41，
  ServiceRun为warning，20步骤中19成功、仅“增量质检”warning；实际unit于17:06:45
  结束，Result=success/exit0。日线5570、限价5655、停牌9、ST记录5570、送配4，
  派生刷新及canonical 11张表写后对账通过，晚到新股分钟收敛步骤成功。
  停牌本入口未决观测0；不外推消除#551历史差异。涨停列表本次返回0行，步骤成功
  仅代表执行完成，不在本次只读排查中判定该空响应的市场完整性。
- 精确增量批次`20261103T170558441444`共558条字段结果：548 passed、10 blocking。
  10条都是`B2.canonical_qmt_leading_padding`映射到canonical分钟表的10列，
  共用同一份12行样本；不是10个独立问题，也不是沿用昨天603828.SH的一个案例。
  本次检查5570个标的日：300205.SZ、300555.SZ、600107.SH、688089.SH、
  920008.BJ、920030.BJ、920055.BJ、920066.BJ、920204.BJ、920405.BJ、
  920931.BJ、920950.BJ在09:30起连续2–5根零量，首根正量标签09:32–09:35。
  样本`artifacts/metric_checks/20261103T170558441444/B2.canonical_qmt_leading_padding.parquet`，
  SHA256=`98264372edecc8b3c3752f5dc070d42e78cfb993f67be6847a3f3d17bd6e3833`。
- Tushare当前仍7d5e2c4，内置旧诊断写“前导零填充”；这不是市场成因已证实。
  今早发布仅升级catalog/checks，未升级数据写入器。同步exit0只证明采集发布完成，
  ServiceRun warning明确保留质量未通过；不把548条passed称为全历史或表级/关系级健康。
  未重跑同步、改数据、清失败或放宽规则。19点同步及19:40/20:00自然检查尚待发生。
- 11-03早已将catalog-verify/service-checks正式安装到main/test基线
  `072240f22c94cf327eb60ccf2f2b611b2c2c1a19`：workflow41548559796/41548722964、
  Deployment7506459175/7506479664及实际节点record/validate_record三方success；
  原timer已恢复active/enabled。正式发布详见[#583记录](https://github.com/wanlinruo/quant-trade/issues/583#issuecomment-6592017562)。
  新代码仅纠正未经证实的填充诊断，质量判据仍保持；本轮17点增量运行由旧Tushare组件执行。
- #539/#579已按精确范围关闭：11-02起盘/竞价/收盘自然报告全部accepted，
  7个要求事件completed、旧254事件保持，20点Paper步骤succeeded，账户1 running/3 ended。
  全天模拟通过不替代独立行情质量与实盘验收，也不证明历史CANTOPEN唯一成因。
- 本次17:35后只读现场：Paper3298434/Web2975679/observer3282489均active/NRestarts0，
  Live inactive/PID0；没有账户恢复或业务重放。Shadow停用、原KILL/主库保持的最近
  精确审计为今早#583发布收尾，本次未重新计算其字节指纹，不能以进程状态代替该证据。
- #550/#478继续validation，#580仍缺premarket覆盖；#543/#545继续QMT更替与容量
  长期观察，#482继续维护/停用告警语义。#541/#542/#535等实盘门禁未解除，
  不启动Live/Shadow或扩研究。下一步核验当日竞价/分钟边界事实，并读取后续自然质量
  记录；不依据零量形状删行、不平移分钟标签、不以诊断发布或同源一致自动放行。

## 11-03：公开分钟观察已取得，正对照不支持直接放行

- #583恢复in-progress：腾讯五日分钟与新浪1分钟共64份重放响应，29例首笔正量
  标签均与raw一致；但三个沪深京正对照腾讯09:30已正量，新浪均首标09:31。
  不能把新浪缺09:30行当竞价无成交，不能按命中样本的一致率裁定规则误报。
- 新增纯离线重放与反例，v2实际结果requires_review=true/非零退出，明确
  production_quality_pass=false；不联网、不写warehouse、不接现有质量放行。
  北交所腾讯缺金额保留null；没有数量单位换算、时间偏移、容差或值填充。
- 603828.SH的09:35新浪与QMT/Tencent价格也不同，未归因；底层供数链独立性
  未证明。公开逐笔页索引不是成交，历史明细入口没有交付可用数据，不用HTTP200
  或空表证明无交易。原始响应/首版及v2审计在Mac持久r1/r2目录保留并核SHA，
  说明见docs/reference/public-minute-prefix-review.md。
- 本轮代码/CI/PR状态以#583最新评论为准，不关闭#583/#550/#478，不改生产
  失败记录、数据源或交易姿态。生产catalog仍ae29、checks/归档/主同步仍7d5，
  上轮#584虽合入但未生产发布；Live继续inactive/disabled，原KILL/主库保持。

## 11-02晚：全天模拟通过，数据新质量阻断由#583承接

- #579/#539/#508已按各自精确范围关闭：自然模拟起盘/竞价/收盘7事件及旧254事件保持、
  连接释放修复与有限重试的自然传输证据通过；历史CANTOPEN唯一触发与10-26认证前
  断开仍未直接归因，不因关闭抹去。当前1 running/3 ended，不恢复旧账户。
- 自然归档5571股日/1342611行及canonical逐键对账成功；但17/19点主同步warning、
  19:40 catalog/20:00 checks真实failed。29只首根零量中28只单根，603828.SH四根；
  10个blocking字段结果均来自同一B2规则/该一个标的日。新#583是#540直接依赖，
  不按形状删除市场行，不用协议accepted覆盖质量失败。
- #583五组原生False/True结果及raw/canonical逐值相等；tick缓存为空，既有Tushare
  stk_auction_o单次实测code40203/permission_denied，尚缺独立市场事实。参考
  docs/reference/native-minute-zero-review.md及同目录结构化摘要。候选只修正诊断断言、
  保留全部判据/阻断/失败传播；代码/CI合入与生产安装分别记，以Issue最新记录为准。
- #580两轮自然审计协议通过但premarket未覆盖，#550/#478受#583质量门禁仍validation。
  当前32开放（5推进/3验收/24依赖或阶段冻结）。Live inactive/disabled/PID0、Shadow
  inactive，KILL/主库原绑定保持；数据三组件仍7d5，Paper2e59。QMT23:17为2044、
  20:54新更替后响应正常，本次退出状态未取证。没有新发布、业务重放或删除副本。


## 11-01晚：#580三组件正式发布，周一自然验收待执行

- 维护者明确确认后，按service-checks → daily-archive → tushare-sync串行发布到
  `7d5e2c41c0eca38c4d8ac3f2815c8674c028a6e0`。正式workflow分别41308083182 /
  41308382815 / 41308602922，Deployment7473199470 / 7473240749 / 7473271210，
  节点`issue580-production-20261101-r1-<组件>/record.json`均经实际validate_record，
  三方success后才推进下一项；安装UID993读取新warning夹具正确，UID982/983模块读取通过。
- 本项增加停复牌写前完整旧新观测/物理版本、写后独立收据、跨入口未决差异与warning传播，
  不裁定#551的历史8/7行哪版正确，不恢复/删除市场行。归档前拒绝较小响应与主同步整日
  替换的既有策略保持；accepted仅指本入口本次接受，后次成功不自动消除历史差异。
- premarket-limits仍`37bb3c4313dbebabe6e2f486eca99b00cce74561`。停用Live时没有其维护
  例外，本轮不发布、不扩门禁；不能称全部生产者均已启用审计。#580仍validation，#551仍blocked。
- 三个业务timer已恢复原active/enabled，oneshot终态、12个业务/数据peer身份、4554个
  Parquet物理版本、原KILL/Live主库保持。Live inactive/disabled/PID0、Paper3298434保持；
  未人工运行采集、恢复账户、启动Shadow/Live或改变QMT恢复配置。未用物理版本冒充逐值质检。
- 11-02数据自然只读观察迁到`issue550-natural-download-20261101-r4`，16:30/19:20两timer
  active；绑定SHA `5e9bb87125c2cef411d2a202b1597482f8e5133a8bb93e87543bb784a0c2e530`，
  verifier仍`0543aff6b68860aa660da0c775907758e0b6be161e647ad56544501c1c372dac`。
  新生产current venv/UID982/实际执行契约的只读预检通过；accepted=false明确不是自然通过。
  旧r3两个timer inactive，定义/绑定/失败证据保留；#579的09:20/10:00/16:00三个Paper
  timer不改。模拟账户快照以#579最新绑定与节点事实为准，不按历史“两账户恢复”记录重启账户。
- 准备时四个真实身份unit夹具通过，当前Web/日报读取器兼容warning/progress及去重；
  三个正式dry-run全部success。首次只读workflow GET断连与timer最终审计过严失败均保留，
  后者因既有2/3分钟随机延迟重算NextElapse导致；独立对账、不重放start。规则已补入AGENTS。
- 发布审计`/data/qsys/var-deployments/issue580-production-20261101-r1/`；候选与unit沙箱
  `issue580-sandbox-20261101-r1/`。原制品/审计/市场事实保持，不清失败历史。
  下一步等周一实际分钟下载与新观测、模拟起盘/竞价/收盘；代码/安装成功不能替代自然验收。


## 10-30 23:43：数据自然同步通过；模拟已恢复待11-02生效

- **当前生产**：归档/Tushare=a132，Web=d505，catalog=ae29，checks=0a67，observer=868b50a，
  Paper=2e59dacef9e48a6dc432980a213a991e04cb71e1，可信controller v41；共享checkout505保持。
  Live inactive/disabled/PID0、原KILL/主库保持，Shadow不启动。下文“候选/未安装”为历史快照。
- #549经PR#576正式安装，workflow41073410855/Deployment7440999417/实际record三方success，
  真实观察器已能将error账户判down；本项关闭不代表账户恢复。#482首阶段已生产，完整语义仍待。
- #539经PR#577正式安装，稳定本地4269 passed/7既有skipped、CI/test/PR/Align/最终CI通过；
  正式workflow41079514822/Deployment7441920545/实际validate_record三方success。
  确定性关闭事务连接、无事务WAL/SHM锚点，真实UID/只读沙箱和完整执行契约下4次Runtime/
  8次tick通过；禁用GC2万次现场FD保持8，仍不附会历史两次CANTOPEN的唯一触发原因。
- 23:31使用既有resume接口，只恢复两个error为scheduled/2026-11-02（Tushare正式日历）；
  原1 ended保持，原资金/配置/状态快照及八张历史表逐值保持，17订单/60成交/254事件/
  249策略run/547945权益等不变，无pending/持仓，不重放旧事件。私有原始一致性备份0600保留。
- 新supervisor3298434、workers3302390/3302391均2e59，FD8/11/11；23:43 HTTP页面2待生效/
  1已结束，原十peer身份/KILL/主库保持。当前正常心跳仅证明未来生效准备，不是周一业务已通过。
- 节点只读timer已安排11-02 09:20起盘、10:00竞价、16:00收盘；绑定精确Paper2e59、原254事件
  指纹、实际事件状态及Live停用。节点重启或Paper版本变化须复核临时timer；Mac休眠不影响节点执行。
  #539保持status:validation开放，原错误历史保留；#550/#478等新分钟自然实际下载，不能据无缺口跳过关闭。
- 审计根 `/data/qsys/var-deployments/issue539-paper-release-20261030/`：
  runtime-preproduction-verified、post-publication-verified、resume-final-verified、
  natural-observers-scheduled.json；备份/原失败/恢复收据在私有resume-output各目录。
  r1审计Row序列化、r2静态WAL冷读均拒绝于状态写前（transition=0），r3用tuple和SHA相同
  私有可写缓存验证原备份后成功；原件/失败记录不动，无immutable/放宽权限/业务写重放。
- 19点a132同步/19:25独立报告accepted；19:40字典638/0阻断/28警告、表11绿1黄/关系7绿，
  20点checks warning含停用Live无事件、系统盘16.2GiB与9到期副本，均不清理或改历史转绿。
- QMT22:14/22:21为PID704，自20:54更替、父字段5340，原因未定，C约3.64GiB；#543/#545仅
  数据/模拟连续性支持。下一重点是自然模拟/分钟验收，不启动实盘、扩研究或大架构。

## 10-30 22:41后：观察器修复完成，继续Paper连接生命周期

- #549已PR#576/main868b50a正式发布，workflow41073410855/Deployment7440999417/实际
  record三方success；新observer3282489记录Paper process healthy/business down、
  strategy_account_error。#549已关闭，#482仅首阶段已安装，完整维护语义仍未完成。
- 全部模拟表逐值/数量、其他十服务身份与原KILL/Live主库保持；两个error账户未恢复。
- #539候选确定性关闭事务连接，单个无事务锚点保持只读WAL/SHM。关闭GC/1024描述符
  离线对照旧代码509次CANTOPEN、候选2万次FD稳定7；正常GC2万次未复现，不能判历史唯一根因。
- v41候选仅允许已安装active且无running/scheduled账户、cgroup只有supervisor的Paper
  升级；九表逐值指纹/九peer/KILL/主库保持。不恢复账户或实盘，不放宽其他组件门禁。
  生产仍v40/Paper987175e；候选不是已安装，最新CI/发布进展以#539最新评论为准。

## 10-30晚：数据自然同步通过，推进模拟健康修复

- a132两个数据组件已17:43正式发布，三方证据及原timer恢复见#550最新评论；
  19点同步与19:25独立观察accepted=true，19:40字典638/0阻断/28警告、表11绿1黄/关系7绿，
  20点checks warning含停用Live无事件、系统盘及到期副本；不称全历史健康。
- #550/#478保持validation，新分钟下载器尚无实际自然下载证据；下一归档11-02。
- 维护者要求继续后，#539/#549解除排期冻结。Paper仍2 error/1 ended，PID2408585/重启2，
  observer34c7仍误报healthy；未恢复账户、未重放事件、Live仍inactive/KILL保持。
- 找到#549旧候选bce8d70仅在本地分支，本轮从最新test承接必要代码/回归，正常CI/test/PR；
  v40候选仅允许已安装active observer在停用Live态升级，九peer/KILL/主库保持。
  当前生产仍v39，候选不是已安装；发布与实际验收进度以#549最新记录为准。
- #539两次自然CANTOPEN，当前真实ACL存在但故障根因未证明，不作猜测性权限修复。
- QMT22:14为PID704，自20:54更替；C约3.64GiB，原因未明，#543/#545仅直接连续性支持。


## 10-30下午：#558已正式启用；#550/#478本地分钟读取修复推进中

- 维护者明确要求尽快恢复数据链路和模拟链路，保持数据→模拟→实盘；允许本次修复在
  非交易时段按正常门禁尝试生产发布。不开实盘、研究或其他扩展线，不清KILL或历史副本。
- #558维护者具体确认后，Tushare/Web均已正式d50592a；workflow40987795073/40988038793、
  Deployment7428619734/7428653987与节点实际record三方success。独立Tushare现金策略
  绑定已生效，历史61087条和4552业务文件保持，原timer active/enabled；现金页面验收通过。
  10-30 17:25精确d505/Tushare只读观察保留，结果未产生前不关闭或声称自然通过。
  审计根issue558-cash-activation-20261030；早期下文“尚未启用”为历史快照。
- 全部34开放Issue于10:04–10:14核验并更新11个关键项，矩阵在#540评论6513262104。
  后续#550/#478作为直接数据依赖进入in-progress，不把blocked当成全部当前生产故障。
- #550/#478固定已收盘样本只读探针：14:29/15:11在实际Python3.11.9、QMT5340/58610
  完成；5组有缓存样本各482行，单日/区间一致，8组True/False相同，仅证明所查缓存。
  301139.SZ RPC仍零价、本地真实；普通股两API存在精确Float64差别，volume逐值相同。
  详见docs/reference/qmt-local-minute-reads.md及两份JSON，未把小差异舍入成相同。
- 候选下载后只读get_local_data，显式count=-1/dividend_type=none/fill_data=False；
  校验真实容器、双时间、整数精度，保留零量和null，不回退RPC。专项/全量、实际Windows
  候选与正常CI/test/PR及生产证据以#550最新记录为准，尚不能当作生产已安装。
- 可信controller v39候选仅增已安装daily-archive的停用Live维护路径：timer inactive、
  任务终态/PID0、原KILL/状态绑定及九peer身份精确保持，不开放其他组件维护。
- Windows Gateway单minute hash与两个消费者必须联合编排；不在自然归档/同步运行时
  改白名单，不使旧消费者新旧hash失配。C盘约3.8–4 GiB，避免大备份/下载；不改QMT恢复。
- 当前Paper2408585/NRestarts2，2 error/1 ended且旧observer仍误报healthy；Live仍停用。
  数据阶段后联合#539/#549，不能因行情fresh或本次软件发布宣布模拟账户恢复。

## 10-30零时：#558兼容基线已生产安装，二阶段选源候选准备中

- PR#573/main2d2d25b正常合并：本地4203 passed/7既有skipped，push CI40901320700、
  test部署40901675631/Deployment7416547828/实际record、PR CI40901985965、Align/
  审计40902213655/40902213646、最终CI40902285908全部通过。
- Tushare current已2d2d25b。正式workflow40904678994、Deployment7417003176、实际
  github/issue558-baseline-20261029-tushare-sync/record.json validate_record三方success。
  ZIP artifact12909577877/digest b136c6fb...一致。规范/控制器v38完全不变，无白名单更新。
- 实际UID983/PID2774543/Invocation6d2333f8...的受限探针捕获运行中MainPID、资源、
  完整环境（含QSYS_CATALOG_SOURCE_ROOT）与UMask0027；真实文件0640、7事件全列相同/
  Arrow schema及CLI守卫先于API/原始同步通过。只写独立审计shadow，真实API/生产写0。
- 23:51验收4552个业务Parquet物理版本、全部受保护unit身份、生产检查点字节完全保持，
  cash-source-policy生产文件不存在，unit仍QMT默认；未人工跑全日任务。timer恢复active/
  enabled，下一自然10-30 17点。代码安装不等于新源已启用或新版本自然验收已发生。
- 辅助准备首次Git archive缺Git元数据被verifier拒绝，改精确main worktree；环境复制白名单
  初漏Catalog source-root在启动前拒绝。原记录/旧目录保留，不重跑CI/业务，不放松验证或
  私有shadow权限。timer暂停后is-active exit3只是已inactive，已读真实属性核验，无重放。
- 二阶段仅准备Tushare显式cash-source参数和Web增量血缘；运行代码/lock不变，N/N-1均为
  已识别独立策略绑定的基线。实际节点unit/选源仍未改，启用前按AGENTS.md §4提交具体
  未来现金范围确认；不再请求已完成八条历史修复授权。模拟/实盘及原KILL保持。
  审计根/data/qsys/var-deployments/issue558-cash-baseline-20261029/。

## 10-29 22:57自然验收完成；#558未来现金候选准备中

- 19:00 Tushare自然run190050-2691078 succeeded，19:40 catalog自然run194003-2702538
  warning/exit0：638字段/0阻断/28既有warning，表10绿2黄、关系7绿。19:55报告所有
  版本/三层绑定/当前输入/真实timer及Invocation检查passed；原13:23失败与关系red保留。
- 20:00 checks自然run200003-2708253 warning/exit0，正确识别638字段已执行且有warning；
  20:10目标消费者报告passed。停用Live无事件及9项到期副本提醒仍存在，不称全业务健康。
  20:30日报自然succeeded。22:57实际两个unit inactive/dead/Resultsuccess/exit0，HTTP
  服务详情/精确自然Run页和关系页200，最新run可见、关系green。
- #561/#562/#568/#570自然验收完成后关闭，开放34项；#540继续剩余数据工作，
  #558现in-progress。#482第一阶段PR#572已合并main ab02992，本地4179/全部CI/test/
  PR/Align/最终CI通过，但生产observer仍34c7b7b/PID3933655，未发布，完整维护语义未完。
- #558新增现金选源候选：QMT原始快照/送转配股保持，Tushare唯一实施cash_div_tax直取；
  缺证据/null/冲突/超时保留候选并拒绝发布。先发布独立策略守卫兼容基线，unit/QMT
  默认保持；二阶段才准备显式flag与增量血缘，避免补偿回不认识新策略的3fac旧代码。
  当前7事件全与Tushare相等；Mac真实收据复放全列相同、append0，原61087行/物理schema/
  b99c9dff字节保持。候选尚未生产安装或启用，最新CI/审查及具体授权以#558记录为准。
  稳定基线全量4203 passed/7既有skipped/2既有依赖warnings，真实installer失败补偿
  控制流程+实际临时文件验证绑定保留（不冒充生产权限/补偿演练）。
  详细候选边界与下一步门禁见docs/plans/dividend-data-recovery.md首节。
- catalog/Web仍ae29a7e、checks0a67a68、Tushare3facceb、controller v38；当前#558候选
  第一阶段节点规格保持；二阶段新增flag前须按精确main审查/备份白名单，不绕校验。
  Paper PID2408585/NRestarts2、Live PID0/原KILL保持；不因软件或自然数据验收解除交易门禁。

## 10-29 17:20自然采证通过；#482告警文案第一阶段候选

- 今天自然Tushare run `tushare-sync-170041-2653775`于17:00:41–17:05:42
  succeeded，20步骤全部成功。17:20只读报告accepted=true，真实UID983、业务Invocation
  ebdd00e36f3d4bcaa481ff2f13385f00；5942证券完整响应、7事件/新增2，5935 empty/7 done。
  报告`issue555-dividend-scope-20261028/next-natural-output/report.json`，business_starts/
  warehouse_writes/api_calls均0。只证明本次自然数据链，19:40/20:00尚待各自验收。
- service-checks已正式安装0a67a68，controller v38，workflow40833203866、Deployment
  7406190491及节点实际validate_record三方成功；真实UID993兼容读取通过，未人工运行
  全日检查。20:00业务/20:10只读观察保持。下文“候选未安装”均为较早快照。
- catalog/Web仍ae29a7e，Tushare3facceb、共享checkout505d7e8保持；原13:23关系批次
  red与failed历史保留。新自然数据改变输入后须重新取绑定证据，不拿17点增量替代三层
  全量验收。19:40业务/19:55只读采证保持，不重跑或reset-failed。
- 维护者认可全系统审计建议后，#482只先准备事实文案：区分timer未启用、任务执行失败、
  临时维护/验收失败；重跑启动和failed列表消失不称业务恢复，午夜未到事件时间不称实盘
  恢复。保持严重级别/边沿去重，未实现有界维护窗口或停用Live完整语义；本项不关闭。
  本地全量4179 passed/7既有skipped/2既有依赖warnings；CI/发布以Issue最新记录为准。
  生产observer尚未改动，先完成代码审查与正常集成，不干扰今晚观察中的实际进程基线。
- #545只读目录扫描发现旧userdata_mini有两条75,497,752字节队列分别10-28/29 15:10
  更新，不能再说目录完全静止；旧agent仍disabled/最后运行10-27，当前Python为现存
  市场进程，具体文件使用者未证实，不因此重启或清理。QMT PID2512/10-29 08:53:54，
  Responding=true；C约3.99GiB/E约98.75GiB，不能把逻辑长度作为可释放物理空间。
- 全部40项Issue已审计更新，#475原迁移natural_acceptance_passed=true和#548安全措施
  完成后关闭；余38项保留独立门禁。#482进入候选实施，其余排期详见#540审计矩阵。
  #539/#549模拟问题和实盘原KILL/停用姿态保持，未清理备份或覆盖数据。

## 10-29 13:42：#561/#568已生产发布，保留一条批次红项；#570消费兼容候选中

- 最终main/test ae29a7e经#565/#567/#569正常合并及全部CI/test/PR/Align/最终CI通过，
  本地4158 passed/7既有skipped。catalog/Web current均ae29a7e；正式workflow分别
  40822371810/40823805986、Deployment7404678256/7404883582及节点实际validate_record
  三方success。controller v37、共享checkout505d7e8、Tushare3facceb及原KILL/Live主库保持。
- 生产人工run catalog-verify-132210-2546373，UID984/PID2546373/Invocation4aa555b4...，
  实际运行中完整unit契约绑定。字段638/0阻断/28既有warning、表10绿2黄、关系6绿1红；
  关系独立failed，任务failed/exit1，MemoryPeak2147901440。60+7绑定有效，旧QA列和
  重复次数全部保留；业务Parquet版本/配置/baseline/KILL/Live主库SHA保持。
- 这验证软件正确拦截已知批次红项，**数据质量尚未全部通过**。限价09:24与昨天市场标记
  20:06不同批次，旧代码同输入同red；不放宽约束、不伪造发布时间或重写业务仅为变绿。
  HTTP估值green/关系red/服务精确Run独立失败步骤均核验；两次页面探针选错列表页已改用
  真实Run URL，没有改产品或重复部署。#566已关闭；#561/#568转monitoring，#540仍in-progress。
- catalog timer恢复active/enabled，19:40自然运行；原17:20 Tushare观察保持，19:55新增
  精确版本/三层绑定/timer触发与invocation只读采证。原绑定8d的#562 19:50辅助timer明确
  被替代，原脚本/请求/失败保留。尚未发生自然结果，不算自然成功。
- 审计根/data/qsys/var-deployments/issue561-versions-r3-20261029/；前两候选失败/拒绝、
  副本与旧制品全保留。r3隔离未捕获live/proc，已以仍loaded精确unit属性+唯一invocation
  journal PID2530575/UID984绑定，无GC默认值或缺失MemoryPeak冒充，未重跑。
- 旧日报UID988读取638/28/0正常。旧service-checks UID993覆盖454/字段读取均通过，但将
  已完成warning误报未执行；#570作为必要数据消费者兼容项实施。候选区分已执行warning与
  真正failed/中断/失败步骤，controller v38候选只允许已安装idle service-checks停用Live
  升级，暂停自身timer并保护九peer；未安装生产。模拟/实盘不恢复，Paper问题仍冻结。

## 10-29 #568关系失败传播修复中，第二候选未激活

- #567/main b215c39经全部CI/test/PR/Align/最终CI通过，复用未变controller v37；
  r2隔离run catalog-verify-115709-2509955字段638/阻断0/既有warning28，表10绿2黄，
  但原始关系是6绿1红，整体仍warning/exit0，后置验收已拒绝激活。此前会话曾未核对原始
  关系而报7绿，已明确更正，不作为证据。60+7版本绑定全部有效，三类旧QA列/重复次数
  保留、生产全部Parquet物理版本保持。catalog timer恢复active/enabled。
- 红项price_limit_daily→market_flags_daily为现有同批次约束：源文件10-29 09:24更新，
  下游10-28 20:06；5653/5570行，matched5570/orphan0/重复0/null0，只有downstream_stale。
  当前生产8d8307e实际函数只读同输入同样red，两函数源码与候选逐字相同，非本轮回归。
  不放宽约束，不伪造按日发布时间，也不为变绿重写业务数据或采未收盘当日。
- #568候选显式返回关系摘要，CLI独立关系步骤，red/gray/必需关系缺失均failed/exit1，
  yellow warning；五类实际CLI/ServiceRun回归和全量4158 passed/7既有skipped通过。
  新制品/新审计根/新run-id验证“正确拦截已知红项”，不把软件验收算数据全通过；正常
  收盘批次仍需自然重验。生产仍catalog8d8307e/Web9727d80，Tushare3facceb、Live/KILL保持。
- r2审计根/data/qsys/var-deployments/issue561-versions-r2-20261029/，candidate-rejected.json
  和old-production-relation-baseline.json留存。#566结果推断已合入但未生产生效，原失败保留。

## 10-29 10:43：#561首次隔离失败，生产未激活，前进兼容修复中

- PR#565/main b112c93经CI/test/PR/Align/最终CI全部success；controller v37已可信安装，
  原v36完整备份、私有控制配置/dispatcher/节点catalog和Web规格未变。两个候选只staging，
  catalog/web dry-run40807820654/40807992728通过，生产仍catalog8d8307e/Web9727d80。
- 真实隔离run catalog-verify-104222-2470972，UID984/PID2470972/Invocationabb992f6...，
  完整unit契约绑定，10:43:05 failed/exit1。旧QA缺新元数据列触发C6异常，随后runner在
  第100行后首次样本路径字符串出现而Polars推断失败。原失败/样本/记录全部留存；生产
  所有Parquet物理版本保持。catalog timer已恢复active/enabled，19:40正常旧版本运行。
- 审计根/data/qsys/var-deployments/issue561-versions-20261029/。前进候选只兼容两QA表的
  精确旧Arrow schema缺input_version，其他缺列/错类型及市场表契约不放宽；runner依据
  显式Arrow schema落结果，增加晚出现样本回归。新制品/新run-id重验，不能重写旧失败。
- 今晨08:58 Paper出现新自然SQLITE_CANTOPEN退出并由systemd自动拉起，当前PID2408585/
  NRestarts2，证据已补#539/#549；发生在本轮生产动作之前，未恢复账户。后续peer核验使用
  当前真实身份，不沿用昨晚634053。Tushare3facceb、Live PID0/KILL及自然观察计划保持。

## 10-29 #561证据版本绑定候选推进（生产尚未切换）

- 维护者再次全权授权继续#540。#561本地候选为表/关系质量元数据加入input_version，
  按实际读集与范围绑定源/目标物理身份，前后防漂移；Web/规范门禁区分当前适用性与历史。
  缺绑定/输入改变为gray待重验，原结果与TTL保留；市场事实不变，旧消费者仍按旧列投影读取。
- 日历API响应单独摘要；分钟日/年选择复用实际query，含覆盖候选/付费manifest和目标freshness。
  保留19:40受控重验，不给Tushare主链追加全量扫描。生产先catalog新证据，后Web读取器。
- 必要controller v37候选只增加原已安装active Web的停机Live维护路径，绑定原KILL/主库/
  稳定绑定，保护八peer并验HTTP；不启动inactive Web或交易核心。专项通过，全量/CI/发布
  以#561最新记录为准。本段是准备快照，不证明生产生效。
- 10:10节点仍catalog8d8307e、Tushare3facceb、controller v36；Live PID0；业务timer与
  17:20/19:50观察timer active。自然结果尚未发生，保持此前#562验收证据，不重复部署旧制品。

## 10-29 01:44：#562正式发布与人工验收完成，等自然采证

- 维护者“继续推进540，我全权授权”下，PR#563正常合并，catalog current现为
  `8d8307e9b2c6cb363facd9beb54dbf7a18d7fa4d`，可信controller v36已安装并留存v35。
  正式workflow40737455712、Deployment7392827014、节点实际validate_record三方success；
  审计根`/data/qsys/var-deployments/issue562-catalog-20261029/`，ZIP digest f965ba12...一致。
- 正式人工批次`catalog-verify-014221-2279566`，UID984/PID2279566/Invocation0dc17780...，
  真实unit契约绑定。session10-28，warning/exit0，字段636/阻断0/既有warning28，步骤warning；
  表级10绿2黄、关系7绿；C1实际114源码文件通过。旧三类QA全部逐值/重复次数保留，
  所有业务Parquet版本、配置/baseline/KILL/Live主库SHA保持。原19:40失败不改、不reset。
- Web990/日报988均用实际current venv只读新记录与表级证据，当前valuation_daily聚合green。
  这清除本次旧红灯现象，**不等于#561未来证据版本绑定/失效已修复**；旧red历史保留。
- catalog timer恢复active/enabled，下一自然10-29 19:40；独立只读采证timer
  `qsys-issue562-catalog-natural-20261029` active，19:50输出本审计根natural-output/report.json。
  原#540的17:20自然采证仍active且未改。尚无自然结果，人工/隔离成功不冒充自然成功。
- #562转monitoring，#540仍in-progress；共享checkout505d7e8、Tushare3facceb及八peer保持，
  模拟/实盘未恢复。下一步#561输入版本绑定与受控刷新，保留TTL、历史失败与全部门禁。
  详细验收及剩余规则见[catalog隔离计划](docs/plans/catalog-verify-production-isolation.md)
  和[#562正式验收](https://github.com/wanlinruo/quant-trade/issues/562#issuecomment-6476359685)。

## 10-29 维护者全权授权继续#540，#562修复与必要控制面准备

- 维护者最新明确“继续推进540，我全权授权给你”，无需重复询问已授权的数据恢复实施。
  顺序仍为数据→模拟→实盘，保持原KILL/交易门禁，不把代码发布当交易恢复。
- #562候选将字段blocking传播到步骤failed/任务failed/exit1，warning传播warning/exit0，
  字段与表级检查独立继续；实际ServiceRun文件正反组合回归通过。
- 必要controller v36只增加catalog-verify停机维护例外：Live loaded/inactive/dead/
  disabled/PID0，原绑定/KILL/状态事实保持；catalog timer inactive、oneshot终态PID0。
  保留精确failed/exit-code非零/Invocation，不reset-failed；保护八peer，其他组件不放宽。
- 代码/CI/test/PR和正式发布证据以#562最新记录为准；以下为当时准备快照，已由上文正式验收覆盖。
  当前生产catalog仍80f7068，保留10-28原失败。#561在本组件完成后继续处理，
  不通过修改历史证据或降低质量规则消除旧红灯。

## 10-29 00:24：八条历史现金修复已执行并验收

- 维护者已明确确认#558八项审阅包。00:22:21持真实写锁重核基线，00:22:54原子替换
  唯一事件表，00:23:21复验通过。61085行仅八个cash_per_share变化，其他值/键/顺序/
  Arrow schema+metadata及owner/group/mode/access ACL保持。生产SHA现df021519...，
  原eab786c3...以独立inode完整备份保留；此批准范围已执行，不再重复请求确认或重放。
- producer/backtest/Paper/Web/checks/digest六真实UID在替换前后均读取通过，替换后
  实际WarehouseQuery全表与候选精确相等。两因子表、KILL、Live主库、checkpoint、组件
  3facceb和共享checkout505d7e8及六peer/原timer完全保持。模拟/实盘未恢复。
- 真实维护PID2250665/UID0/invocation2dbe79ef...，exit0/success；一次性任务已停止。
  首次账号切换辅助失败在requested前退出，保留原记录，修复用新r2 run-id；没有回放。
- 结果见[生产验收](docs/reference/cash-dividend-production-acceptance.md)及#558最新记录。
  审计根/data/qsys/var-deployments/issue558-cash-approved-r2-20261029/，全部备份保留。
- #558仍跟踪未来增量现金选源，另八条null/0保持源值。#540继续等10-29自然业务；
  17:20只读采证timer保持active，脚本不绑定旧事件SHA，已核对无需修改。

## 10-29 零时：现金差异独立验真完成，历史覆盖未执行

- #558 承接 #540 的八条非空现金差异：正式实施公告每10股金额明确除以10后，与留存
  Tushare直取精确一致，QMT八条原值均不同；全部原PDF/元数据指纹已留存。不能用
  容差视为一致，也不推定QMT内部舍入原因。
- 本地候选61085行仅八个cash_per_share变化，其他值/键/顺序/Arrow schema保持，
  Parquet回读及实际现金消费函数隔离探针通过；未上传候选或改生产表。
- 另外八条null/0中，三份公告明确不派现，五份仅列转增；两源原值保持，不由未列现金补0。
  审阅包见[现金独立核验](docs/reference/cash-dividend-announcement-review.md)。
- 本次历史现金覆盖不在此前批准范围，须按AGENTS.md §4明确确认。未来增量现金策略
  未切换；初始窗口仍10-28，八条历史日期均在其前，不扩大checkpoint或重放旧请求。
- 23:54复核明日17:20只读timer仍active，尚无结果；本轮继续未启动业务或恢复模拟/实盘。

## 10-28 自动送配正式验收完成，20:30自然日报成功

- 维护者已明确确认并继续推进。PR#552/#554/#556依次正常合并、CI/test/自审通过；
  生产tushare-sync为`3faccebeb9736486f801ce1c91861b95083d7f4b`，controller v35。
  最终CI40682291567、dry-run40683731604、正式workflow40683959984、Deployment7384688652
  及实际节点record一致success，ZIP SHA与artifact12813541008 digest精确一致。
- 19:01:57首次自然运行因历史目录身份T600018.SH在PUT阶段被拒，原Run190159-2153226/
  invocation146edac390ab41fe9d399654432482fd与5条阻断保留。直取退市2006-10-20，
  修复只排除窗口外非原生历史身份并存scope，不去T映射、不删旧事件；真实完整5941清单
  PUT通过。本地最终4103 passed/7既有skipped/2既有依赖警告。#553控制依赖已验收关闭。
- 新版本**人工验收**Run`200201-2173949`（20:02:01–20:07:06）20步骤全部succeeded/
  exit0，invocation`7d3812657e1d4dcb829919b1097448ff`、实际业务PID2173949/UID983、
  parent2173945与实际unit绑定。User/Group/UMask/查询环境/CPU内存/Nice/IO/沙箱/精确
  写路径现场取证，MemoryPeak3221487616字节，未用辅助unit成功替代业务结果。
- 5941证券完整响应：5真实事件、5936 empty、failed/incomplete0；5条均已入库故新增0，
  事件表61085条SHA仍eab786c3...、全部旧字节保持。checkpoint结束2026-10-28，scope/
  bundle SHA核验通过。今日5570行价格因子与Tushare直取及规范表逐键精确一致，12只/
  336行修复历史样本保持；11张canonical写后对账通过。20:06最新556项质检0阻断，
  原19:02的5项失败记录仍在。不能把本次人工成功称为19点原自然任务成功。
- 正式Windows gateway及两组件key正/负向验证已通过，新增快照实际落E盘；旧分钟/竞价
  C盘根、活动QMT目录/任务/个人key保持。20:22 QMT3788与159个qsys任务文件SHA保持；
  C空闲4565495808字节、E空闲106036125696字节，#545仍需系统盘容量观察。
- 20:30:02–05自然digest-203002-2182508汇总/邮件均succeeded。20:00原service-checks
  为warning：旧Live无当日事件、修复前catalog阻断及存储保留警告，原记录不清、不重跑。
- 原归档/Tushare timer active/enabled。20:53复核Live inactive/PID0、Paper634053/
  NRestarts1、Web3700671、observer3933655、reader1466393保持，KILL未清、模拟/实盘未恢复。
  新增只读timer`qsys-issue540-next-natural-20261029`定于10-29 17:20，在节点观察下一次
  自然任务、scope/完整快照/质检/canonical；不启动业务、调用API或写Warehouse。
  此时仅已安排，结果未发生；输出位于下列审计根next-natural-output/report.json。
- #540继续in-progress：明日自然增量、8条非空现金/8条跨源空值差异、#550/#551和D-23
  约束仍分别保留。下一步先读#540/#555最新验收及节点只读报告，再按数据→模拟→实盘推进。
  Linux最终审计根`/data/qsys/var-deployments/issue555-dividend-scope-20261028/`，前次发布
  `/data/qsys/var-deployments/issue540-auto-release-20261028/`；Windows
  `E:/qsys-private-audit/issue540-auto-release-20261028/`。所有失败、requested和备份保留，
  已成功发布/业务不得重放；本条仅交接文档更新，不需要重复部署未变业务制品。

## 10-28 19点首次自然业务失败，#555前进修复准备（覆盖下文状态）

- PR#552/#554均已合并，main/test f09dcf7；controller v34和tushare-sync组件已正式安装。
  dry-run40675599024、正式40675756482、Deployment7383415419、实际节点record三方
  success，artifact digest与ZIP SHA一致，六peer及Live PID0保持。18:58:58恢复timer
  active/enabled，19:01:57实际自然触发；详见#540/#553最新正式验收。#553已关闭。
- 真实ServiceRun`190159-2153226`于19:03:16 failed，唯一invocation
  `146edac390ab41fe9d399654432482fd`。Tushare事实完成，送配PUT清单被拒，未请求QMT run、
  未写事件、未派生/canonical；原after_sync执行并报告5阻断。原failed/journal/目录保留，
  未重放、未回切或reset-failed。timer仍active，下一自然10-29 17:02:53。
- 根因已实测：5942行/130736字节清单唯一非原生标识T600018.SH，直取主表记录为
  上港集箱、上市2000-07-19/退市2006-10-20/as_of2026-10-28，历史事件0条。
  本地同版gateway也拒绝，Windows原作业目录为空且无run-requested，不是传输容量问题。
- #555为#540直接依赖：候选保留全部原生代码/历史事件，仅按直取退市日期排除窗口外
  非原生目录身份并存scope/SHA，未知或窗口内非原生身份显式报缺；不去T映射当前600018。
  controller v35候选限定PID0/非零exit-code/唯一invocation的failed终态维护，前后绑定
  原终态/KILL/主库/六peer；不清失败再安装。专项通过，全量/CI/test/PR与正式发布待记录。
- 当前事件仍61085条/eab786c3...，Windows正式gateway/脚本与E盘根已实际切换、真实两key
  正/负向验证通过。模拟/实盘未恢复；原自然失败与后续新版本新run验收须分开记录。
  工作详情优先读#540/#555最新评论，不把安装success描述为自动送配业务已恢复。

## 10-28 18点维护者已确认发布，控制面直接依赖准备

- 维护者明确“确认”PR#552合并与正式发布、必要gateway文件、E盘送配根和精确事件表
  写权限启用。PR已合并d56c9ac，自动对齐/审计及最终SHA CI40671319925成功。
- 发布预检发现旧controller仅reader允许Live停止时安装，tushare-sync仍要求Live active。
  #553作为#540直接依赖准备限定数据组件的停机守卫，不启动Live过门禁：Live严格停止/
  disabled、原KILL/主库/绑定及六peer保持，Tushare timer/oneshot均inactive方可安装。
  同时补齐该组件参数迁移的drop-in精确失败补偿，避免旧代码配新参数。
- 此修复仍须正常CI/test/PR再准备精确main控制器。Windows从GitHub读取d56源字节并
  创建版本化控制目录与E盘空根，准备快照时未切正式key、组件或生产事件写入；后续现场
  以#540/#553最新评论为准。原Tushare timer保持active，19点先到则由原版本自然执行。
- Live inactive/PID0、Paper634053/NRestarts1、Web3700671保持；模拟/实盘不恢复。

## 10-28 自动送配候选与17点自然验收（覆盖下文对应待办）

- 维护者明确继续推进#540，并确认Windows数据盘此前已扩容。实测E: NTFS约100GiB、
  空闲106077167616字节；C:空闲4586393600字节，QMT PID3788仍从C运行，活动userdata
  为普通目录。E盘此前已有私有备份。候选将**新**送配快照写独立E盘目录，旧分钟/竞价、
  活动QMT目录、恢复配置及备份不迁移、不清理。#545仍开放，系统盘余量需单独观察。
- 自动送配候选包含完整逐证券快照/固定窗口与SHA收据、两个bundle文件传输、仅追加与
  旧事实冲突整批拒绝、事件成功后才派生及发布因子。首次起点显式2026-10-28，按成功
  导入窗口回看7天，超过31天断流拒绝；不以最后事件日期判定供应完整。仅tushare-sync
  可用hash批准的dividend操作，独立E盘根与旧两个根不重叠，未知送达不重放。
- Windows隔离首轮发现只读句柄fsync报errno9，失败/请求/临时文件保留；修复为可写句柄
  并在R2新目录验收。3只证券返回2真实事件/1空响应，完整快照、重复run和跨组件拒绝通过；
  实际SSH probe/list/get及客户端长度/hash/schema/覆盖/隔离导入通过。QMT3788和159个
  qsys任务文件SHA保持，下载/账户/交易调用0。正式组件key/生产权限/控制面仍未切换。
- 最终本地4067通过/7既有跳过/2既有警告。冻结的Live/Paper代码保存在远端
  `fix/frozen-ops-recovery`（f87babd）；数据候选将对应5文件恢复至main，PR#547关闭未合并。
  CI/测试部署/新数据发布PR继续由#540记录，生产发布仍须精确审批；不得直接发布宽泛
  Windows控制面制品或重启业务任务。详情见[自动送配清单](docs/plans/dividend-data-recovery.md)。
- 原17:00自然同步`170005-2111515`于17:03:22 succeeded，19步骤成功、11表canonical
  对账通过。实际采集UID只读核对今日5570行因子与Tushare直取及规范表精确一致，12只/
  336行修复历史样本保持、事件仍61085条；不是新自动送配的自然验收。17:10原替代分钟
  源探针仅1次调用被40203频次限制拒绝，未重试，时间窗口未知；不算权限拒绝或供数通过。
- 17:14 Live inactive/PID0、Paper634053/NRestarts1、Web3700671保持；KILL未操作。
  19:00同步、晚间质检/日报仍待自然采证。模拟/实盘保持暂停恢复，#540唯一in-progress。
  Linux证据根`/data/qsys/var-deployments/issue540-dividend-auto-20261028/`；Windows实际
  隔离根`E:/qsys-private-audit/issue540-dividend-protocol-20261028-r2/`，首轮在无-r2目录。

## 10-28 16:14 送配历史修复已执行（覆盖下文准备/待确认状态）

- 维护者本会话明确确认#540审阅包6463418320后，等待15:41自然归档终态，再持真实
  warehouse-write.lock，于16:03:44–45完成三表修复；没有发布新组件或恢复模拟/实盘。
  corporate_actions新增661条、合61085条，旧60424条逐字段保持；adj_factors全18368804行
  与规范因子2026全1074139行均仅164个量因子值改变，其余键/列/物理顺序保持。
- 16:07生产全量逐行验证通过，真实backtest/Paper/Web/checks/digest五UID只读沙箱通过，
  全部读取61085事件、661新增及12只/336因子样本，旧/规范对账一致。SSH回执中断后只读
  核对五份节点报告及readers-verified.json，没有重跑消费者验证或写操作。
- 事件候选仅symbol的Arrow large_string与生产string不同：绑定原批准SHA后无损重编码，
  61085行逐值不变，schema匹配。最终生产事件SHA为
  `eab786c3a2de0a300c832a3b53749bcd33f6d7356f9b1ab6b47b28dce0b4dee5`；两因子SHA
  仍为审阅包2773ca...及177557...。原批准文件、转换对应、完整备份和全部失败证据保留。
  三表owner/group/mode/access ACL逐项保持；只收尾本次辅助timer/service，未reset-failed。
- 今日自然归档daily-archive-154113-2086038于16:03:22结束：5570股日/1342297根分钟，
  原质量0阻断/0已知差异，canonical逐键逐字段对账通过，竞价步骤成功。**整体warning**：
  Tushare停牌响应7行少于已有8行，原保护保留8行；差异仅301139.SZ当天S/null记录。
  当前直取退市日期为今天，该股已从今日分钟清单排除；不推断源删除原因、不删原快照。
  [#551](https://github.com/wanlinruo/quant-trade/issues/551)记录口径待核验，排期blocked。
- 16:14旧Live inactive/disabled/PID0、KILL保持；Paper634053/NRestarts1、Web3700671、
  observer3933655、reader1466393保持。7日14个旧分钟分区SHA保持。归档/Tushare timer
  active/enabled；下次Tushare17:00:02，归档10-29 15:41:08。16:12 Windows QMT仍3788，
  四业务任务SHA保持，C盘4579663872字节；没有重启或清理。
- **数据阶段仍未完**：本次仅恢复历史事件/量因子，未来自动送配入口/最小写权限/固定gateway
  协议未接入；候选导出/导入代码虽4061通过、7ebe332的CI成功，未生产发布。8条非空现金
  差异/8条跨源空值差异保留；今晚17/19主同步、17:10已排只读探针和质检/日报仍待自然验收。
  #540唯一in-progress，其他开放Issue blocked，PR#547不合并；后续发布先隔离冻结变更。
- 详细[修复记录](docs/plans/dividend-data-recovery.md)，节点审计根
  `/data/qsys/var-deployments/issue540-data-recovery-20261028/dividend-review/`：apply-result.json、
  applied-full-verification.json、readers-verified.json、final-boundaries.json。存在requested
  的写操作不得重放。代码/备份/历史失败与KILL均保留，不把数据修复说成交易恢复。

## 10-28 送配恢复准备（#540，生产事件表尚未写入）

- 5942只证券的20260910–20261028原生送配只读盘点完成：661条真实事件、5281只空表，
  None/失败0，下载/订阅/账户/交易调用0。旧事件表60424行止9-9，新增键重叠0，合并应61085行。
- 12条送转事件影响现有12只/164行量因子，价格因子不变。14只/17247行真实完整历史的
  实际构建器精确复现旧样本，导入661条后验证旧事件及价格因子保持；规范因子同步验收。
- Tushare29个除权日独立核对：661条送转比例全部精确一致；现金645条精确一致、8条小数
  差异、8条Tushare null/QMT原生0，原值保留，不设容差或填值，不宣称全部现金验真通过。
- 导出保留null、按固定窗口/SHA收据续传、数据先持久化再记完成；导入拒绝不完整事件的
  修复候选全量4061通过/7既有跳过。尚未发布，不把候选修复称为生产自动送配已恢复。
- 三张生产目标完整备份及SHA保持；全量候选文件与审阅包见
  [送配恢复计划](docs/plans/dividend-data-recovery.md)。此前批准不覆盖送配/两张量因子表，
  必须按AGENTS§4取得本次精确清单的明确确认后才覆盖。17:00同步改变基线后重核范围。
- #540仍唯一in-progress，其他事项blocked，PR#547不合并；今天15:41归档及后续自然
  Tushare/质检/日报待实测，不重复安排既有17:10探针。

## 10-28 14:55 数据恢复主线（覆盖下文冲突的旧快照）

- 维护者最新顺序为 **数据 → 模拟 → 实盘**。#540是唯一in-progress；其他开放Issue均
  blocked，PR#547冻结。Paper存活不等于账户恢复，不推进旧Live/内置交易或清KILL。
- 经维护者对#540审阅包明确“确认”，已完成10-26/27原正式QMT通道回补。分钟各5570只，
  分别1342305/1342229行，合11140股日/2684534行；raw与canonical对账通过，覆盖、
  null、重复及原质量门禁阻断均0。竞价72/44只新增8487/5334条，原275270条逐字段保持。
  原年度文件、错误raw及失败记录保留；未改变源值、单位、阈值或D-23裁决。
- 301139.SZ两日原RPC返回有量零价，被原质检拦截；正式get_local_data(fill_data=False)
  返回真实482根，已按精确输入SHA及原分区备份受控替换，其他证券保持。长期采集器缺陷
  由#550承接且blocked；本次维护没有修改生产下载器、gateway或部署新组件。
- 归档timer恢复active/enabled；14:33:44的Persistent补跑14:42:24成功，真实记录
  `daily-archive-143346-2047017`。7日38982股日无缺分钟，竞价无缺，质量0阻断，
  canonical发布9393887行。补跑前后14个raw/canonical分区SHA完全保持。下一自然触发
  15:41:18；今日收盘后的归档/Tushare/质检/日报尚未验收，不用补跑冒充自然增量成功。
- Web/checks/digest三个真实UID和只读沙箱读取通过。14:55旧Live仍inactive/PID0，
  KILL SHA保持；Paper634053/NRestarts1、Web3700671、observer3933655、reader1466393
  均保持。Windows QMT3788/08:53:53启动及四业务任务SHA保持，C盘4872556544字节。
- **数据阶段未完成**：corporate_actions仍止9-9，正式接口已证实001399.SZ/600017.SH
  有10-27送配事件；缺口早于MiniQMT升级，不能归咎本轮升级。下一步核对送配原调度、
  无填充字段映射及精确事件清单，独立准备事件/量因子恢复；不扩大本次分钟/竞价写授权。
  Tushare价格factor与依赖送配的cum_share_factor分别验收。冻结表不算新断流。
- 完整47表分类与来源/消费者矩阵及每次执行证据在
  [#540](https://github.com/wanlinruo/quant-trade/issues/540)。节点审计根为
  `/data/qsys/var-deployments/issue540-data-recovery-20261028/`，关键报告：
  `minute-verification.json`、`consumers-verified.json`、`timer-restored-final-readback.json`。
  不重放存在requested的脚本，不删备份、不清失败历史。

## 10-28 跨日排障与修复准备（覆盖下文对应快照）

- 维护者本轮再次全权授权继续推进并休息。00:37旧Live仍inactive/PID0，reader1466393、
  Paper634053/NRestarts1、Web3700671/NRestarts0保持；Tushare timer active，归档timer
  inactive。未清KILL、恢复旧Live、修改生产业务状态或发送测试通知。
- **Paper进程存活不等于业务恢复**：真实qsys-paper UID只读查得2个error/1个ended账户，
  没有running；error更新于10-26 09:18且错误包含行情。#549已登记，原assess_paper只查
  running而漏掉error，导致健康判断正常。已准备只读判定修复：已生效error账户明确DOWN，
  不改账户或自动重跑；专项50项通过。#540供数恢复与#539 SQLite故障仍分别承接。
- #535已准备连续故障熔断修复：本地KILL落下后锁定本进程，撤单只尝试一次；Redis超时
  记录未知结果并继续critical告警，不退出后自动拉起或自动恢复tick。真实KillSwitch/
  LiveBroker隔离测试覆盖未送达与已送达但回执超时，专项17项通过。没有启动旧Live或撤单。
- 两项合并本地全量4045通过/7跳过，2条既有依赖警告。代码尚未生产发布；CI、测试部署及
  PR最终证据继续由对应Issue与PR#547记录，不能把准备/离线通过说成现场已生效。
- #539当前权限链与主库/WAL/SHM的named ACL正常，真实UID只读可查；原失败栈仍在
  PRAGMA journal_mode=WAL。当前655–656个FD中652个指向Workbench，软上限1024。
  用实际生产PaperStore、真实UID、1024上限及正常GC分别跑空账户/相近容器结构的合成
  三账户3000轮，均未复现，峰值144/142，GC后4；故障根因仍未确定，不猜测改权限或归因。
- #545当前C盘约4.04GB；现有E盘NTFS约100GiB且已有此前64队列备份，本轮没有附盘或
  格式化。“唯一C盘”是旧快照。原审计根未压缩大队列候选0。按既定可恢复流程精确预检
  16个旧代理静态队列，源文件保持，仅E盘私有备份校验后NTFS压缩；先2个通过后再完成14个，C盘已升至约5.23GB；16份E备份和原文件全量复核后由#545
  记录最终结果，不重放有requested标记的步骤。
  原agent仍disabled、旧代理进程0，新QMT仍PID5932/10-27 20:54启动；新版活动目录不动。

## 10-27 21:03 实际Python3.12与跨库GET通过（覆盖20:53快照）

- 20:55只读复查发现客户端已自行换为PID5932，启动20:54:13.3114919；本轮没有发出
  第二次重启命令。既有watchdog20:54运行结果0、四业务任务XML保持；对应时段Windows
  Application1000/1001无匹配崩溃事件，原进程退出原因未知，#543不能据此关闭。
- 新进程恢复登录后重新绑定只读身份/桌面/前台守卫，在新建诊断模型中仅运行一次：
  实际Python3.12.10/pandas3.0.2/NumPy2.4.4，8条协议；本地600000.SH两根四字段返回
  普通DataFrame，000001.SZ空表未补值。下载/KV/账户/交易均0，未验证Panel接口/数值等价。
  [证据](https://github.com/wanlinruo/quant-trade/issues/546#issuecomment-6442527901)。该模型已停止关闭。
- 同一新进程打开原QSYS533_READONLY_20261024模型，源码逐字核对后21:01只执行既有key
  GET一次：返回码0、消息空、19字节合成值精确匹配，save/account/trading均0；该模型随后
  停止关闭。这进一步证明本原模型单key跨此次运行库切换保存，不代表账本/未决委托恢复。
- #544晚间自然验收已完成并关闭；reader/Paper/Web维持原PID与NRestarts，旧Live仍停止、
  KILL保持、归档timer仍inactive/enabled、Tushare timer active/enabled。#540数据缺口保持。
- 最新C盘约4.03GB，刚超过既定4GB门禁，#545空间风险仍在；未删除旧库/备份、未执行
  第二批压缩、未发出额外重启。后续优先做#538同输入精确算法对照、三个Panel接口容器
  专项，再推进#541账本恢复和#542原生事实。仍不激活真实交易。

## 10-27 20:52 晚间自然验收与新模型失败（覆盖下文对应快照）

- #544已关闭：20:00自然checks成功读取Live状态，仍如实报告当日无策略事件；20:30自然
  digest汇总/邮件均success、exit0，定点日志读取失败事件均0。Web当前live-core观测行
  HTTP200、reason=strategy_event_failed，与observer一致。reader仍PID1466393/NRestarts0。
  [最终证据](https://github.com/wanlinruo/quant-trade/issues/544#issuecomment-6442312508)。
  归档缺竞价44只/分钟5570只、原failed记录、#482维护语义与#540缺口分别保留。
- 维护者20:26回复桌面就绪后，已停止并关闭旧诊断模型。20:34在UI选择py3.12、将自动
  追加的嵌套路径纠正为已验证的python312根并确认设置；未出现重启提示，PID1596保持。
- 按指南新增QSYS546_RUNTIME312_20261027，逐字核对无交易探针源码，20:50编译成功；
  20:51仅运行一次被客户端拒绝：“Python版本3.6不匹配Python库路径…python312”。
  无协议输出，不把py312安装/编译成功当作实际运行成功。该模型已停止/关闭；未重跑，
  未进行第二次重启。下一步核对设置持久化与加载时点，不能直接改枚举或覆盖DLL。
- 原四业务任务XML保持，旧agent仍disabled；旧Live/KILL及真实交易门禁保持。20:50
  C盘3997032448字节，再次低于既定4GB重启门禁，不因卡点降低阈值或删除回退副本。


20:53再次只读打开设置，界面仍显示py3.12及精确python312根路径，随后取消退出。
当前进程运行却报3.6，设置展示与进程实际生效时点分开；后续重启须先重新满足容量与
进程/任务守卫，不直接修改枚举或绕过版本校验。

## 10-27 20:24 晚间复核与运行库分层结果（覆盖下文对应快照）

- #546新库安装已完整通过14497个文件SHA、Windows ZIP与Linux正式包一致性、旧库8个
  关键文件保持检查。容量首次拒绝后，只对4个未加载的候选DLL做普通NTFS压缩，文件内容、
  mtime、ACL、身份/单硬链接保持，物理分配实减42343936字节；第二批仅预检，未apply。
  点击前C盘4018573312字节通过既定门禁，15:11仅确认一次客户端下载完成后的原生重启。
- 新客户端自行恢复为PID1596/15:11:47.6215254/session1；20:22仍同PID响应，C盘4008337408
  字节、旧agent仍禁用。路径已指向python312，但**实际3.12运行尚未验收**：复用切换前
  诊断模型的一次运行未取得协议输出，未重复运行。配置枚举0不单独用于推断版本。
  下一步核对实际设置，并按指南新增无交易模型验证；不假设旧模型已自动迁移。
- Windows会话现已断开；20:19停止诊断及20:21设置查看均在输入前被WTS守卫拒绝，
  无input marker、无重启/写配置。不能将被拒绝的停止点击说成模型已停；已知源码只有
  有界只读历史查询，无账户/交易调用。待RDP保持连接并QMT置前后核验，不绕过守卫。
- #544永久reader PID1466393/NRestarts0仍active；20:00自然service-checks完成30步
  （29检查加交易日判定）。数据失败2项为归档缺竞价44只/分钟5570只，策略事件失败为
  旧Live暂停下“当日没有任何策略事件”，不是状态库读取不可用。运行结果failed保留，
  警告含catalog B4.arrival及存储到期；不清failed记录或为通过验收重跑/通知。
- 日线同步19:00自然终态success；20:30日报自然结果仍待采证。#540归档依赖未恢复、
  #541完整账本恢复和#533真实交易门禁保持；#546安装成功不替代实际运行/数据验收。

## 10-27 14:55 原生运行库核验在途（覆盖下文对应快照）

- 维护者本轮明确全权继续推进并准备休息。可恢复的独立运行库安装/设置和一次客户端
  原生重启按#546具体预检推进；旧Live停止/KILL与真实交易的独立门禁保持。
- #541原模型单key跨客户端GET已通过：当前PID5616读回旧进程保存的19字节合成值，
  GET1/save0/account0/trading0、精确匹配。#546已采得当前py3.6.8/pandas0.22.0/NumPy1.19.1
  基线，600000.SH返回2×4 DataFrame、000001.SZ空表未填补。完整业务恢复仍未通过。
- #546正式包在Linux完整通过MD5/SHA、ZIP CRC与路径核验。14:31只点击一次客户端正式
  下载；14:54 Windows已下完整204236998字节包，界面仍“解压中”。**不要再次点击下载、
  将目录存在当作安装通过或直接重启。** 当前进展以#546最新评论为准。
  独立目录`C:/self/qsys-qmt-2.1.33.0/bin.x64/python312`，py36原目录/库/压缩包保留。
- 保存/重启前须完成全部14497个展开文件与正式包摘要核验、旧库指纹保持、客户端与任务
  守卫，且C盘仍至少4000000000字节。当前尚未保存py312设置或重启；原PID5616仍响应。
  节点完整文件核验脚本已准备，详见[执行记录](docs/plans/qmt-python312-runtime-verification.md)。
- #542原生目录元数据新增“沪深京A股”等真实板块线索；过期合约SH16662/SZ15925/BJ0，
  抽样详情所查分类/日期字段均null，未生成历史池或据空集判断覆盖。11次只读查询，无
  下载/KV/账户/交易；该轮诊断已停止/关闭，模型基线源码先备份，后续须重新逐字核对再运行。
- #548记录私有配置转换异常意外回显输入的问题；节点侧改为白名单字段提取、异常仅类型，
  合成失败路径证明原文不输出。长期约束已写AGENTS.md，不在项目记录转载配置或敏感字段。
- #544永久reader仍active/enabled，旧Live inactive/disabled；归档timer继续inactive/enabled，
  Tushare timer保持。晚间自然任务观察待做，不把读库恢复描述为停机期间策略执行成功。

## 10-27 13:10 正式读库与QMT补充资料（覆盖下文对应快照）

- #544已按明确授权完成PR#534合并、控制面升级、正式发布及`live-state-reader`激活。
  main/test当时对齐`711fe0b0ddb2970675e72ccde69fa59ba33ad7ea`；正式workflow
  `40447170350`、Deployment`7349968407`和节点record一致成功。永久服务active/enabled、
  RuntimeMax=infinity，旧临时anchor已退出，原23:51期限已由永久组件接替。
- 实际Web/digest/checks/observer身份及只读沙箱三轮共12次读取通过，主库/KILL指纹保持。
  读回70条历史事件不代表停机期间事件已执行；#544保持monitoring，晚间自然任务仍待观察。
  旧Live仍停止/disabled、本地KILL保留，Paper/Web等原进程与原五项failed记录保持。
  详见[#544正式验收](https://github.com/wanlinruo/quant-trade/issues/544#issuecomment-6433684495)。
- 维护者提供两份QMT补充PDF，评估见
  [资料记录](docs/reference/qmt-python312-supplement-review.md)，#546跟踪运行库核验。
  实测运行时仍为py3.6.8，未保存切换设置、下载py312或重启客户端。第一份为第三方线索；
  第二份明确三个接口的Panel→MultiIndex DataFrame变化，不推广到get_market_data_ex。
- Windows 12:43只读观察仍为10:17启动的PID5616、C盘4931551232字节；不视作长期稳定。
  #541跨客户端GET尚未执行。13:03公告窗口无法成为前台，输入守卫拒绝，无点击或KV调用，
  新辅助任务disabled；待维护者将QMT置于前台并关闭公告再继续原诊断模型，只做既有key的GET。

## 10-27 11:15 停机态读库准备（覆盖下文对应快照）

- #544 新增独立 `live-state-reader` 准备件及仅限该组件的停机安装守卫，
  [方案与审批后步骤](docs/plans/live-state-reader-maintenance.md)。尚未生产安装/激活，
  原临时服务仍约今晚23:51:46到期；准备代码与隔离通过不解除期限。
- 真实生产者/两个只读消费者 UID、合成库和匹配 systemd 沙箱已验证冷库失败、持连接可读、
  新提交可见、SQL及主库/KILL/锁文件写入拒绝、文件替换退出。生产主库/KILL的SHA、mtime、
  权限/归属保持，原五项 failed units保留；失败探针与最终证据分别留存，不是生产故障演练。
- 11:09 Windows仍PID5616/10:17:16，C盘4920266752字节；旧agent任务disabled、
  LastRun仍09:14。只证明该观察区间未重拉起，客户端原生KV/必要输入和长期稳定仍未验收。
- 旧Live停止、本地KILL、归档临时暂停与日线timer保持；未生产发布、未清KILL或激活交易。
  控制面/CI workflow升级的证据bootstrap、正式部署及服务激活须另行完成，不能直接重启旧Live。

## 10-27 00:45 恢复排障交接（覆盖下文对应快照）

- 维护者已明确“按照建议继续推进，完全授权”。当前顺序为保住日线底座、排除独立故障、
  完成最短实盘闭环；全市场分钟/竞价归档恢复后置。旧 Live、KILL、资金与真实交易门禁保留。
- **归档临时暂停**：10-26 23:49 仅 stop `qsys-daily-archive.timer`，enabled 保留，
  Tushare timer 仍 active/enabled。节点重启会恢复归档 timer；未修上游前不能无条件恢复长重试。
- **状态读取临时缓解有到期时间**：`qsys-issue544-state-reader-anchor.service` 使用只读 URI
  维持 SQLite sidecar；真实 checks/observer UID 及只读沙箱读取通过，主库 SHA/mtime/权限不变。
  旧 Live 未启动。辅助服务最长 86400 秒，约 **10-27 23:51 到期**，重启后不自动恢复。
  #544 保持开放，到期前必须复核，不能把它当永久修复。恢复读取也不代表停机期间策略已执行。
- **发现C盘仅余约8MB，容量缓解后新版再次恢复界面，稳定性待观察**：#545在不删除文件的
  前提下，仅NTFS压缩79个静态审计备份，逐文件SHA/大小/mtime/ACL不变、无外部硬链接；
  实际余量升至5548810240字节（约5.17GiB）。不把磁盘风险直接认定为全部崩溃根因。
- #543此前重启后午夜复发。确认容量完成且余量至少4GB后，00:39进行另一次有界重启验证，
  新PID5540/session1/00:39:00.8500849，00:40确认登录后模型研究。未改DLL、配置或自动恢复。
  原四业务task XML不变，原三个UI辅助task disabled。新增只读观察任务
  `qsys-issue543-capacity-observe-20261027`，每分钟取PID/崩溃事件/余量，最多20分钟、25分钟
  任务上限，结束自禁用，不重启或调用交易；其结果须另读，不提前称长期稳定。
- WTS确认session1为Disconnected；23:57编辑器点击在输入前被守卫拒绝，新进程均未执行
  KV/账户/交易调用。待远程桌面重新连接后再操作，不改变RDP/登录配置来绕过前台守卫。
  **跨客户端 KV 恢复仍未验收**，不能把重启成功和旧进程的读回合并为通过。
- 当前明细及精确审计路径见 [恢复排障记录](docs/plans/qmt-recovery-operations-20261026.md)。
  GitHub 运维评论已在网络恢复、精确去重后补记。重试前仍须核对送达状态，禁止盲目重复POST。

> 快照时间：2026-10-22 20:12（Asia/Shanghai）。这是接手入口，不替代 GitHub、节点和机器可读
> 契约的实时状态。执行任何写操作前必须重新核验。

## 10-25 QMT迁移与旧Live暂停（覆盖下文对应历史状态）

- **实盘当前暂停，新版尚未接管**。#533是迁移主线，#535承接旧Live异常退出与启动重试。
  10-24 23:11三次Redis读取超时后，自动急停已写入本地，cancel_all投递再次超时导致退出；
  是否送达/产生券商撤单尚未核清。后续启动前门禁因账户快照和agent心跳过期拒绝运行。
- 维护者明确授权“仅停止旧Live服务”后，10-24 23:36:58只执行一次systemctl stop。
  qsys-live-core.service验后inactive/dead/MainPID0；停前241次重试已留证，unit哈希及原有
  UnitFileState=disabled不变。**不得自行重启旧Live、清除自动KILL或启用新版实盘**。
  停止授权不包含这些操作。Paper在10-25 20:02因SQLite打开失败退出后自动恢复为PID634053/NRestarts1，
  已登记#539，进程恢复不代表模拟事件连续性已验收；Web3700671保持active/NRestarts0。
- 新版2.1.33.0独立安装在`C:/self/qsys-qmt-2.1.33.0`，10-25午间XtItClient PID6004已登录；
  官方py36库安装后实际Python3.6.8、pandas0.22.0、NumPy1.19.1。旧Mini已不在运行，
  原四个Windows任务定义及旧目录保留，原QMT自动恢复仍指向旧目录，不能称其已适配新版。
- #533公开样本600000.SH在沙箱内可读取101根日线；GUI分钟下载显示完成后最初仅11根，
  正式内置download_history_data明确范围补充后取得10-23全天241根。成交量汇总与日线一致；
  成交额原始值差-16由#536核验，未改值或放宽门禁。两个价格指标在沙箱内计算成功，
  仅验证不复权样本上的既有算术，不代表生产复权/PIT、全市场候选、状态恢复或交易已通过。
- 补充正式过期合约列表元数据后，两种ST接口均取得000004.SZ历史ST/*ST区间；
  只证明接口及该样本可用，不证明全市场覆盖。过期合约列表SH16662/SZ15925、BJ空，
  不能当作历史全A股票池或认定北交所没有过期股票。历史限价、股本PIT与状态恢复仍待验证。
- 10-25午间补充：原生财务准备后，股本公告期43条/变动期47条，单样本公告日前后
  查询通过按日过滤；同公告日多事件、公告早于生效及盘中可见性仍需处理。当前沪深A股
  默认查询5225个代码、带历史时间为空，上证50正对照50个，不代表历史全A覆盖。
- #537承接调度：repeat_times=0/1/2/3实测回调1/1/2/3次；ContextInfo自定义标记
  停止模型再运行即不存在，不能充当持久化。未为验证退出客户端/重启Windows。
- #538承接复权适配：600000.SH截短到除权日前，重叠35根前复权价格仍含后续因子；
  301379.SZ送转样本39根，原生等比前/后复权量均等于不复权量。不能直接替代现有
  as_of锚点与独立股本量算法。正式单样本tick补充后，600000.SH有177条早期竞价、
  09:25:10前一条09:25:02记录及3条连续交易对照；只证明历史样本可读，不代表实时时效、
  全市场覆盖或完整单位验收。全部探针已停止，未激活策略交易。
- #538已增加前复权纯算术及原生内存映射，并在实际py36连接600000.SH/301379.SZ
  两组缓存：完整/末根收盘前/除权日收盘前与收盘时分别101/100/35/36根、39/38/22/23根，
  均可进入History，送转前22根量调整、成交额保留。原生日线为UTC+8午夜毫秒标签，
  映射用15:00收盘规则，不能以午夜视作可见。空事件/缺增量不补1；完整单位/事件覆盖
  与全市场仍未验收。现金样本对内置前复权有最高约1.78e-15差异，未抹平。
- 本轮新增多事件测试发现并修复准备核心的Polars单组标量/多组向量末位差异：
  reference_symbol_count必须显式提供实际可见证券组数，未知不默认1。只影响准备材料，
  未进入生产交易。全量/现场/CI与剩余门禁见迁移计划§0.2–0.4及#538，不据此启用实盘。
- 10-25晚间#538追加600036.SH配股日线：一次正式准备后19根，四个as_of场景
  19/18/9/10根、56行六列与实际HistoricalFeed及py36精确一致；成交额原样保留。
  但股票get_trading_dates也是19日，上证指数正对照25日，少了配股停牌期间6日；不能将
  股票列表当独立市场日历证明覆盖。停牌完整性仍未通过，不能补bar或放宽当前覆盖门禁。
- 历史限价stoppricedata在内置查询返回普通日线字段，正式单次下载返回False；当前合约
  限价不能补历史，能力仍未打通。没有据此判断所有正式接口均不支持。详见#533及计划§0.4。
- 10-25 21:51外部供数盘点由#540承接：现有Tushare日线/QMT分钟和竞价数据上界到10-23，
  只查footer/旧运行证据，未重做质量验收。corporate_actions用于累计股本量因子，价格因子
  可直取Tushare不代表全部派生独立。QMT补采失败后仍执行既有质检的代码修复尚未发布生产；
  不把整项任务改报成功、不恢复旧通道，详见docs/plans/qmt-data-continuity-migration.md。
- 10-25 21:35新版进程变为5916，启动20:54:12，不推断原因；弃用旧句柄。维护者解锁后，
  22:09实际调用get_trading_calendar三市场均NameError，ContextInfo同名方法不可调用，
  股票/指数仍19/25日；未取得独立日历或停牌完整性，不以较早“等待桌面”记录作为最新结论。
- #541承接状态恢复：原生只读ACCOUNT返回1条并与私有配置身份匹配，POSITION/ORDER/DEAL
  均空，历史交易明细函数三类查询均NameError；7次尝试/4次返回，下载及交易0。
  空查询不证明券商空仓/无委托或缓存完整；历史/持久化/lot与去重仍未通过，#535撤单结果不变。
  22:48检查点确认账号探针停止、原任务XML不变、临时辅助任务disabled。后续函数名枚举被
  前台守卫拒绝，未运行；详见docs/plans/qmt-inner-state-recovery.md，私有账号不入Git/Issue。
- #537新增独立决策回调守卫准备：按session/run_id及证据有效期拒绝未恢复状态，显式
  校验决策/行情时点；同实例非阻塞拒绝并发/重入，回调前消费检查点，异常或中断后整个实例暂停，其他检查点也不续跑。
  未接原生调度或生产入口，未提供实际持久化/共享锁/证据生产者，生产延迟阈值未设默认，
  不能把离线测试或局部内存去重当跨重启验收，详见docs/plans/qmt-inner-state-recovery.md§6。
- Linux停机审计在`/data/qsys/var-deployments/issue533-20261024-qmt-native-readonly/`；
  Windows源码、原始日志及缓存备份在`C:/qsys/var-deployments/issue533-20261024-qmt-install/`。
  账号、配置、截图和缓存不入Git；恢复/接管前须核对券商真实持仓、活动委托与pending，
  保证单一交易所有者。完整当前门禁及最新现场结论先读#533/#535/#536和
  `docs/plans/qmt-inner-strategy-migration.md`，不复用下文active快照作为现状。

## 先读什么

按以下顺序建立上下文：

1. GitHub 未关闭 Issue 列表，重点看各 Issue 的最新评论；`status:*` 标签就是当前阶段；
2. #137（实盘上线清单）、#330（独立制品与发布总跟踪）、#127（数据治理总跟踪）；
3. `docs/deploy/production-linux-release.md`：生产 Linux 发布规则，以及 10-19 归档修复的发布顺序；
4. `docs/reference/deployment-contract.md`、`deploy/deployment.toml`、`deploy/component-releases.json`：
   机器可读的部署边界与独立组件清单；
5. `.github/workflows/deploy-production-linux.yml`、`deploy-production-component.yml`：实际的 GitHub 门禁；
6. `deploy/linux/qsys-production-deploy-entrypoint.py`：节点最终执行与代码回滚逻辑；
7. `docs/deploy/linux-core.md`、`docs/deploy/windows-node.md`：节点实况和约束。

发生冲突时，优先级是：维护者最新明确指示 → GitHub Issue 最新记录 → 当前 workflow/
节点入口和 manifest → 本快照 → 其他历史文档。不得用旧文档覆盖维护者的新要求。

## 10-22 20:12最新交接（覆盖下文历史快照）

> 20:38补充（#122/#508/#515）：维护者明确确认应急入口及SSH维护窗口后，Windows
> 已新增`PerSourceMaxStartups 3`；全局10:30:100、LoginGraceTime120、密码/公钥yes均保留。
> 节点恢复保护先行；首次Get-ScheduledTask既有WMI错误拦在写配置前，失败证据保留，改用
> 任务文件核验后切换成功。Mac/Linux连接、Redis PONG、原Windows5业务进程/4任务/keys与
> ACL/boot保持、Live3385058/Web3448094/worker1300978保持通过；临时apply/rollback任务已
> disabled，原配置备份保留。Windows审计`C:/qsys/var-deployments/issue508-20261022-ssh-preparation`。
> 后续混合probe/list发现Windows复用通道重复首次响应，**两次相同probe不能证明协议正确**。
> #515改为每次操作独立连接并拒绝响应操作错配；正式发布/真实混合文件取回和自然验收以
> #515/#508/#475最新评论为准，不用下文“复用probe通过”证明混合协议已通过。

- main/test已对齐`b760b5f91eb0ff00e8737ed90e627c2a1f4557da`（PR#513）；共享checkout仍
  `505d7e885e7d98154c1d83cc150eaec59b4f6f7c`，入口v31。15/15独立部署数量不变，#330未关闭。
- **#485/#506/#492/#469/#512本轮已关闭**。归档15:40自然6步骤完成，18:15审计通过，
  canonical当时1341933行逐键逐字段对账通过；晚到新股缺口另由#475/#512承接。
  Live四张journal检查已提前至停旧/marker之前；hosted39765323139真实权限拒绝与连续性通过，
  共享正式39765540832/Deployment7250125728/实际validator三方success。
- Web current `b760b5f`，正式39775105208/Deployment7251627559/实际validator三方success。
  新PID3448094，UID990/GID982，仅私有组及observers；runs的47目录/769文件全可读不可写。
  Workbench access/default ACL已补，owner/group、worker/Paper合法写者与原事实保留；
  隔离真实SQLite/WAL/草稿/入队与worker领取取消通过，未写生产测试队列。Live3385058、
  worker1300978未重启，四个关键页面200。审计`issue492-20261022/production-acceptance.json`。
  Web既有Windows只读key缺失仍由**#511**承接，不能称该入口已验收。
- Tushare/archive current均`b760b5f`，分别正式39773504561/Deployment7251378853、
  39775472006/Deployment7251684725，workflow/Deployment/实际validator三方success。
  **#508/#475仍在进行**：Windows无效登录挤占MaxStartups已由精确服务端事件证实；
  有限重试及0700私有SSH multiplex已发布。Tushare实际两次probe复用同socket并清理通过，
  archive20:08实际probe仍在首次握手连续reset，客户端修复不能代替#122服务端治理。
  未改密码登录、个人key、Windows SSH配置、RDP/Autologon/QMT恢复。
- 17:00/19:00 Tushare自然任务失败记录保留；19:00真实raw已到301718.SZ的241行，
  但取result.json失败阻断canonical。#512已修复raw/canonical双重存在判断。
  **维护者本会话明确批准“更新该分区并运行质检”后**，持warehouse写锁并重新核验hash，
  原子更新10-22唯一canonical分区：1341933→1342174，只增该股241条，旧行改动0/删除0，
  owner/group/ACL与原备份保留。审计`issue512-20261022/{review,publication}.json`；
  此次特定授权不扩展到其他数据覆盖或删除备份，不切换D-23回测分钟源。
- catalog实际独立根为**`/opt/qsys-data/catalog-verify/current`**，current仍80f7068。
  19:40自然任务failed确因上述241条canonical缺口；获批修复后20:06–20:08实际unit复验
  exit0：636指标/阻断0/既有警告28，12规范表10绿2黄0红0灰。人工复验不改称自然成功。
  原三张质检表历史与冻结基线保留，当前Web/日报读取原failed及新warning共14条唯一记录，
  样本可读/结果只读。`issue469-20261021/post-repair-audit-2026-10-22.json`，原自然审计不覆盖。
- #495明日10:00/10:05自然dry-run待；#488 Redis5强制只读、#482通知语义、#511及
  #475/#508/#122仍未解决。Live仍c741c5f/3385058/重启0、1万元/live/true，marker及旧备份保留。

## 10-22 17:30历史交接

- **#497/#499已关闭，15/15 manifest组件已独立生产部署**；Shadow保持inactive/enabled/live/true，
  reboot-drill保持dry-run，不能称全部业务激活或#330完成。逐项current清单：
  `/data/qsys/var-deployments/issue497-20261022/all-component-currents.json`。
- main/test与生产共享基线`c741c5f7871eef01ab4a01bbce9325ec84ee085d`，固定入口v31。
  Live current同SHA，User/Group=qsys-live-core（UID976），PID3385058/NRestarts0，包内入口，
  WorkingDirectory=/；仍1万元/live/true，严格preflight16/16。Web/digest/observer/checks current
  `06cb7c1`；Shadow current`1e7fd0e`；其余current见清单，不用旧列表覆盖它。
- Live重试dry39753936011、正式39754118067、Deployment7248389752及实际record validator
  三方success。第一次39753509764因缺空live_rejects目录失败，自动补偿到共享入口；失败记录保留。
  #504直接脚本导入修复已通过PR#505及正式39752058392发布；#506承接目录检查提前到停旧前。
- 稳定marker `/etc/qsys/components/live-core/state-binding-v1` **已激活并必须保留**。最新state为
  `/home/ubuntu/qsys/var/live-core/var/live-state.sqlite3`，kill/lock/workspace也在该私有根。
  共享补偿入口同读最新私有状态，**不得移除marker或复制旧库覆盖新事实**。原库未移动或改symlink，
  与停旧一致性备份逐值相等；新库保留63事件/63策略记录/2历史成交，历史行无丢失/改写。
- 四个真实生产消费者已自动切读新库/kill，各自账号对主库及父目录只读，9 sessions/0pending。
  Live仅四张journal目录可写；完整配置不可读，旧state/Workbench/runs/日线不可写。
  证据`final-consumers.json`、`final-state-continuity.json`在issue497审计根。
- 分层证据：hosted真实安装器/代码补偿/部分成交连续性39739215169通过；生产第一次真实失败补偿
  与重试成功分别记录，不能把失败workflow写成成功。没有生产故障注入或重复交易实例探针。
- **#475 17:00自然任务失败**：17:02–17:05更新主数据/派生后，301718.SZ分钟补采Windows SSH
  握手被reset（rc255），收敛入口exit1。发生于Live切换之前，不归因于Live部署；失败journal保留，
  不reset-failed、不造分钟。19:00自然重试/19:15采证仍按既有timer，未手动恢复数据。
- #485今日15:40自然任务/18:15审计、#469 19:40/19:50、#495明日10:00/10:05仍须据实际证据
  收口；#488 Redis强制权限、#492 Web Unix权限、#482维护/异常通知区分继续保留。
  #137首笔自然费用仍待。#330以这些实际剩余项推进，不因15/15计数关闭。
- 本轮普通发布明确授权仍有效，不扩展到交易开关、Windows重启、生产故障注入或删除备份。

## 10:52历史快照（以上述最新交接为准）

### 生产版本

- 共享checkout：`98fff8b837a08b21acb1ff9826a154a50df704e4`（#444），干净；固定入口v30。
- 独立Web current：`34f9cf647926153982178de69fddb803c93653ac`（PR#471，#463），既有回退副本保留。
- Windows live_app（执行代理）与paper_app（行情网关）仍为`43f3f81`（#283）；本轮只新增
  专属取数/只读基线forced-command入口，未重启QMT/代理、未改任务XML/登录与恢复配置。
- catalog/limit-up-shadow `80f7068`、observer `dfd65bf`，checks/storage `b48e84b`，worker `3c182a7`；
  digest/paper `987175e`，tushare-sync `2e89e66`，daily-archive `e005bab`，premarket-limits `37bb3c4`，
  shadow-core `6e4393a`，reboot-drill `57562f4`。Web仍`34f9cf6`。
- **14/14个manifest目标已有独立制品与生产单元**；Shadow仍inactive/enabled/live/true，
  reboot-drill仍--dry-run，不能称全部业务激活或#330整体完成。catalog的既有独立根目录为
  `/opt/qsys/catalog-verify/`，其余在`/opt/qsys/components/`，计数以manifest为准，不只glob后者。
- 最新现场current清单：`/data/qsys/var-deployments/issue495-20261022/all-component-currents.json`。
  分层验收见`docs/reference/component-acceptance-matrix.md`与#330最新记录，所有历史副本保留。

### 实盘（#137）

- 仍为1万元实盘上限，未改资金/交易开关；Live Core PID1748883，启动于10-19 18:21:47，
  本轮所有组件发布及#444共享发布均未重启它。组件发布前后严格preflight通过。
- 10-20 09:25:10竞价事件completed，09:28只读采证无错误；首笔自然股票费用/入场成本仍待，
  #137保持monitoring，不用零成交代替费用验收。
- #433停机闸只读状态与布局已发布；三处均有效解除才显示解除，过期/异常立即未知。
  来源默认折叠、两级导航紧邻。#448服务页延迟、#449窄屏SVG溢出均已发布验收关闭。
- #444实际安装unit显式--dry-run，真实交易日分支exit0、拒绝重启Windows；timer恢复正常，
  failed units=0，旧失败journal保留。实际重启演练仍未授权。

### 本轮迁移收尾与未决项

- #463/#476/#479/#487/#490/#493已关闭；盘前10-22 08:58/09:15/09:24三次自然任务成功，
  09:35采证通过。5655条今日限价直取，QMT校验mismatched0/missing41如实保留，不称全覆盖。
- Paper #490与必要日报读取器联合发布均为`987175e`，各自三方success；Paper实际私有UID、
  supervisor+2workers，MainPID3011011、重启0。10-22两个运行账户开盘/竞价各2项completed，
  09:43采证healthy、Web200、日报可读2条真实投递账；不能据此代替维护者外部收件验收。
  原账户/订单/成交事实保留；Workbench保留原owner，Paper精确named ACL，没有加入workbench组。
- #493 Shadow正式39704790276/Deployment7241246964与7241250075/实际validate_record三方success。
  独立私有身份/配置/状态已生效，仍inactive/enabled/live/true；旧状态/kill/其他peer保持。
  隔离真实初始化与当前消费者显式新路径只读通过，不等于生产启动/自然策略事件或切读路径。
  未来启用需另行明确授权，并联合切换实际生产消费者，不能为验收改成shadow/false。
- #495 reboot-drill正式39712705553/Deployment7242347709与7242350244/实际validate_record三方success。
  实际私有unit保持--dry-run/readonly-baseline；Windows专属key可读基线、shell/SFTP/额外命令拒绝。
  原授权/ACL/boot/关键进程/任务XML保持，未执行重启或外发测试通知。隔离真实基线、Web/日报
  新旧记录只读与实际生产入口交易日门禁通过；**timer已恢复active/enabled**，原定义不变。
  status:validation：10-23 10:00首次新版本自然timer待，10:05节点只读采证已安排。
- #485 daily-archive current`e005bab`，正式39642942325三方success；6步骤真实任务成功，
  7天质检0阻断、canonical9,387,766行逐键逐字段对账及当前消费者通过；10-22 15:40自然任务待，
  18:15只读采证已安排，不能提前关闭。
- #475 tushare-sync current`2e89e66`，真实19步骤/限权Windows取数/当前消费者通过；
  10-22 17:00/19:00自然任务及19:15采证待。#469 catalog当前`80f7068`，19:40任务/19:50采证待。
- 下一主项 **#497 Live Core最后独立迁移**：生产仍共享`98fff8b`/Ubuntu，PID1748883保持。
  当前只做方案/依赖与隔离准备，不在交易时段顺手重启；先联查Web/日报/observer/checks的
  live-state及kill/preflight/共享journal写者，停止旧生产者后再取最新一致性状态，防止漏订单/成交。
  现有14组件通道要求Live身份不变，不能关闭全局peer保护或借其他组件发布绕过它。
- #488：Redis5经无密码loopback隧道访问、无ACL，代码只GET不等于强制只读；不擅自升级/重启Redis。
  #492：Web既有Unix workbench组还可写var/runs，unit沙箱阻断不等于Unix权限收敛，待单独修复。
  #482运维通知维护/失败区分仍未实现，不宣称全部异常告警根因已解决。
- #485/#487/#490/#493/#495证据根为`/data/qsys/var-deployments/issue<编号>-20261022/`；
  #473首次缓存权限失败、#495首次timer门禁拒绝等证据均保留。新失败先摘要/定点修复，未变CI不重复跑。

### 数据恢复历史证据（10-20 01:24快照，当前状态以上文为准）

- 10-19 归档恢复：#434 运行目录、#438 退市日、#440 未收盘候选问题均已修复并发布。
  10-20 01:13:47–01:16:47 手动归档成功，按最近已收盘日 10-19 执行；竞价候选 195 组、
  分钟 33,381 组全部已齐，6 个交易日跨源校验 0 阻断。canonical 10-19 已发布；没有请求或
  写入未收盘的 10-20 数据。生产恢复前保留 20 个分区备份及 SHA-256，审计目录
  `/data/qsys/var-deployments/issue440-20261020/`，代码回滚点 `c328b8c`，数据不随代码反向恢复。
  本次仅发布 linux-data-jobs，Live Core PID 1748883 / 启动时间 10-19 18:21:47 保持不变。
- canonical 10-19 与原始分钟线均为 **1,340,844 行 / 5,564 只**，重复键 0；写后对账
  source_only / target_only / mismatched 均为 0。旧 canonical 分区、原始竞价和分钟文件 hash
  未变，20 份备份完整。已知 D-23 未结，不切换回测分钟源。
- catalog-verify 01:17:22–01:18:22 成功，无 OOM：636 条字段结果、阻断 0、既有警告 28；
  支持的 12 张规范表为 **10 绿 / 2 黄 / 0 红 / 0 灰**，7 条关系全绿。
  黄项如实保留：名称区间 246 个已知问题；分钟 8 个非 240/241 根标的日。
  其他未覆盖表仍可灰色，不能将本结果表述为全仓库已验收。
- service-checks 按 10-19 业务日回看：01:18:44–01:19:29，28 项、数据失败 0、策略失败 1；
  catalog 两项旧失败显示 repaired，10-19 早盘 STALE_QUOTE 保留，存储保留告警保留。
  使用临时最小配置禁用额外告警发送，随后删除临时配置；归档与质检 runtime drop-in 均已移除，
  实际 unit 与执行前逐字比较一致。没有 reset-failed 或改写历史。
- 当前独立 Web 与日报的实际账号/venv 均能读取 15 条新旧记录，identity 无重复；Web 归档
  详情已消除启动失败 banner，服务/实盘/回测/表页均 HTTP 200。16 项实盘只读检查全通过，
  当前空仓 / 活动委托 0、三处停机闸未生效、启用资金仍 1 万元；systemd failed unit 为 0。
- **#442（P2 / in-progress）**：已实测确认持续更新的 live_equity 使全仓缓存失效，重复扫描
  约 19 亿行分钟数据。修复改为按表精确缓存、后台统计与明确的待更新/失败提示，见
  `docs/reference/data-catalog-latency.md`。本条记录代码推进状态，生产激活与实际时延以
  #442 最新发布证据为准；不能把本地测试写成生产已经解决。

### 在途 Issue

- #330仍为总跟踪：14个manifest组件已独立部署，#497承接Live Core；#485/#475/#469及#495
  保留各自自然验收，#488/#492保留残余权限，不因计数完成提前关闭总项。
- #137/#126继续自然交易费用和30日容量观察；#123/#122/#283/#138/#250按维护者排期。
- #304/#234/#230/#139/#142/#143保持各自外部/口径阻塞，不擅自购买、删数据或更改认证入口。
- 历史数据恢复#434/#435/#436/#438/#440、页面#442/#433/#448/#449、参数#444已闭环。

### 本轮授权与证据

维护者明确指示“全权授予你部署和发布”，本轮据此完成 #440 发布及 10-19 数据恢复。
发布 PR #441 由 Codex 代行逐文件自审；test CI 39291429145、测试部署 39291676091、
PR CI 39291812341、生产 dry-run 39292039046、正式发布 39292155034 均 success。
GitHub Deployment 7179993822 success，节点 `issue440-20261020-release-linux/record.json`
经实际 validator 校验成功，三方一致后才启动归档。本轮没有故障注入、删备份、改资金或交易开关。

以上 SHA 和健康状态会变化。接手时至少重新查看 Issue、Actions、远端分支和节点只读状态。

## 不可擅自跨越的授权边界

- 本轮普通部署与发布已有维护者“全权授予”明确授权，执行前仍核对精确候选、受控 workflow 与现场门禁；该权限不扩展到故障注入、交易开关或不可逆数据处置。
- 未获得维护者明确确认，不得执行生产自动回滚故障注入演练。
- 不能仅凭“继续”“按建议处理”或本交接文档推定超出现有明确授权范围的新许可。
- Linux 与 Windows 都必须保留维护者的密码登录能力。
- 不得关闭 `PasswordAuthentication`，不得删除个人 SSH key，也不得更改 Windows
  Administrator 密码、RDP、Sysinternals Autologon 或 QMT 自动恢复配置。
- 不得把密码、私钥、token、完整配置或其他秘密写入 Git、Issue、Actions 日志或交接文档。
- 不得把代码回滚扩展成数据/schema 自动反向操作。

## 只读接手检查

这些检查不会授权后续写操作：

```bash
git status --short --branch
git fetch origin main test
git rev-parse origin/main origin/test
gh issue list --state open
gh run list --workflow deploy-production-linux.yml --limit 5
```

Linux 节点只读检查应确认生产 SHA、工作区、监控、health 和密码认证状态；不要在检查命令中
打印配置内容或任何 secret。预期快照是：共享生产 `98fff8b`、独立Web `34f9cf6`、入口 v30、工作区干净、
`qsys-live-core` / `qsys-monitor.service` active、`8420/healthz` 为 200、`PasswordAuthentication=yes`。

## GitHub 与节点名称

- Workflow：`Deploy production Linux`；只允许 `workflow_dispatch`，不会因 `main` push 自动发布。
- Environment：`production`，部署分支策略仅允许 `main`。
- Variables：`PRODUCTION_DEPLOY_HOST`、`PRODUCTION_DEPLOY_USER`。
- Secrets：`PRODUCTION_DEPLOY_SSH_KEY`、`PRODUCTION_DEPLOY_KNOWN_HOSTS`。
- production forced command：`/usr/local/libexec/qsys-production-deploy-ssh`。
- production entrypoint：`/usr/local/libexec/qsys-production-deploy-entrypoint`。
- production entrypoint config：`/etc/qsys/production-deployment-entrypoint.json`。
- production records：`/data/qsys/var-deployments/github/<run-id>/record.json`。

只记录名称，不尝试读取或回显 secret。部署 key 轮换只能替换带
`qsys-github-production` 标记的授权行，不能影响其他登录方式。

## 交接完成判据

接手者应先核对：当前生产 SHA、最近发布/回滚节点记录、服务与 health、Windows 心跳，
以及密码登录保留要求。信息不一致时停止写操作，先更新 Issue。
