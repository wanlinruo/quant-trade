# 送配事件与量因子恢复（#540）

## 八项历史现金修复已完成（#558，10-29 00:24）

维护者明确确认后，持真实写锁于00:22:54仅替换事件文件八个现金值；61085行其余
单元/键/顺序/物理schema保持，六真实UID前后读取及实际WarehouseQuery逐值验收通过。
现生产SHA df021519...，原eab786c3...独立备份保留；两因子表、状态/KILL、checkpoint、
组件和原timer均保持。完整留证见[生产验收](../reference/cash-dividend-production-acceptance.md)。
此前八项“待确认”已执行，不重放。未来增量现金策略未变，另八条null/0保持源值；
#558继续跟踪，#540仍待自然业务。

## 现金独立核验更新（#558，10-29零时）

八条非空差异已由实际实施公告独立验真：公告每10股税前金额的纯派生与Tushare逐条
精确一致，QMT原值不同。已准备只改八个现金单元的本地候选，未执行生产覆盖。另八条
null/0保留两源原值；三份公告明确不派现，五份仅列转增，不将其解释为补0许可。
完整来源、PDF SHA、逐值/schema与现金消费者隔离验证见
[审阅包](../reference/cash-dividend-announcement-review.md)。未来自动增量现金策略未改变。
此前现金“未完成独立公告核验”的描述为当时历史状态，本节覆盖其当前进度。

## 最终实施与分层验收（2026-10-28）

维护者明确批准后的PR#552、必要控制面#553/PR#554、清单修复#555/PR#556均完成正常
CI/test/逐文件代行自审。最终生产source为3faccebeb9736486f801ce1c91861b95083d7f4b，
controller v35；最终CI40682291567，dry-run40683731604，正式40683959984，GitHub
Deployment7384688652及实际node record一致success。实际调用validate_record，source/
components/status精确匹配、六peer与Live PID0保持。artifact12813541008 digest/节点ZIP：
`4a84e0fc13a4c0acda03c7c7b23fa9008c3a9b565cd263d18403be25671e54df`。

组件安装过程保持原failed/PID0/exit-code1/Invocation146edac...，未reset-failed；
三方成功后恢复原timer active/enabled，再用新版本独立人工run验收。Run
`tushare-sync-200201-2173949`于20:02:01–20:07:06完成20步骤，succeeded/exit0；
invocation7d3812657e1d4dcb829919b1097448ff、实际业务PID2173949/UID983/parent2173945
与完整生产unit资源/权限/查询环境/沙箱契约现场绑定，MemoryPeak3221487616字节。

- 完整5941响应：5 done/5936 empty/failed与incomplete均0；5事件新增0（均已存在）。
  scope排除仅T600018.SH及直取退市依据。bundle SHA
  `03fd67a536436699d9eba5ec9fe6dd5b004edbbdeb2e68cc9027ff5234f5962b`，scope SHA
  `40b7b09c535895e7bbeec600cddb44fb03eb4237c09994664057aec07d81ee70`；checkpoint
  结束2026-10-28，身份/范围/覆盖/SHA全部验收。
- 原61085事件文件SHA仍eab786c3...、逐字节保持。今日5570行因子与直取及规范表精确
  一致，12只336行历史样本保持；原构建器/11表canonical对账通过，未改算法或阈值。
- 20:06增量556项0阻断，19点原5失败仍保留。20:00原service-checks warning保留，
  不能以20:07成功覆盖其当时结果；20:30自然日报汇总/邮件均succeeded。
- Windows正式两个专用key、不同命令及真实字节/hash、shell/跨组件拒绝现场通过；
  新快照实际在E盘，QMT3788/159个qsys任务SHA保持。C/E余量分开观察，未迁活动目录。

最终审计根`/data/qsys/var-deployments/issue555-dividend-scope-20261028/`：
three-party-verified.json、business-running-contract.json、snapshot-and-old-facts-verified.json、
factor-quality-verified.json、final-business-and-boundaries.json及全部请求/原参数备份。
生产record在`/data/qsys/var-deployments/github/issue555-dividend-scope-20261028-tushare-sync/`。
首次失败及Windows空作业目录保留，不重放。人工验收不能冒充首次自然恢复完成。

