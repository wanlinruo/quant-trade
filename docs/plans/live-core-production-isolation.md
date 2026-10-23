# Live Core最后独立迁移（#497 / #330）

## 2026-10-23状态库辅助文件生命周期（#529）

13:28与13:30 observer两次短暂无法读取新私有库，约10秒后恢复；实盘进程持续运行，
14:25前四个事件全部completed，不将监控读库异常等同于实际交易事件失败。
在独立合成库、真实qsys-live-core/qsys-health-observer身份下，生产者最后一个连接关闭后
WAL/SHM消失，普通只读UID报SQLITE_READONLY_DIRECTORY；相同ProtectSystem=strict、
ProtectHome=read-only沙箱下复现现场SQLITE_CANTOPEN（unable to open database file）。
生产者保持无事务的连接时读者正常，关闭后再次失败。原始基线证据位于
`/data/qsys/var-deployments/issue529-20261023/baseline-sidecar-reproduction.json`。

修复由Live入口在取得单实例锁后创建LiveStore并pin_reader_sidecars，生命周期覆盖Runtime，
退出或初始化失败时关闭。锚点不持有事务/旧快照，不改SQLite schema、订单或事件，普通事务
仍原样提交。只读LiveStore禁止pin，消费者的目录/库权限维持只读，不能用immutable绕过WAL。
observer仍对读库失败发CRITICAL，文案明确“状态库读取失败”；同类去重、读通后恢复，若读通
却发现实际事件失败则另报交易链路失败，不因两者同属DOWN而漏掉状态转换。

