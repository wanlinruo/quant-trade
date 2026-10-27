# QMT 恢复优先级与夜间排障（2026-10-26 至 10-27）

承接 [#533](https://github.com/wanlinruo/quant-trade/issues/533)、
[#540](https://github.com/wanlinruo/quant-trade/issues/540)、
[#543](https://github.com/wanlinruo/quant-trade/issues/543)、
[#544](https://github.com/wanlinruo/quant-trade/issues/544)。时间均为 Asia/Shanghai。
维护者明确授权按评估方案继续排障和可恢复调整，仍保留真实交易、数据覆盖及质量门禁。
本记录不包含账号、配置正文、截图、凭据或原始账户日志。

## 1. 恢复顺序与当前数据事实

最小数据保底比完整实盘恢复更快；全市场分钟/竞价恢复没有已验证正式上游，不能假设它更快。
保住 Tushare 日线底座，优先排除客户端不可用和监控读取故障，再完成策略所需的最短实盘闭环。
全市场归档回补后置，但策略依赖的竞价、实时行情、历史窗口与风控不能省略。

| 10-26 自然运行 / footer | 结果 | 限制 |
|---|---|---|
| Tushare 19:00 | 日线直取 5570 行，年度文件 1068569 行、最大日期 10-26 | 整体 warning，不是全部质量通过 |
| 每日归档 15:40–17:23 | 分钟下载超时 6000 秒；进度 2988/5570、失败 2988、落盘 0 | 有行情服务连接失败，不单独归因 SSH |
| 两张分钟表 10-26 分区 | 均不存在 | 行数记未知，不能造 0 行或补 bar |
| 竞价年度表 | 275270 行，最大日期仍 10-23 | 不代表当日到货 |
| 20:00 到货检查 | 分钟缺 5570 股，候选竞价缺 72 项 | 真实缺口保留告警 |
| 增量质量 / catalog | auction_ticks.dt / B4.arrival 阻断 72 项 | 不改门禁或放宽阈值 |

`qsys-issue475-natural-audit.service`、`qsys-issue515-daily-archive-natural-audit.service`
是 10-22、10-23 的历史失败，不能算成今天新增故障。Live 按授权停止属计划状态；
主库读取失败、新客户端启动异常则需独立处理。进程健康不等于行情新鲜、数据完整。

## 2. 归档定时触发临时暂停（#540）

23:49 重新确认归档 MainPID=0、旧 Live MainPID=0 后，仅执行一次
`systemctl stop qsys-daily-archive.timer`。验后 inactive/dead，原 enabled 保留；
归档原 failed 与历史记录保留。Tushare timer active/enabled、日线自然任务保持。
未补采、切源、覆盖数据、关闭质量任务或修改源选择。

审计：`/data/qsys/var-deployments/issue540-20261026-archive-pause/pause-record.json`。
这是当前开机期间的暂停，节点重启会恢复 enabled timer。恢复前应确认正式上游可用、
小范围实际到货及质量通过，再恢复定时触发；不能直接重放失败的 6000 秒全市场下载。
归档与日线窗口重叠是风险，尚无证据认定它造成日线失败。

## 3. 停机后状态库读取（#544）

真实主库仍在，停止 Live 后 WAL/SHM 已不存在。实际 qsys-service-checks UID 以 mode=ro
读取复现 READONLY，原只读 systemd 沙箱报告 CANTOPEN。这是状态读取故障，不证明当日
发生了新的委托或策略执行失败。不能启动旧 Live、创建空库、放宽消费者目录写权限或用
immutable 绕过 WAL 来掩盖。

隔离 fixture 使用实际生产者/读取者 UID：冷库只读失败，生产者身份只读 URI + query_only
辅助连接存在时，真实读取 UID 和 ProtectSystem=strict/ProtectHome=read-only/PrivateTmp
沙箱均读通；业务 INSERT 拒绝，fixture 主库 SHA 不变。

23:51 启动临时 `qsys-issue544-state-reader-anchor.service`，只运行独立 SQLite 只读连接，
不导入或初始化交易 Runtime、Redis、通知或 LiveStore。真实 checks/observer UID 在只读
沙箱各读取成功：70 条本地历史事件、0 条本地 orders。计数不是券商对账或空仓证明。
真实主库 SHA、mtime、owner/group/mode 不变，WAL=0 字节、SHM=32768 字节。
自然 observer 已从 strategy_state_unavailable 转为 strategy_event_failed，不能据此报 healthy。
今日自然 service-checks 的原失败未重跑、删除或覆盖。

审计：`/data/qsys/var-deployments/issue544-20261026-reader-anchor/` 下
`fixture-result.json`、`activation.json`、`anchor-readonly.py`。
辅助源码 SHA256：`474aa9c7d18df259420ce97aa4262935c06df9358e569905006e5b83510c53a2`。
真实主库 SHA256：`a44f1befac5254cd65d8f905ec8fae0081eac418e86f0c7be421cbd498fd5190`。

**本项仅临时缓解，#544 不关闭。** unit 与脚本最长运行 86400 秒，约 10-27 23:51 到期，
节点重启亦不会自动恢复。到期前须复核接管进度与正式维护方案，不盲目重放 activation。
永久停机态读取及维护状态展示仍需正常开发、验证与发布。

## 4. 新客户端单次受控恢复（#543）

21:45:28 Windows Application Event1000：XtItClient.exe 2.1.33.0，模块 ucrtbase.dll
10.0.14393.9060，异常 c0000409、偏移 000000000006de4e。原 PID2712 停留重新初始化页面，
Crashui5436 存在。Lua nil/customModules 缺文件日志不证明故障根因，未修改配置或替换 DLL。

23:52 精确核对根目录、PID、启动时间、session、唯一客户端及四个原 task XML 后，单次
停止这两个异常进程，从新版 bin.x64 工作目录启动原 exe。CreateNew 维护 marker 禁止重放。
新 PID5452/session1，23:52:42 启动；只读窗口截图确认进入登录后模型研究，原诊断模型存在。
公告已关闭，未改“不再提示”等设置。原四任务 XML 不变，三个辅助任务 disabled。
未更改 QMT 自动恢复、密码、RDP、账户或交易配置，没有下单、撤单或 KV 调用。

Windows 私有审计沿用 `C:/qsys/var-deployments/issue533-20261024-qmt-install/`，包含
`client-startup-restart-requested-20261026.json`、`client-startup-restart-result-20261026.json`。
原始日志和截图不入 Git/Issue。一次重启恢复界面不等于根因修复或长期稳定性通过。

23:57 尝试打开只读编辑器时，前台守卫在输入前拒绝；三个辅助 task 已禁用，未继续尝试
绕过窗口守卫。此时尚未执行新客户端原生查询。

### 00:09–00:15 复核发现再次异常，覆盖此前恢复检查点

人工单次恢复后的 PID5452 已退出；客户端自身于00:00:06拉起 Crashui2692，继而
00:00:38出现新 PID2864。代理没有执行第二次重启。00:00:42再次出现 Event1000，
故障 PID 十六进制 b30 即2864，模块、异常码、偏移与此前一致。
00:12只读 PrintWindow 确认 PID2864仍停在“正在加载”splash，编辑器当前不可用。
仅发现一份32字节 `.dmp`，不能当有效完整崩溃转储或据此编造调用栈。

已核对 OS 为 Windows Server2016 Datacenter / build14393。XtItClient.exe、python36.dll、
msvcp140.dll、vcruntime140.dll、ucrtbase.dll 的SHA均与此前校验的官方安装包payload一致；
这不能证明没有环境或用户缓存问题，也不能直接归因为OS不兼容。
00:13重新查询[官网正常软件下载接口](https://www.jhzq.com.cn/main/home/software/index.shtml)
`/servlet/json?funcNo=741004&software_type=1&curtPageNo=1&numPerPage=1000`，software_id=78
仍为2.1.33.0/10-23，MD5仍为93b9a64d6913a47f3bb34bb7383701a8，没有取得更换安装包的依据。
未重复下载、重装、替换DLL、删除缓存或重置配置。

该检查点 #543继续P0，**客户端稳定性没有恢复**；先整理上述版本、OS、两次事件、故障偏移与
发生条件供券商/迅投定位，再决定有证据的修复。不向券商自动发送消息，不循环重启。
既有Lua日志不作为已证实根因；不得发布账号、原日志或截图。

### 00:26–00:45 发现系统盘耗尽，保留全部备份后缓解并重新验证

独立登记[#545](https://github.com/wanlinruo/quant-trade/issues/545)。实际C盘仅余约8MB，
内存仍余约5.5GiB。只有一块约100GiB系统盘和C卷，无可直接使用的其他Windows数据盘。
`C:/self`逻辑文件约64.8GB，审计目录约6.88GB；逻辑字节不当作实际占用/释放量。
磁盘耗尽是启动和转储的严重风险，但尚未证实与所有ucrtbase故障的因果。

按维护者对可恢复排障的授权，仅对原审计根
`config-backup/userdata_mini-root/`的静态副本做原生NTFS压缩：

| 批次 | 精确清单范围 | 验后C盘实际余量 |
|---|---|---:|
| 紧急小批 | 两个142606616字节的xtmodel队列副本 | 290717696字节 |
| 已盘点批次 | 77个75497752字节的队列副本 | 5548810240字节，约5.17GiB |

逐文件检查单硬链接、无外部硬链接、非reparse/sparse、原size/mtime稳定；压缩后79文件
SHA256、逻辑大小、mtime、ACL均不变，compact返回均0。文件/路径/回退副本保留，删除0，
活动业务文件修改0；不因队列名或看似空白认定没有业务事实。第一次清单拼接错误在校验阶段
拒绝，未发生压缩，修正后执行；没有盲目重放已请求的动作。
私有审计为`capacity-emergency-compress-20261027.json`和
`capacity-backup-queues-compress-20261027.json`，保留精确清单、内容/权限及容量证据。

完成容量缓解、实际余量至少4GB且目标PID/路径/启动时间/session/原任务仍匹配后，00:39
单独执行一次有界启动复验，使用新的`client-capacity-retest-requested/result-20261027.json`。
这次输入条件已有实质变化，不是循环重放此前失败。新PID5540/session1，启动
00:39:00.8500849；00:40只读截图确认登录后的模型研究界面。未替换DLL或改配置/自动恢复，
原四task XML不变、原三个UI辅助task禁用，原生KV/账户/交易调用0。

新增独立只读任务`qsys-issue543-capacity-observe-20261027`：每60秒检查精确PID、
新安装根Crashui、Application1000及C盘余量，共最多20分钟，任务时限25分钟，完成自禁用。
它不重启客户端、不发送消息、不执行策略或交易。结果文件
`client-capacity-observation-20261027.json`，需读completed/reason及采样后才判断观察结果。
辅助源码SHA256：`15ddefc47b8861959fba72069fcc9d0ae17acffd17940c18c17f7375f30269b4`。
界面恢复和有界观察均不能单独证明长期稳定或全部根因消除，#543/#545保持开放；
长期容量治理与扩容方案由#545/#126承接，本轮未购买、扩容或删除业务/回退数据。

WTS独立只读查询确认session1为Disconnected，解释当前前台不可交互；待远程桌面连接后
继续探针，不变更RDP/Autologon或绕过窗口守卫。已准备的原生探针仍未上传/执行。

## 5. 实盘剩余关键路径

1. 在当前实际新客户端只读查询旧诊断键，独立验收跨客户端持久性；没有新读回前保持未知。
   已准备日期限定为 10-27 的单 GET 探针，源码 SHA256
   `52dd42185c3cf521e812c06a03eeab1357bc42c1ce6fa103eefee988f9feae86`。
   Python3.6 语法解析及离线成功/空值/错误码/异常四路径通过，每次只读一次。
   **尚未上传或执行**，离线结果不作原生持久性证据。
2. #542 优先确认首次恢复日及必要滚动窗口的股票池、历史限价、停牌、ST 与 Gate 输入。
   当前沪深A股历史名单为空不能由格式更换、当前名单回填或空候选来“解决”。正式板块目录
   API 可用于发现原生板块名称；get_sector 文档仅支持指数历史成分，不盲目作全A回退。
3. #541 完成真实账户新鲜度、持仓与活动委托、pending、逐lot、成交标识及去重闭环。
   19 字节诊断值读写成功不证明容量、事务原子性、多实例或完整账本恢复；不扩写为 5000 字节。
4. 若维护者有已正式开通且当前可用的其他券商接口，比较复用既有策略/账本的整体成本。
   尚未取得该信息，不假设其他券商/平台天然可用，不开账户、转资金、购买或实盘试单。

代码合入、CI、测试部署、原生样本通过与真实交易激活仍分别记录。旧 Live 保持停止，
KILL 与资金上限保留，未激活新版交易。

GitHub 评论请求曾在直连及本地代理上超时；HTTP 超时不证明服务端未写入。
恢复请求后按完整正文精确匹配，再补记#540/#544/#543/#533/#541；没有重复POST相同评论。
`fd212f3`的push CI40356202214与PR CI40356209418均success；只证明该文档提交的检查，
不证明Windows原生运行或实盘通过。当前复发补充提交的证据另行核验。