已在节点安排10-29 17:20只读timer qsys-issue540-next-natural-20261029，继承生产身份/
资源/查询环境并将写路径收窄为审计输出；只读业务记录、scope/快照/检查结果，不调用
API/启动业务/写Warehouse。此时仅scheduled，次日结果输出next-natural-output/report.json。
原KILL、模拟/实盘门禁、8条非空现金/8条空值源差异、#550/#551、D-23保留，#540不关闭。

## 19点自然运行与历史目录身份修复（#555）

首次正式组件workflow40675756482/Deployment7383415419/实际record成功，18:58:58恢复
timer；19:01:57自然触发的ServiceRun190159-2153226仍于19:03:16 failed。送配清单PUT
被严格证券标识门禁拒绝，未发QMT run。Tushare事实已写、事件/派生/canonical未执行，
after_sync保留5阻断。不能以组件安装成功替代自然增量验收，不重放此run。

5942行130736字节manifest SHA d44b00c87298674d058bbfdd0b35dd244fbc8aa1dbde44c34f30878716af0dd0，
在同版本地validator同样失败；唯一非法协议标识T600018.SH。当前直取instruments记录：
name上港集箱(退)、list_date2000-07-19、delist_date2006-10-20、as_of2026-10-28，旧事件0。
Windows对应目录为空且run-requested不存在，排除误判为上传容量/执行超时。

候选仅将有直取退市日期且早于窗口的**非原生目录身份**排除并记录scope.json/SHA；
所有六位原生身份继续请求（包括退市与主表外历史代码），不扩大为所有已退市证券过滤。
缺证据、窗口内或当前非原生身份显式报缺，保留全部旧事件；不去T、不映射为当前600018。
5942原清单的正确原生请求范围应为5941，排除依据由真实元数据和窗口共同决定。

本地最终全量4103 passed/7既有skipped/2既有依赖警告，包含未知/窗口内身份不丢弃、
原生退市/主表外代码继续查询、scope SHA和failed终态/Invocation漂移拒绝。修复范围的
真实完整5941行清单经采集UID正式key PUT验证；只在新审计作业写清单，不请求QMT run，
不重放原失败或写Warehouse，具体大小/SHA与结果见#555及节点scope555-put/verified.json。

失败后前进安装还须保留原systemd失败证据：controller v35候选仅对tushare精确接受
loaded/failed/PID0、Result=exit-code、非零ExecMainStatus与32位InvocationID的已结束状态，
timer仍须inactive。终态序列化加入前后身份与record，状态漂移拒绝；不reset-failed、
不停止仍运行的任务、不扩展其他组件。修复后按正常CI/test/PR/精确main发布，再用
新版本新run完成实际unit验证；原失败、请求和5项质量拒绝永久保留。

## 10-28 维护者确认后的发布控制面依赖（#553）

维护者已明确确认PR#552合并、必要gateway控制文件和自动送配/精确事件表写权限启用。
PR#552合并main d56c9ac655abf835e196c3e96597e9d89a626c06，自动对齐/审计及最终SHA
CI40671319925成功。准备正式发布时发现当前controller除reader外要求Live active，
tushare-sync受阻；#553为本数据主线直接依赖，仅准备精确非交易数据组件的停机维护。

新守卫在Live未active时仅对tushare-sync选择：原Live须inactive/dead/disabled/PID0，
可信稳定绑定、主库和非空KILL保持，tushare timer与oneshot均loaded/inactive、服务PID0；
准备前/后、安装前/后复核，保护Paper/Shadow/Web/backtest/observer/reader六服务身份。
Live active时仍走原心跳preflight，其他组件门禁保持。原reader边界保持。

入口参数迁移还须补齐既有失败补偿：tushare更新前用GNU cp --archive留存原drop-in，
失败时恢复代码指针后原子恢复原drop-in，核对字节/uid/gid/mode/mtime/access ACL后再
判定补偿成功；备份永久保留，避免旧代码配新参数。候选专项实际调用validate_record。
该控制器修复须按正常CI/test/PR和合并精确main准备，不手工绕过现有门禁。

Windows已从GitHub取得d56c9ac精确源字节并核对SHA，准备版本化目录与E盘新根，
原专用key入口尚未切换；此处为准备快照，实际执行以#540/#553最新评论为准。
Tushare原timer保持active，19点自然运行若先到则保留原版本执行，待终态再进入维护窗口。

## 10-28 自动增量候选与发布前置