该段记录设计与隔离归因；CI、最终制品、实际生产发布及后续自然观察证据分别以
[#529](https://github.com/wanlinruo/quant-trade/issues/529)最新评论为准，不将代码合入视为现场生效。

14个既有manifest目标组件已独立部署；Live Core另列且仍共享运行，不因14/14计数自动发布。
本计划先提供包内入口与隔离前提，生产激活、消费者/状态切换与控制面扩展另分层验收。

## 第一阶段：包内入口与默认兼容

将原scripts/run_live.py实现移到qsys.live.core_cli，旧脚本调用同一main；新增qsys-live-core
console entry。默认仍为旧state/workspace/lock、本地var/KILL、主warehouse journal与var/alerts；
mode=live且orders_enabled=true的原双条件门禁先于任何Redis与状态构造，三次连续失败的急停、
cancel_all和critical通知保持。单实例锁先于Redis/策略快照/Store/Runtime初始化，避免重复
实例抢锁前产生副作用（#499）；Shadow同类问题另验，当前仍停用。显式--state/--workspace/--lock及新增--kill-switch/
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

## 2026-10-22 现场初始化证据与待接线边界

生产共享98fff8b与main323d496的LiveRuntime、LiveStore、Broker、messages/config、agent、
策略目录与SessionJournal无差异（逐路径git diff）；入口新增参数与抢锁顺序另验。
12:05现场Live PID1748883、重启0，状态数据库106496字节且WAL持续变化；只拷贝主文件
不能作为一致性迁移。当前实际消费者四个独立版本均仍指向旧live-state.sqlite3。

新增`scripts/verify_live_core_preproduction.py`只接受验证根内的
预先备份，不拷贝/覆盖生产库。真实配置仅作只读输入，移除通知与真实账号白名单，保留风险和
策略参数；策略与工作台相对路径以显式source-repo解析。固定GET账户/两条心跳/kill原值，
不读真实订单或成交流，不重写时间戳；所有写入统一到新validation namespace，SET NX预留
拒绝复用。以真实LiveRuntime构造恢复状态，启动隔离心跳两秒后退出，不调用tick。

12:14现场验证成功：使用test2fa8220的实际Runtime，一致性状态副本中59条事件、59条策略记录、
2条历史成交和全部其他表schema/逐值摘要前后完全相同；0待处理订单，隔离心跳成功、通知关闭。
证据`/data/qsys/var-deployments/issue497-20261022/initialization-1220/result.json`。
原值快照新鲜度约1.82/0.81/0.50秒。生产Live进程保持，未访问生产订单/成交流，未执行策略tick。

该证据是**实际Runtime初始化层**：不是最终不可变制品、私有Live账号/最小配置、策略tick、
N/N-1安装器失败补偿或生产激活验收；Redis命名空间也不等于原生ACL（#488）。临时unit
ProtectSystem=strict、ProtectHome=read-only，只写专属验证目录，非Live最终私有身份。

切换设计必须同时解决：

- 新旧入口和当前四类消费者必须绑定同一个最新状态位置；不能仅让新入口读私有库，而恢复
  旧unit时读旧路径。一次性一致性seed并不能证明切换后的补偿连续性。
- `scripts/kill.py`目前只识别Shadow独立drop-in；Live固定旧var/KILL。Web当前也固定旧路径。
  如果迁移本地kill，须先让实际维护入口与当前消费者识别新位置。简单将旧KILL改为符号链接
  不成立：release/unlink会删除链接本身，不能保持双路径语义。
- journal四表live_equity/live_fills/live_rejects/live_recon当前位于主Warehouse；运行时代码是
  唯一SessionJournal写入口，但仍须联查节点定时/维护作业。消费者直接读主Warehouse，不能
  把journal指到空私有目录后宣称净值/成交历史连续。
- Live正式目标仍未加入manifest/workflow；全局14组件Live身份不变保护保持原样。后续新增
  目标必须增加精确目标例外、其他peer保护和实际validate_record回归，不能只改workflow选项。

12:18用当前生产Web/日报/observer/checks各自私有Unix身份和已发布venv的真实LiveStore读取
同一验证状态副本：四者均成功读到9个策略session、0持仓/0待处理订单，副本与父目录均不可写。
证据`initialization-1220/current-consumers.json`。该项证明显式新路径可读，尚未改变生产发现路径，
不能代替最终切换后自动发现/去重及页面验收。SQLite WAL读者还须逐个核对sidecar access ACL。

## 稳定绑定与首次发布失败补偿设计（实施中）

选定独立根`/home/ubuntu/qsys/var/live-core`，其内保留`var/live-state.sqlite3`、`var/KILL`、
`var/live-core.lock`与`var/live`布局，使旧代码的相对默认本地急停与新入口显式路径一致。
代码current切换与状态绑定必须分开：一次性root只读marker为
`/etc/qsys/components/live-core/state-binding-v1`，固定内容`qsys-live-core-state-v1\n`。
marker不接受任意路径；拒绝symlink、非root owner及group/other可写，异常时Web显示未知、
CLI拒绝resume，显式stop仍尝试所有已定义本地路径与Redis并以非零退出要求核验。

Web每次读本地急停时重新解析绑定，预发布读取器不用等Live迁移时重启才能发现新路径；显式
自定义验证路径与未配置仍保持原语义。绑定不依赖`zz-isolated-release.conf`存在，组件失败
补偿移除该drop-in后不会自动切回旧kill。没有marker时现有生产路径保持。

**这里只新增路径选择器、Web只读识别和维护CLI兼容，尚无创建生产marker或迁移状态。**
选择器回归不冒充N/N-1安装器/交易连续性验证。正式通道尚须实现：

1. 当前生产维护脚本与Web先具有该识别能力，并逐一核验实际版本；不因main已合入而假定生效。
2. 在受控Live通道停旧并持有真实单实例锁后，最新一致性SQLite备份逐值校验到新私有根，保留
   原库、WAL及审计；原本地kill状态按事实保留，不解除/触发生产急停来验收。
3. 先升级共享失败补偿入口：默认state/workspace/lock/kill在marker存在时选择同一私有根；
   LiveStore每次连接重新解析默认路径，使预先发布的四类长驻读取器在激活后自动切读。
   自定义路径和validation namespace不重定向。首次迁移控制面逐字核对共享关键入口和四类
   实际消费者的已安装实现；后续独立升级不要求重新发布共享源码。
4. 新生产者运行前，以真实单实例锁保护一致性备份并逐值验证；marker是唯一激活点，原数据库
   不移动、不覆盖、不改为符号链接。组件drop-in/current失败补偿只切换代码，marker保留，
   共享补偿入口也写同一最新库。无需额外持久legacy unit或生产回切命令。
5. 新增GitHub-hosted一次性Live安装器/状态夹具：实际安装→移除drop-in→实际daemon-reload
   失败补偿→重新激活；独立账号与共享入口身份交替读取/追加实际LiveStore订单、部分成交和
   完成事件，证明读到最新事实、旧库不变及完成事件不重放。该夹具禁止节点/本地执行，
   不运行策略或连接券商，不能冒充生产业务激活。
6. 主Warehouse四张journal表保留既有事实与合法写者，以精确ACL限写这四表，其余市场表只读；
   如改journal目录，必须先联合发布全部实际读取器，不跳过历史。


## 受控Live目标与发布顺序（实现待发布）

manifest新增第15个目标live-core，原14个生产目标不受影响。入口v31/capability
component-live-continuity-v1才允许选择该目标；只有此目标的PID/启动时间可以变化，Paper、
Shadow、Web、worker、observer身份均受保护，原14组件Live身份不变校验保持。
新Live启动后必须产生晚于停止旧进程时点的真实core心跳，不能只看systemd active。

发布顺序：稳定test CI与测试部署→合入/CI workflow bootstrap证据→最终Live制品及托管
安装器状态夹具→四类当前消费者先独立发布→15:40之后共享补偿入口基线发布→冻结最小配置
与私有策略文件、实际账号/ACL复核→受控Live dry-run/正式发布→workflow/Deployment/实际
record validator三方成功→实际订单/成交/心跳/消费者连续性验收。交易日08:45–15:40拒绝重启，
不以“继续”推定紧急例外。首次迁移阶段可能有共享基线与独立化两次短暂停机，各自留记录。

私有配置显式冻结当前解析的全部risk/fees以及stream/safety/strategy参数，不依赖下一版本默认值；
file策略复制为root只读私有输入，工作台绝对路径保持，未改原配置。prepared-only没有marker，
不能当作生产已经迁移。实际源配置生成已只读探针通过；当前Live内存约249MB、峰值318MB，
进程VmHWM约324MB，拟定MemoryHigh1G/MemoryMax2G/Swap512M；自然策略事件另行观察。
私有目录default ACL同时保留组件与合法补偿写者，覆盖对方创建的WAL/SHM/KILL；安装后文件
access ACL单独核验，不用目录default ACL替代。


## 10-22生产迁移结论

#497/#499已关闭：Live current c741c5f，正式39754118067、Deployment7248389752及实际
validate_record三方success；PID3385058/NRestarts0，私有UID976、独立venv、16/16严格检查。
四消费者06cb7c1已自动切读新私有state/kill且只读。63事件/63策略/2历史成交逐行保留，
原库与停旧备份全摘要一致。marker已正式激活，代码补偿不移除它、不恢复旧state。

首次39753509764因不存在live_rejects目录失败，自动补偿共享入口继续读写最新私有库；
准备空目录后新ID重试成功，保留全部失败记录。#506承接停旧前目录检查；#504直接脚本导入
修复经正常PR#505正式发布，不手改节点代码。hosted状态补偿39739215169与生产失败补偿
分层记录，不冒充生产故障注入。完整证据见#497最后评论及HANDOFF最新交接。

#330仍保留自然验收/权限残余；17:00同步Windows SSH reset由#475承接，#137自然费用仍待。
