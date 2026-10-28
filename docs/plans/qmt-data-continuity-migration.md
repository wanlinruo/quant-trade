# 江海 QMT 升级后的外部数据连续性（#540 / #533-F）

专项：[数据连续性 #540](https://github.com/wanlinruo/quant-trade/issues/540)，
总跟踪：[迁移 #533](https://github.com/wanlinruo/quant-trade/issues/533)。
本文件记录外部仓库及模拟行情的影响；沙箱策略进度见
[内置迁移计划](qmt-inner-strategy-migration.md)。不把沙箱内可读取行情当成外部供数已恢复。

## 2026-10-28 最新裁决与验收

维护者要求先数据、再模拟、最后实盘。#540唯一主动主线，其余Issue排期blocked。
10-26/27分钟与竞价已按明确批准范围通过原正式QMT数据通道补齐：11140股日、2684534根
分钟及116候选股日、13821条竞价新增；完整性、原门禁、canonical及真实只读消费者通过。
归档timer已恢复，14:33 Persistent补跑成功；7日14个分钟分区SHA保持，今日自然增量待验。

原RPC对退市当日301139.SZ的两日历史返回有量零价，被质量门禁拒绝；正式本地接口真实
482根已受控修复，长期缺陷见[#550](https://github.com/wanlinruo/quant-trade/issues/550)。
当前数据接口实测可用，不能继续笼统沿用“外部数据接口全部不可用”；不推导交易限制已解除。

送配增量仍未恢复：原生接口已证实10-27两条事件而仓库止9-9，早于MiniQMT升级。
须继续核对无填充映射、完整事件范围及依赖量因子；本次未扩大送配写权限，未恢复模拟/实盘。
详情与证据以#540及[最新交接](../../HANDOFF.md)为准。以下旧日期记录保留为历史，不代表现状。

## 1. 约束与结论

维护者已向江海确认不支持xtquant，策略的动态输入和指标必须在QMT内部取得/生成。
原外部行情、分钟和竞价导出脚本均依赖xtdata。SSH data gateway的隔离和协议正确性
只能证明传输能力，不能恢复行情上游。人工复制少量诊断输出只用于私有离线验真，
不作为自动归档桥，也不将审计结果回灌策略。

当前保留历史仓库；Tushare原生主采集仍有独立来源，但QMT分钟、竞价、送配增量及
Paper实时行情没有通过新版替代供应验收。10-26晚获明确授权后，仅暂停失效归档 timer，
Tushare timer保留；未购买、切源或修改生产配置。最新事实与恢复条件见
[夜间排障记录](qmt-recovery-operations-20261026.md)，下文10-25记录保留为历史证据。

## 2. 实际依赖与消费者

| 链路 | 入口和依赖 | 消费者及影响 | 当前处置 |
|---|---|---|---|
| Tushare主数据 | tushare_sync_cli → pipeline/tushare_sync | 日线、估值、ST、限价、停牌、主表等原生API仍独立；既有数据/质量约束保持 | 保留采集，不把QMT失败写成Tushare接口失败 |
| 每日分钟 | daily_archive_cli → Windows data gateway → win_download_minutes.py → xtdata | minute_bars、bar_minute_1m及未来分钟研究/回测增量受影响 | 历史保留，未来供应未恢复，不默认切换canonical（D-23仍未归因） |
| 晚到新股分钟 | tushare_sync_cli.converge_late_listing_minutes → daily_archive_cli | 生产Tushare unit显式启用；有缺口时补采失败可使整个任务失败 | 保留失败和缺口；主同步完成后的质检仍应执行 |
| 竞价逐笔 | daily_archive_cli → win_download_auction_ticks.py → xtdata | auction_ticks、早期竞价研究及同口径验真 | 无正式出口不声明可归档；最终竞价不替代早期快照 |
| 实时行情/概览 | run_market_gateway.py → xtdata | Paper、Live、未来Shadow及行情页、限价交叉核验 | active进程不证明行情新鲜；外部行情替代尚未通过 |
| 分红送配 | win_download_dividends.py → xtdata → pipeline/dividends → corporate_actions | 回测现金/持仓动作，以及build_adj_factors的累计股本量因子 | 按需增量通道受影响，不能只关注日线价格因子 |
| 价格复权因子 | Tushare直取factor → build_adj_factors | cum_factor本身独立于QMT | 不把价格因子直取扩大为股本量因子也已独立 |
| 旧QMT股本 | win_download_capital.py → share_capital | 2026-09-16起冻结历史，仅作既有证据 | 保留原表，不将冻结历史作为当前股本/PIT真值 |

需特别区分：`build_adj_factors`的价格因子来自Tushare，`cum_share_factor`仍消费
`corporate_actions`。仅Tushare采集成功不能证明未来送转/配股、成交量复权和回测动作连续。
当前盘点不改历史值，不按价格反推动作，不因事件表最新日期较早就断言之后一定漏事件。

## 3. 2026-10-25 21:51只读现场证据

Linux审计根：`/data/qsys/var-deployments/issue540-20261025-data-continuity-readonly/`。
`inventory-20261025.json` SHA256：
`11f54c38236ec5f50cb4580c01750fbb879044ad5df8207f65bb7cbecb971262`。
脚本仅取Parquet footer、4份脱敏ServiceRun和7个unit状态；nice/ionice/timeout有界执行，
未读取全量年度数据、未调用DuckDB、未写Warehouse或执行下载/交易。

| 数据 | 本次读取范围 | footer行数 | 日期上界 |
|---|---|---:|---|
| tushare_daily | year=2026 | 1,062,999 | 2026-10-23 |
| auction_ticks | year=2026 | 275,270 | 2026-10-23 |
| minute_bars | date=20261023 | 1,342,753 | 2026-10-23 15:00 |
| bar_minute_1m | date=20261023，字段ts | 1,342,753 | 2026-10-23 15:00 |
| corporate_actions | 当前单文件，字段ex_date | 60,424 | 2026-09-09 |
| share_capital | 当前冻结单文件 | 219,427 | 2026-09-16 |

以上是文件元数据范围，**没有重新执行质量检查**。同样行数不证明逐行对账；分钟窗口
首末时刻不证明全证券/网格完整。事件日期上界不是最近下载时间，不能据此推定漏了多少事件。

运行记录分别保留历史失败及恢复：

- 10-23 15:40归档记录failed，分钟步骤失败，已有1,342,512行记录；不能删除此失败。
- 10-23 17:57归档记录succeeded，分钟下载步骤rows=0、canonical发布及分钟校验成功。
  零缺口/零新下载不证明新版Windows下载恢复；它仍是尚未安装新版客户端时的旧通道记录。
- 10-23 17:01 Tushare记录succeeded，晚到新股分钟补采1只；19:00记录succeeded，
  该步骤无新增rows，增量质检步骤成功。它们不证明10-26新供应链已通过。
- 当前实际独立unit的User/Group分别为qsys-daily-archive、qsys-tushare-sync，入口在
  `/opt/qsys/components/`，使用gateway；后者明确启用late-minute-convergence。
- 两个oneshot当前inactive、最近退出0，原timer仍active；未手工重跑/暂停或改编排。
  Live保持inactive/PID0，KILL保留；Paper634053 active/NRestarts1，Web3700671 active/NRestarts0。

## 4. 补采失败不应跳过主数据质检（实现准备）

既有顺序为事实同步 → 派生 → canonical发布 → QMT新股分钟补采 → 增量质检。
当补采抛异常时，后续质检被短路，即使前面的主数据已经写入。

本轮修复在前三步完成后，捕获补采普通异常，执行原有after_sync，再原样抛出补采错误：

- 主数据成功、补采失败、质检成功/警告分别保留在实际ServiceRun，整项任务继续失败。
- 验证器报错沿用现有warning语义，不清除补采失败、不假称缺口已补齐。
- 主同步/派生/canonical未完成时不执行成功后挂钩；人工中断不启动额外质检。
- 依旧按原先单次、少量新股边界调用补采，不增加重试、不改质量阈值/源选择。

此项只改善失败后的可观测性，不恢复QMT数据供应；完成CI/test也不表示实际独立组件已发布。
生产部署与自然交易日观察另行记录，#540完整连续性验收保持未完成。

## 5. 后续验收顺序

1. 核实券商正式允许的数据导出/独立数据服务；无正式能力时明确列出无法持续供应的资产。
2. 按分钟、早期竞价、最终竞价、送配事件和实时行情分别定义权限、字段、单位、时点、
   覆盖及消费者验收。现有付费/聚宽历史和预留Tushare分钟契约不等于未来增量渠道已具备。
3. 有真实新源后先探针及交叉质检，再提出生产接入清单；不采购、不自动切源、不填补缺口。
4. 自然交易日验证数据真正到达、质量结果和Paper行情新鲜度；保存停止日期与真实缺口，
   不以已运行timer、旧成功记录、进程active或沙箱单样本可读替代验收。

沙箱日历/停牌后续验证当前等待可操作的远程桌面：新版进程已变为5916，原生主界面可见，
前台守卫拒绝输入，尚未运行新的日历探针。没有沿用旧窗口句柄、解锁桌面或调整自动恢复。