候选最终本地全量4067通过、7既有跳过、2既有依赖警告。生产入口和配置未切换。
Windows首轮真实隔离导出因只读句柄fsync抛OSError/errno9，三样本均记failed，未写生产。
同批临时文件只读/可写句柄探针确认Windows差异后，改为Parquet写句柄flush/fsync，新增
零字节写探针回归。首次失败、requested及临时文件全部保留，没有在原作业重放。

R2新审计目录实测3只证券：001399.SZ与600017.SH共2条真实事件、600000.SH明确empty；
完整bundle校验通过，重复run与daily-archive组件请求均拒绝。两个文件再经真实SSH
probe/list/get传回Mac，实际WindowsDataTransport长度/SHA、客户端覆盖/schema/原值与
隔离Warehouse导入通过。该轮使用维护账号和E盘独立gateway配置；正式组件专用key的
配置切换与负向现场验收仍待发布。QMT PID3788、159个qsys任务文件SHA保持，下载/账户/
交易调用0。Windows审计根`E:/qsys-private-audit/issue540-dividend-protocol-20261028-r2/`，
Linux摘要`/data/qsys/var-deployments/issue540-dividend-auto-20261028/`；首次失败在无-r2目录。

原生产17:00自然同步`170005-2111515`于17:03:22 succeeded，19步骤成功、11表canonical
对账通过。实际采集UID预算内只读查得当日5570行价格因子与Tushare直取精确一致、规范
因子逐键精确一致；获批修复的12只/336行历史样本与修复候选保持，事件仍61085条。
这不代表新自动送配已激活。17:10原只读分钟替代源探针仅1次调用收到40203频次限制拒绝，
响应提及2次但未标出时间窗口；不猜频率、不重试、不判为权限拒绝。19点及晚间任务待自然验收。

历史修复后的下一项仍由#540承接：在`tushare-sync`事实同步后、因子派生前取得QMT完整
送配快照。启用参数、首次起点`2026-10-28`、已批准下载器和gateway凭据必须显式给定。
默认入口不启用新行为。新增事件只追加；同键同值不重写，无论差异大小，旧值冲突均整批
拒绝并要求独立审阅。源失败/None/null/非有限数值、重复、窗口/身份/schema/收据/hash
不符均停止派生和canonical发布；已完成Tushare事实仍执行原增量质检，整次运行保留失败。

固定协议仍为v1，旧minute/auction配置继续有效；仅`tushare-sync`且控制配置包含已批准
`dividend`脚本时开放`dividend_YYYYMMDD_<run-id>`。清单固定`symbol,dt`，每证券仅一行且
dt为窗口结束日，窗口最长31天，实际源码SHA绑定，执行无shell。Windows落盘requested后
拒绝重复run；Linux也先持久化requested，超时/未知送达不重发。逐证券保留done/empty/
incomplete/failed及真实Parquet/SHA，全部完整后汇总`events.parquet`与`result.json`回传，
避免数千次SSH。Linux逐证券核对覆盖、空响应、行数及事件范围，不以exit0替代验收。

成功事件导入窗口记录在组件私有`dividends/last-imported.json`，不从最后事件日期推断
采集进度。下次从上次成功结束日回看7天（不早于首次明确起点），长断流超过31天或回退
日期显式拒绝。该收据只证明事件导入，不证明随后因子/质检成功；窗口外历史更正仍需另行
核验，不宣称滚动窗口能发现所有历史源修订。事件导入后沿用原构建器与canonical对账。

### 数据盘利用与最小权限

维护者确认已扩容Windows数据盘，本轮只读实测E: NTFS 107339558912字节，空闲
106077167616字节；C:空闲4586393600字节，QMT PID3788仍从C盘运行，userdata非链接。
E盘已有私有审计备份，无需再建盘或格式化。新送配快照拟单独使用
`E:/qsys-component-data/tushare-sync-dividends`，通过控制配置`dividend_root`显式指定，
不得与两个旧组件工作根重叠；旧分钟/竞价目录和活动QMT数据不迁移。未生产创建此目录。

Linux仅增加`corporate_actions`这一写表，保持原共享flock、私有组和observers读取边界；
不加入Workbench写组。真实采集UID在隔离`/tmp/qsys-dividend-acl-0fdauflt`创建/原子替换
新inode后，backtest/Paper/Web/checks/digest五UID与ubuntu均回读正确。正式准备仍须对
精确生产目录应用采集账号/ubuntu写及observers读的access/default ACL，并验证实际unit
沙箱、消费者和反向写拒绝；该探针不等于已开放生产权限或完成组件安装。

