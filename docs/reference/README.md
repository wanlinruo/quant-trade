# 参考资料

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
