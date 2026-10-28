# 项目交接入口

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