### 发布清单（尚未执行）

1. 冻结Live/Paper源代码保存在`fix/frozen-ops-recovery`（原PR#547 head f87babd），
   数据候选已将相应5个运行时/测试文件恢复至main；原PR#547关闭且未合并，冻结分支保留。
   正常数据发布不夹带这两项修复。
2. 完成候选全量/源码扫描/可信main与目标planner分类、成功CI与测试部署、逐文件代行
   自审和新的数据发布PR；原PR#547不合并。组件与测试环境的验收分别记录。
3. 从已合并精确main安装版本化Windows gateway和送配下载器：保留两旧脚本SHA与工作根，
   增加精确dividend脚本SHA和上述E盘目录；备份并精确替换两组件的forced-command入口，
   原个人key、密码、任务、RDP/Autologon、QMT/Python路径全部保持。先准备文件/ACL，
   后切入口，实际probe/list/get及非授权组件/重复请求负向验证；不重启sshd或QMT。
4. 新增manifest规格与节点`/etc/qsys/component-releases.json`必须精确比对，审批后仅从
   合并main安装可信规格并备份；准备精确corporate_actions ACL。旧组件不得提前套新参数。
5. 隔离真实Windows读取及Linux新inode/消费者/沙箱验收，再按既有审批门禁发布
   tushare-sync；workflow、Deployment、实际validate_record三方成功后验业务，最后观察
   17/19自然任务。首次激活包含未来事件表写入，须明确批准，不沿用历史661事件批准。

本候选没有生产安装、开放权限或采集新快照，没有恢复模拟/实盘、清KILL或删除备份。
现金源差异、#550/#551和D-23历史约束保留。

## 2026-10-28 16:14 执行与验收结论

