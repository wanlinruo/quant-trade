# 参考资料

## 外部策略与大QMT薄执行器（#601，2026-11-05正式采用）

[唯一主线实施方案](qmt-external-execution-mainline.md)记录江海新版停用外部`xtquant`后的最终
架构、三端职责、事实状态机、分层验收顺序及暂停范围。Windows网关不直接调用旧
`XtQuantAdapter`；只有QMT内置薄执行器调用正式交易接口，生产运行不得依赖RDP。

[11-06正式模拟运行与VNC断开证据](qmt-mainline-runtime-probe-20261106.json)记录默认关闭候选的
编辑器回读/编译、自动运行配置、正式模拟完整合成ACK及VNC断开复验；不包含账户标识，且不把
传输ACK冒充券商受理或真实交易授权。

[11-07 Windows整机重启证据](qmt-windows-reboot-probe-20261107.json)记录受信QMT与验证模型在
整机重启后的自动恢复，以及协调器在创建发送门前因Windows安全检查超时而失败的独立事实。
本次证明自动启动，不证明重启后的合成意图闭环；失败记录保留，收尾已停止模型并恢复默认关闭源码。

[原生账户与回报只读探针记录](qmt-native-report-readonly-probe.md)记录第4阶段查询/回调的正式
接口边界、脱敏规则和两级授权。检入候选默认关闭且没有下单/撤单调用；离线通过不授权读取真实
账户，也不把模拟模式或函数存在性当作原生回调证据。

[11-05可行性分析报告](qmt-external-signal-feasibility-report-20261105.md)给出现场证据、异步执行
契约与剩余门禁；维护者已据此采用外部Linux决策→Windows执行网关→大QMT薄执行器，停止扩大
完整内置策略移植。路线采用不是实盘启用证明。

[可行性与分层证据](qmt-external-signal-feasibility.md)记录Windows回环、独立诊断模型、
离线协议和异步受理设计。22:24单次共享内存、23:20编辑器内正式调度API的8条消息组合验证通过；
[持久发送与恢复接线](qmt-external-dispatch-recovery.md)11-05已在Windows私有SQLite与
编辑器接收器完成两阶段诊断；原型当轮未隔离有效期条件，后续应用ACK恢复补验见下方。
正式实盘运行模式、生产身份、完整重启恢复与账户许可仍待验，不恢复交易。