维护者本会话明确确认[审阅包6463418320](https://github.com/wanlinruo/quant-trade/issues/540#issuecomment-6463418320)。
已于16:03:44–45持真实写锁执行三表修复，16:07全量验证及16:09五真实消费者读取通过。
以下准备/审阅内容保留为执行前证据，不再等待本次批准；未授权范围仍按原门禁。

- corporate_actions合61085条：60424旧事实和661新增原值均逐值保持，null/重复0。
- adj_factors全18368804行、规范2026全1074139行，各仅164个量因子值变化；价格因子及
  所有其余键、列和原物理顺序保持。不是只用14只样本替代生产全量验收。
- backtest/Paper/Web/checks/digest五真实UID在只读沙箱读取事件表和12只/336行因子样本，
  与规范表精确对账；SSH回执超时后从节点报告确认全部完成，没有重放。
- 事件候选symbol为large_string，生产为string。绑定原批准36d295...输入，按生产schema
  无损重编码并逐值证明61085行相同，最终实际文件SHA为
  `eab786c3a2de0a300c832a3b53749bcd33f6d7356f9b1ab6b47b28dce0b4dee5`，记录在
  schema-normalization.json。两因子候选SHA不变；三表owner/group/mode/ACL均保持。
- 一次性root辅助任务仅用于持锁及保留原属主/ACL，三目录和审计目录精确可写，原归档
  运行时先有界等待其终态。任务成功后已停止自身timer/service，所有备份/失败/审计保留。
- 今日自然归档5570股日/1342297分钟与canonical通过；整体warning因停牌快照8→7保留，
  差异为退市当日301139.SZ停牌行，见[#551](https://github.com/wanlinruo/quant-trade/issues/551)。
  不将它描述为整体归档全绿或分钟失败，不删除原观测以消warning。

历史修复不等于未来自动增量恢复。正式自动送配入口、固定gateway/最小权限、现金跨源
差异独立核验及今晚自然主同步/质检/日报仍未完成；#540继续in-progress。模拟/实盘及
PR#547继续冻结，没有生产组件发布、清KILL、改账户或重放交易。发布前须隔离冻结变更。

本项属于[数据恢复唯一主线 #540](https://github.com/wanlinruo/quant-trade/issues/540)。
维护者顺序为数据→模拟→实盘。本文的只读盘点、隔离重放与代码候选不等于生产写入批准；
此前确认仅覆盖10-26/27分钟、竞价、必要停牌合并及归档timer恢复。

## 已核验的缺口

2026-10-28 15:06起，以当前证券主表5919只，加旧事件表额外23只，共5942只，调用原正式
`get_divid_factors`查询20260910–20261028。82.20秒完成，661只返回各1条事件，5281只
返回空表，None/异常0；没有download/subscribe/account/trading调用。空表只是本轮响应，
不推断不存在任何未知事件。原始响应逐证券保留列名、日期索引和全部8个源字段，零null。

原始JSONL SHA256：`1db0473e3fb541179a0682df8843f2eeeaa5545ad37478232563319d5f9e17e6`。
规范化输入Parquet（含校验用factor）：661行/14786字节，SHA256：
`b7dff8d936d89f8b4fcced4510c1e292408176d443aa8036b770e9ff45d2a701`。
`stock_per_share`只作`stockBonus + stockGift`；不转换单位、不补null、不按价格反推事件。

生产`corporate_actions`60424行，最大ex_date为9-9，和新输入键重叠0，合并后应61085行；
旧表各列null均0。源差异仍逐字段报告，不据历史零值推定旧表已经验真。
缺口起点早于MiniQMT升级，不能全部归因于本轮升级。

## 跨源对照及未决差异

按29个实际除权日调用Tushare `dividend`（只读、29次，无失败或2000行截断），取得674条，
其中667条为“实施”，另7条不属于实施；实施中6条为当前A股清单之外的B股，保留不入本次集合。
661条QMT事件均有唯一的Tushare实施记录，全部送转比例十进制精确相同。

现金字段645条精确相同，8条存在原始小数差异，另8条Tushare为null/QMT原生为0。
不补Tushare空值，不改QMT原值，不增加容差把差异标成相等。

| 股票/日期 | QMT interest | Tushare cash_div_tax |
|---|---:|---:|
| 000099.SZ / 10-23 | 0.064699 | 0.0647 |
| 000403.SZ / 10-23 | 0.150069 | 0.1500687 |
| 000498.SZ / 10-22 | 0.02645 | 0.0264499 |
| 000528.SZ / 10-22 | 0.054221 | 0.0542207 |
| 002056.SZ / 10-12 | 0.229993 | 0.2299934 |
| 002475.SZ / 10-22 | 0.109923 | 0.1099231 |
| 300453.SZ / 09-23 | 0.100238 | 0.1002381 |
| 301232.SZ / 10-21 | 0.099884 | 0.0998841 |

现金null的8只：001225、001285、300803、300908、300980、688300、688808、688809。
本次拟按既有QMT选源优先级保留其真实直取值；8条非空差异仍未完成独立公告核验，
不能宣称全部现金字段跨源一致。它们不改变股本量因子。

字段口径依据：[Tushare官方dividend说明](https://tushare.pro/document/2?doc_id=103)：
`stk_div`与`cash_div_tax`分别为每股送转与税前每股分红；本次仅交叉验真，不切换事件源。

## 量因子实际影响

| 股票 | 除权日 | 新增股本比例 | 截至10-27受影响因子行 |
|---|---|---:|---:|
| 001225.SZ | 09-23 | 1.4 | 19 |
| 001285.SZ | 10-16 | 1.45 | 8 |
| 001309.SZ | 10-13 | 1.4 | 11 |
| 002961.SZ | 09-23 | 1.48 | 19 |
| 300803.SZ | 09-22 | 1.45 | 20 |
| 300908.SZ | 09-14 | 1.45 | 26 |
| 300980.SZ | 09-30 | 1.4 | 15 |
| 688110.SH | 10-12 | 1.49 | 12 |
| 688300.SH | 10-16 | 1.3 | 8 |
| 688610.SH | 10-19 | 1.4 | 7 |
| 688808.SH | 09-29 | 1.48 | 16 |
| 688809.SH | 10-23 | 1.4 | 3 |

合计12只/164行；价格累计因子仍取Tushare，不由QMT dr改写。
`adjustment_factor_daily.share_factor`是`adj_factors.cum_share_factor`的规范副本，
必须同步更新与对账，不能只修旧因子表。数目以写入前最新生产SHA/交易日重新核验，17:00
自然主同步后不得继续使用旧SHA与164行预估作为当前授权清单。

本地用真实14只证券（12只送转+2只现金对照）的完整17247行直取历史及原全部60424条事件，
调用实际`build_adj_factors`精确复现生产旧样本；再用实际`import_dividends`导入661条并重建。
旧事件全部保持、价格因子全部保持，仅12只/164行量因子改变，现金对照股量因子保持。
这只是样本实际算法重放，不冒充生产18368804行全量重建验收。

## 候选代码与持久化

- 导出器要求明确起止日和新的快照目录；日期/非有限值/重复事件拒绝，源缺失保留null，
  送转两字段任一缺失时结果仍为null，不填0。
- 每证券数据原子落盘且fsync后才写带SHA的收据，done/empty/incomplete/failed分开。
  崩溃后无收据的数据会重取；有收据必须核对文件SHA。旧done.csv与变更范围拒绝复用，
  不把已经下载过历史当成未来每天无需更新。每次增量用新快照目录。
- 导入拒绝null/NaN/Inf/文件内重复键，保留旧事实。现有API仍按坏文件计errors，调用方
  必须以errors=0验收，不能只看导入行数或父进程exit0。
- 生产导出器尚未发布，长期自动送配链路仍未恢复。两个现有组件都没有corporate_actions
  写路径，且gateway没有dividends命令，不能临时放宽账号或白名单冒充已接入。

## 生产执行审阅范围（已获本次明确确认并执行，原方案）

只允许在取得同一稳定输入、完整备份、实际锁和已发布模块指纹核验后执行：

1. 保留全部60424条旧事件，按候选SHA新增661条，合61085；不写其他事件、不改旧值。
2. 同步修复`adj_factors.cum_share_factor`及2026年`adjustment_factor_daily.share_factor`；
   不改价格因子、源日线、分钟、竞价、策略阈值、账户或交易状态。
3. 全量逐键验证两表仅批准键的量因子改变，所有其余列/键保持；规范表对账，真实只读
   消费者验证。保留备份、失败和requested记录；未知送达时先核验，不自动重放。
4. 一次性历史修复与未来增量接入分开验收。长期实现应使用独立的最小写权限与固定正式
   gateway协议，按正常分支/CI/test/main/组件发布流程，不夹带PR#547中的Live/Paper修复。

审计根：`/data/qsys/var-deployments/issue540-data-recovery-20261028/dividend-review/`。
Windows原始响应：`C:/qsys/var-deployments/issue540-dividend-inventory-20261028/`。
全量事件修复、自然增量、现金差异独立核验及长期接入未完成前，#540保持in-progress。

## 15:35 全量候选验收

在审计根的`staged-targets-r2/`生成完整目标，通过有界DuckDB流式生成及PyArrow分批逐行比较。
512MiB查询内存、1GiB临时空间上限、1线程、nice/idle IO、180秒外部超时；未写生产表。
首次候选因SQL连接改变物理行顺序被严格验证拒绝，文件保留；R2显式按原文件行号排序，
未放宽比较规则。两张因子表全量逐行证明只有实际构建器给出的164个share值改变，
其余所有列、键和物理顺序保持。完整备份的原文件SHA仍是生产激活前必须复查的条件。

| 目标 | 总行数 | 变化 | 完整候选SHA256 |
|---|---:|---|---|
| corporate_actions | 61085 | 保留60424，新增661 | `36d29584c3bd6537fa2911425ba7e2300a6299f23461a4fe494fdf57a1218194` |
| adj_factors | 18368804 | 仅164行cum_share_factor | `2773ca70f663d16ae9b8f65a666d3b7f564ed82449a3ea735b48e8895b616c78` |
| adjustment_factor_daily / 2026 | 1074139 | 仅164行share_factor | `177557332f731c8011e425b7b6750cc55b96560618440d69d0eb770deb83240a` |

原文件SHA按上述顺序为：
`dcf9cdc0c21399f4ae5dcb7de0e9cde98be64f40e0a64f07eb68860092fbb073`、
`5471b542d5f30a4d53785224344b2db061eb5b562fc289550086c573b0e104f1`、
`61e42450e99514978930f8a959f587551f009bda31cb8862ae97f0b6d7fd6ad4`。
完整报告`staged-verification-r2.json`与`pre-write-backup-verification.json`位于审计根。

候选代码全量4061通过、7既有跳过、2条既有依赖警告，仓库政策与diff检查另记录在#540。
本次确认只批准上述历史数据修复，不包括新组件发布、放宽gateway权限、恢复模拟或实盘。