[异步发送隔离接线（#601）](qmt-async-dispatch.md)记录默认关闭的应用契约、持久防重、
Core预留保持和提交后ACK；Windows私有夹具准备/隔离工厂已实测，
尚无正式QMT交易端口或生产执行身份安装验收。

[11-05实际应用接线](qmt-async-qmt-probe-20261105.json)已在QMT编辑器补验ACK后、
有效期内跨接收会话恢复：原意图零重发，新对照ping成功；未重启客户端或接交易接口。

[唯一主线完整合成意图现场证据](qmt-mainline-synthetic-probe-20261105.json)记录17:46的逐字段
验证、明确非执行回执、冷恢复`unknown`零重发和辅助任务收尾。它完成第1阶段，不证明正式策略
运行模式、券商许可、无人值守恢复或真实委托回报。

[#602完整合成指令协议](qmt-synthetic-intent-protocol.py)与
[外部校验端口](qmt-synthetic-intent-port.py)是默认关闭的本地诊断候选：只允许固定测试
OrderCommand，回执明确非执行且不授权交易；现场运行证据已并入上述#601主线摘要。

## 指标验证与健康证据（#562 / #561）

[catalog隔离与正式验收](../plans/catalog-verify-production-isolation.md)记录字段状态传播修复、
10-29人工质检和待自然采证的界限；[证据版本绑定方案](../plans/catalog-evidence-version-binding.md)
记录旧红灯的已证实成因与下一步规则，尚非已实现功能。旧失败和市场事实均保留。

## 跨日运行可靠性修复（#535 / #549）

`live-core-failure-fuse.md`说明连续故障后的本进程锁定、单次撤单未知结果及告警边界。
`paper-business-health.md`说明已生效error账户与进程心跳分别判定，不自动恢复模拟账户。
两项先经隔离/CI/test及正常PR，生产发布单独验收，不解除#533/#541交易门禁。
`windows-static-queue-capacity.md`记录#545静态旧队列的E盘备份及无损压缩验收，保留C原文件。

## 停机态历史状态读取（#544）

见 `../plans/live-state-reader-maintenance.md`：标准库只读辅助连接、独立组件的停机安装
门禁、真实UID/systemd合成验证和正式发布前置。10-27已完成独立组件正式发布及生产激活，
临时服务已退出；自然晚间任务观察仍由#544跟踪，恢复读库不代表停机期间策略执行成功。

## QMT持久化与Python3.12补充资料（#546）

见 `qmt-python312-supplement-review.md`：两份维护者提供PDF的来源、页码、指纹与可信边界，
三个接口的Panel迁移范围、当前py36实际运行证据和先GET后准备运行库切换的建议。
`qmt-runtime-container-probe.py`为首轮原生环境/现有get_market_data_ex容器诊断准备件，
只有一次有界只读历史查询；离线守卫测试不替代客户端执行或数值等价验收。
运行库核验顺序、正式包指纹、实际独立路径和本轮授权/容量门禁见
`../plans/qmt-python312-runtime-verification.md`；安装与实际加载/业务验收分开记录。

## 江海 QMT 内置策略迁移（#533）

见 `../plans/qmt-inner-strategy-migration.md`：当前生产策略基线、新版2.1.33.0官网下载核验、
真值输入/状态恢复/运行控制门禁及无交易诊断步骤。`qmt-inner-readonly-probe.py`仅供新版
编辑器人工诊断，不是生产交易入口；离线通过不代表新版现场或实盘验收完成。
`qmt-inner-input-contract.md`补充输入/恢复验收要求；`qmt-inner-decision-core.py`为本地生成的
纯计算材料，现含LOW/HIGH入场、个股因子、Gate、定量和退出，绑定源文件指纹。
History容器仅处理明确完整的内存数值；#538另增加规范前复权纯算术及已观测原生内存包的
事件/日历/时基映射，保留缺失null、独立股本量及half-to-even舍入。参考调用的可见证券
组数须显式传入，以保持锁定Polars的标量/向量路径。现金/送转/配股三组原生样本已连接；
配股股票日期列表比指数少6日，不能作为独立市场日历证明覆盖。完整单位、事件/停牌覆盖
仍未通过；同输入对照不代表完整选股、状态或交易接管完成。

## QMT内置状态恢复（#541 / #533-B）

见 `../plans/qmt-inner-state-recovery.md`：原生ACCOUNT身份正对照、当前查询空结果的
边界、历史接口NameError与跨运行持久化/lot/FIFO/去重的剩余条件。未验证可靠自动恢复。
`qmt-inner-runtime-guard.py`是独立回调守卫准备件，显式证据/时效、同实例去重及未知副作用
不重播均有离线验证；没有QMT入口、持久化或证据生产者，不作为可激活策略。

## 送配现金独立核验（#558 / #540）

`cash-dividend-announcement-review.md`与同名JSON记录八条公告/Tushare精确一致、QMT原值
不同的事实、八条null/0的公告观测及仅改八现金单元的本地候选。历史覆盖已按确认完成，见
`cash-dividend-production-acceptance.md`的全表/真实身份验收；未来增量现金策略已按10-30明确确认启用Tushare，首次新源自然采证待#558验收；不能用最新质检0阻断替代现金字段独立验真。

## 公开分钟观察与正对照（#583）

[public-minute-prefix-review.md](public-minute-prefix-review.md)记录29例首笔标签一致、
三只正对照09:30/09:31不一致及严格离线重放。不同发布方的标签一致不代表竞价分钟
覆盖等价；当前重放明确requires_review，不接入生产放行。

## 原生零量与质检契约（#583）

[11-03成交快照核验](native-minute-trades-review-20261103.md)记录当天15组原生False/True、
30组分钟片段入库对照、沪深成交观察和688089.SH量差（#586）。北交所成交证据仍不足，
候选仅改善同步警告的规则/字段/案例计数说明，不接入质量放行。

[native-minute-zero-review.md](native-minute-zero-review.md)记录五组False/True及raw/canonical
逐值对照、tick缺证据与保留阻断的诊断修复；同源一致不代表市场真值或已证实填充。

## 本地分钟读取与填充（#550 / #478）

[qmt-local-minute-reads.md](qmt-local-minute-reads.md)与两份10-30实测JSON记录当前SDK
真实容器/时间、单日与区间、fill_data True/False及RPC的逐值差异。候选不按零量形状
删本地False返回值，不回退或猜单位；生产联合发布与自然验收以#550最新记录为准。

## QMT外部数据连续性（#540 / #533-F）

见 `../plans/qmt-data-continuity-migration.md`：分钟/竞价归档、Paper行情、送配事件、
晚到新股补采与Tushare主链的实际依赖；21:51只读元数据与旧任务证据不等于未来供应恢复。
补采失败后继续主数据质检的修复独立记录，生产发布与自然交易日验收仍待完成。

## Redis5只读能力隔离（#488）

见 `../plans/redis-readonly-production-isolation.md`：真实UID白名单GET入口、按账号端口门禁、
四读取组件与无Redis依赖checks的分阶段切换及残余信任边界。

## Live Core最后独立迁移（#497）

见 `../plans/live-core-production-isolation.md`：包内入口默认兼容、预生产传输/文件/通知
守卫、当前消费者与最新状态一致性、受控Live发布例外及后续真实现场验收。

## reboot-drill 独立只读部署（#495）

见 `../plans/reboot-drill-production-isolation.md`：专属Windows只读基线key、严格事实协议、
私有配置/写集合及dry-run与实际重启验收分层。

## Shadow Core 独立休眠部署（#493）

见 `../plans/shadow-core-production-isolation.md`：保留Live模式停用门禁、旧状态/kill/血缘保持、
完整传输命名空间隔离及当前消费者显式新路径阅读，生产启动和切读路径另行验收。

## Paper 独立生产迁移（#490）

见 `../plans/paper-production-isolation.md`：共享Workbench合法写者、私有身份/最小配置、
显式告警路径与日报读取器先行、SQLite一致性隔离副本和自然模拟事件分层验收。

## 盘前涨跌停独立隔离（#487）

见 `../plans/premarket-limits-production-isolation.md`：三表精确写权限、Redis5受限发布入口、
原payload兼容与隔离namespace验证、首次自然盘前任务验收。

## 每日归档生产隔离（#485）

见 `../plans/daily-archive-production-isolation.md`：四张写表、原工作区和续传历史保留、
专属账号/最小配置/Windows限权入口、资源与真实消费者分层验收。

## Tushare 同步生产隔离（#475）

见 `../plans/tushare-sync-production-isolation.md`：写表集合、账号/配置/Windows限权凭据、
原子写与消费者兼容、资源依据及分层验收顺序。

## 数据组件 Windows 限权取数（#476 / #475 / #330）

见 `../plans/windows-data-component-transport.md`：固定操作协议、组件独立key/命名空间、
下载器hash批准、产物完整性和Windows控制面引导/真实验收顺序。

## catalog-verify 账号与权限隔离（#469 / #330）

见 `../plans/catalog-verify-production-isolation.md`：全局记录只读/专属记录写入、三张质检结果表
精确写权限、冻结基线保留、真实 Linux ACL 与消费者验证，以及逐组件推进顺序。

## limit-up-shadow 独立迁移（#463 / #330）

见 `../plans/limit-up-shadow-production-migration.md`：交易时段开发验证、维护窗口内
最小配置/一致性SQLite seed/控制面bootstrap/榜单与Web联合发布，以及自然运行分层验收。

## `catalog-warning-review-20261020.md`

#465：10-20 指标验证的 28 条字段警告、两张黄色规范表及分钟根数样本复核；
源差异保持可见，不把字段结果条数当作新增故障数。存储到期清单见
`../plans/storage-retention-review-20261020.md`。

## `b-share-limit-codes.md`

#460 探针：Tushare `stk_limit` 把沪市 B 股标为 `900xxx.BJ`（`.SH` 查无），日线与
`stock_basic` 不含 B 股；仓库里 B 股只出现在两张涨跌停价表。核对页 41 只缺口即此。

## 实盘净值图窄屏溢出（#449）

2026-10-20 合成页面逐元素定位：390px视口被固定760px宽的净值SVG撑到791px；
320/768px同样可复现，当前样本表格无超宽。overview图表容器限定SVG最大宽度100%、
高度自动，保留viewBox比例和完整数据；不隐藏body溢出，不修改其他SVG生成器。
90日合成曲线在320/390/768/1440px真实Chrome下无整页横向溢出，桌面图保持760px。
实际账户现场验收以#449发布后记录为准，合成截图不能代替生产验收。

## 服务管理页查询延迟（#448）

2026-10-20 以生产 qsys-web 账号、2233f08 制品、最小配置只读分段实测：
验收看板约 2.84–3.48 秒（全服务14天健康历史占2.78–3.38秒），进程状态
约1.15–1.41秒（latest窗口函数扫描历史）；运行记录扫描仅0.013–0.046秒。
原库只读候选查询：两库按既有(service_id, observed_at_utc)索引分别取最新行
约0.012/0.013秒；只查live-core/paper的14天历史约0.103/0.142秒。
修复保留同时间按id倒序、双库合并去重、失败/未知与过期语义；不改schema、
不迁移历史，不跨请求缓存健康状态。共享库代码合入不等同各组件生产升级；
本项仅独立发布Web，其他消费者保持各自已发布制品。生产发布/页面时延见#448最新评论。

## `live-kill-status.md`

#433 实盘三处停机闸只读聚合、状态时效、桌面/移动提示及独立 Web 发布边界。

## `reboot-drill-timer-mode.md`

#444 共享与独立入口的显式只读模式、交易日/休市日分支、真实重启审批及分层验收边界。

## `data-catalog-latency.md`

#442 数据目录约 30 秒延迟的生产分段实测、权益更新使全仓库缓存失效的根因、按表精确缓存与异步统计的读取边界。

## `delist-date-trading.md`

2026-10-19 探针：Tushare `delist_date` 当天及之后都没有日线，因此每日归档的分钟采集清单不再
请求退市日当天的数据。`alive_on` 的闭区间语义和其他消费者不在这次修复范围内。

## `component-acceptance-matrix.md`

#330 的 14 组件分层验收矩阵、证据口径及 #414 storage-report 试点；生产迁移审阅包见
`../plans/storage-report-production-migration.md`；service-checks 最小配置、查询预算和分支验收见
`../plans/service-checks-production-migration.md`。

## `warehouse-snapshots.md`

Issue #285 的 Warehouse 不可变快照协议：snapshot manifest、分区 hash、完整性验证、
硬链接发布约束和原子 `current` 指针。

## `research-runner.md`

Issue #285 的隔离研究作业协议：Web/Runner 文件所有权、代码与快照身份、状态机、取消、
失败恢复、资源声明，以及日志和产物 hash；月度竞价/Gate task 的受限提交、查询预算与生产调度迁移边界。

## `production-data-management-audit-20260921.md`

2026-09-21 对生产 warehouse、质量证据、定时任务、磁盘和备份状态的只读审计，
以及冻结分钟源、canonical 覆盖和整改流水。原审计整改已收口；未完成的灾备、容量、
安全与数据资产治理分别转 GitHub #127/#239/#126/#122 持续跟踪。

## `auction-order-timing-study.md`

集合竞价挂单时点研究：竞价内提前决策、09:25 后挂单与 09:31 下单三条实盘入场路径的
信号一致性、成交率与相对开盘价的滑点，以及回测应扣的入场滑点建议。

## `qmt-range-query-padding.md`

QMT 的 `get_market_data` 按区间取数时，会把当日首笔成交之前的分钟用昨收价填满、
成交量置 0；按单日取则不会。缺陷的发现经过、两批数据的污染程度、为什么「241 根」和
「成交量对日线」两条质检判据对它完全失明，以及剔除规则与验证。

## `paid-minute-d20-attribution.md`

付费分钟线 D-20 的生产归因：年度 OHLC、成交量、成交额差异的分层数量，北交所重大
残差、2025 QMT 重叠区间和 2026 日期覆盖的独立验证，以及保留源事实、不推断修复的处置。

## `factor-d1-d2-independent-truth.md`

复权因子 D-1 / D-2 的 QMT 独立验真：非股改期逐类归因、北交所上市身份边界根因、
永久质量证据、源反馈样本，以及修复对市场因子和冻结 Gate 阈值的生产量化。

## `etf-intraday-study.md`

T+0 ETF 日内策略探查：六次独立测试（均值回归、动量、同质套利、跳空回补、外盘时段切分、
网格）全部不成立，可提取信号稳定在成本的四分之一。含 QMT 分钟数据的接入口径、T+0 资格的
交易所条文、以及两个把结论带偏过的错误——方向无关的跳空测试与整数溢出。

## `gate-study.md`

生态闸门（S6 分类 + S7 Gate）研究：开闸门与关闸门的完整回测对照、逐笔反事实
（被拒的交易如果买了会怎样）、按分支与拒绝原因的分解，以及研究期间修掉的两个缺陷。

## `structural-gate-attribution.md`

结构策略从首板候选池、低/高开入档到 S6/S7 门禁的逐标的漏斗留痕，
以及 LOW_MAIN 市场×个股、HIGH_CHINEXT 活跃度×广度组合的反事实归因。

## `live-readiness.md`

实盘上线检查清单：按上线手册逐项登记外部事项、技术准备、小资金实盘与扩容的完成状态、
日期与证据，以及费率等配置变更记录。

## `storage-retention.md`

生产节点数据盘的保留策略：回滚副本、暂存区、测试快照与临时工作区的保留规则，
占用与可释放空间的口径，容量告警阈值与删除审批流程。

## `strategy-api.md`

qsys 的策略 API 参考：一个策略 = 一个 `.py` 文件。生命周期钩子、调度声明、
`ctx` 上的全部接口、落盘格式，以及几条容易踩的坑。

可跑的示例在 `strategies/`：`ma_cross.py`（从零写的单文件策略）、
`structural_v1_2.py`（聚宽移植版的装载入口）。

## `joinquant-alignment.md`

与聚宽逐笔对齐的口径清单：已经证明一致的、我们改过的、剩余的真差异。

---

以下是**外部来源、不参与构建**的材料，供移植与对照使用。

## `joinquant-structural-v1.2.py`

用户在聚宽平台上跑了很久的实盘策略 `Structural Strategy V1.2 — FROZEN FINAL`
原文，2,651 行。**不要修改它** —— 它是移植的基准和交叉验证的对照组。

它不能在本项目里运行（依赖聚宽的 `jqdata` 运行时）。qsys 侧的移植版另放
`src/qsys/strategy/` 下。

交叉验证的方式见 spec §8.2：在同一区间比对**「哪天买了哪些票」**。
选股是离散的，错一只就看得出来，比对净值严格得多。

## 移植所需数据的就绪状态（2026-09-09）

| 策略需求 | 状态 |
|---|---|
| 候选池与首板/炸板判定 | ✅ `daily_flags` 894 天全市场 |
| `check_low_open` 60 日形态 | ✅ 日线，5,182 只有 ≥101 根 |
| `check_left_pressure` 101 日 | ✅ |
| `check_high_open` 市值过滤 | ✅ `valuation_snapshot` **100% 覆盖** |
| S6 Gate 的 `index_ma20_slope_5d` | ✅ 894/894 天非空 |
| S6 Gate 的其余生态因子 | ✅ `market_regime` |
| 竞价价 | ✅ 日线 `open` 即竞价成交价（实测相等） |
| 竞价量（仅 LOW_MAIN 需要） | ⚠️ 分钟线只覆盖最近 242 / 894 天 |
| `filter_new_stock` 上市 >50 天 | ✅ 用日线首个 bar 日期替代 |
| `filter_st_paused_stock` | ⚠️ 无历史 ST；但按 10% 判涨停使 ST 股本就进不了候选池，方向等效 |

**已知的语义分歧**（交叉验证时必须单独归因，不可误判为移植 bug）：

原策略 `get_first_auction_record` 取 `get_call_auction(09:15~09:26)` 的**第一条**
记录（作者明确注释「不按 time 重排，也不改成最后一条」），那是 09:15 的竞价
快照；我们只有 09:25 的**已成交价**。这条无法用现有数据消除。

**V1.2 已整体移除 WEAK 家族**（`_v12_keep_non_weak`），故只需移植
HIGH 与 LOW 两个分支。

- [前导零成交量一致性契约与11-03只读实测](minute-zero-consistency.md)（#583，直取参考与反例门禁）

## 行情覆盖与订单市场准入（#588 / #589）

[消费输入合同](market-input-consumer-contract.md)说明沪深京观察范围、报价覆盖与离线证据核对；
[订单市场准入](order-market-access.md)说明禁止北交所新开仓与历史受理事实保留。
代码集成、生产安装及实际消费者接线分别验收，不以离线核对授权交易。
