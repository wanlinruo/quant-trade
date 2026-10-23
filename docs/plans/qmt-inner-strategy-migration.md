# 江海 QMT 内置策略迁移准备（#533）

专项：[GitHub #533](https://github.com/wanlinruo/quant-trade/issues/533)。
本文件覆盖第一阶段：生产基线、能力验证、数据/状态门禁和切换准备。
**当前没有完成新版现场验证，也没有授权或执行生产切换。**

## 1. 已确认事实与本轮范围

- 维护者 2026-10-23 向券商确认：江海不再支持 xtquant，需使用内置策略。
- 维护者提供[官方下载页](https://www.jhzq.com.cn/main/home/software/index.shtml)，并确认
  客户经理告知该页已上新版；当前尚未安装，明确先完成迁移准备。
- 下载页使用动态列表，静态 HTML 未列出 QMT，不能据此判断“新版未上架”。页面自身
  `softwareutil.js` → `wsService.querySoftwarePageList` → `/servlet/json`，查询 `funcNo=741004`、
  `software_type=1`；须使用页面正常的 AJAX 请求头，非 AJAX 请求返回“非法请求”。
- 2026-10-23 本地读取官网下载接口：software_id=78，名称“迅投QMT极速策略交易系统”，
  版本 **2.1.33.0**，更新日期 **2026-10-23**，标示大小 216.67M，MD5
  `93b9a64d6913a47f3bb34bb7383701a8`。官方链接 HEAD=200、Content-Length=227198028，
  Last-Modified 为 2026-10-23 08:41:15 GMT。
- 安装文件名为 `XtItClient_x64_江海证券QMT实盘_实盘_2.1.33.0_20261019174441.exe`，
  位于官网 `/plat_files/upload/source_upload/20261023/` 下。文件名构建时间不等于官网上架日期。
- 20:16完整下载到本机临时目录，227198028字节，实测MD5与官网一致，SHA-256为
  `6bfa4fad0cba90541aab2fc540688bf288a64428da8a7fff0adb1fe43f62f6d1`。
  首次150秒总超时后保留部分文件，仅一次校验精确Content-Range的HTTP206续传成功，
  随后对完整文件重新计算两种指纹；未运行安装包。安装包和本地核验JSON不加入Git。
- 不在旧客户端执行探针冒充新版验证；不修改登录、恢复配置、Windows计划任务或交易开关。

## 2. 当前生产基线（只读采样，切换前须刷新）

2026-10-23 20:00（Asia/Shanghai）从实际运行的 Linux Live 进程读取：

| 项目 | 观察值与边界 |
|---|---|
| Live current | `34c7b7b2937d9716d4972e5504726c00c3e2555a` |
| 进程 | PID 3931099，active，NRestarts=0 |
| 策略快照 | `/home/ubuntu/qsys/var/live-core/var/live/strategy.py` |
| 快照 SHA-256 | `88fc87ce11a4b1f39ce1a0a71925ffda4243c8780f324649d004f962f97c18a1` |
| 模块依赖 | 快照导入 StructuralSelection/StructuralStrategy；快照 hash 不是所有依赖模块的 hash |
| 状态库 | `/home/ubuntu/qsys/var/live-core/var/live-state.sqlite3`，仅 `mode=ro`/query_only 查询 |
| 参数来源 | 配置承袭的模拟账户 config_json；不是 deploy.toml 中空的 params |
| 参数 | auction_at=`09:25:10`，require_auction_volume=true，entry_price_buffer=0.03，sizing_price_buffer=0.005 |
| 参数指纹 | 对完整 params 做 `json.dumps(sort_keys=True)` 后 UTF-8 SHA-256：`a1730db59d23a6bc219d2dc0f1a44ee2a509cb828908d71a3ed48cefad84a05f` |
| 资金及姿态 | enabled_capital=10000，mode=live，orders_enabled=true；未修改 |
| 本地事实 | 非零持仓行0，live_orders无行；**不替代券商端活动委托/持仓核对** |
| 今日事件 | 09:15、09:25:10、11:25、14:25、14:50、15:00、15:30共7项completed；不据此认定新增成交或费用验收 |

最终迁移基线还须绑定当前 release 的 selection/strategy/entry/gate/allocation/exits/params
及 feed/market_feed 等实际依赖指纹、手续费/风控有效值和成交事实。不能只复制包装脚本，
也不能拿原聚宽研究版的09:24/09:26或回测bar标签替换现行09:25:10调度。

## 3. 先验证三个门禁

### 数据输入门禁

| 输入 | 当前依据 | 新运行环境需要解决的问题 |
|---|---|---|
| 历史OHLCV/成交额 | Tushare日线与可见复权因子 | QMT直取原值、时间索引、单位、101根窗口、复权锚点分别验证；不可直接宣称同源等价 |
| 首板候选 | daily_flags/limit_list事实及确定性派生 | 首板、连续涨停、ST首板纳入规则必须一致；不能只凭涨幅筛选 |
| ST | Tushare stock_st | QMT ST接口即使可调用也不是已获准的新真值源 |
| 涨跌停价/停牌 | stk_limit/suspend_d | QMT价格字段可做核对，不自动替代既定输入 |
| 退市整理期 | namechange.change_reason | 不从名称含“退”推断；候选日期与公告可见性须保持 |
| 上市日/估值 | stock_basic、daily_basic | 不能用首次行情日期猜上市日期，不能用当前股本反推历史估值 |
| LOW_MAIN生态 | index_ma20_slope_5d、prior_limitup_nextday_return_mean | T-1、完整全A范围、滚动历史必须一致 |
| HIGH_MAIN生态 | market_median_return_1d | 统计范围、缺失值处理及T-1时点一致 |
| HIGH_CHINEXT生态 | touched_limit_up_count、market_advance_ratio | 全A含ST/科创/北交所；交易候选池排除规则不能反向裁剪市场因子 |
| 个股因子 | stock_return_10d、distance_to_60d_high | 相同历史窗口、复权与可见性 |
| 竞价/盘中报价 | 当前runtime实时输入 | 09:25:10能否看到最终撮合值；不得用未来bar或最终竞价值替代早期快照 |

既有 Gate 对部分真正未知输入有显式 UNKNOWN_ALLOW 等语义，移植须逐项对照。
但“输入通道整体未接通”属于启动前完整性门禁失败，不能把整套因子都填None后利用该语义
继续下单；也不能借迁移修改冻结阈值或扩大交易池。

只有券商明确允许，才研究通过正式模型参数人工录入真实盘前快照。届时必须包含目标交易日、
数据可见时点、完整性、源字段及hash，拒绝过期、混日和不完整数据。代码嵌入动态数据同样
要先确认政策，不以“没有open/HTTP”推定允许。此路线为半自动，不能称为无人值守迁移完成。

### 状态恢复门禁

原策略以 tags 区分HIGH/LOW，以lots记录真实入场价、数量、取得日和分支，并按FIFO核销。
这些状态决定T+1卖出资格与退出规则；券商聚合持仓和平均成本不足以证明可恢复。

需要实测并记录：

1. 正式内置状态机制是否存在，策略停止/重新启动、客户端退出后是否保留；
   ContextInfo逐bar保存和模块全局变量不能默认等于跨进程持久化。
2. 委托备注/userOrderId实际长度、跨日保留、历史成交回溯范围和成交唯一标识。
3. 由历史回报重建分支与lots是否无歧义，是否能隔离人工成交/其他策略；交易费用与资金冻结一致。
4. 委托已受理但本地尚未记账时崩溃，重启后如何判定已发、未发或结果未知；不能盲目补发。
5. 部分成交、重复回调、拒单、撤单失败、历史查询不完整与无报价分别处理。

无法证明时的候选方案是暂停自动续跑并人工核对；它不是等价的全自动恢复。
禁止通过重建空账本、猜分支或删除历史事实让策略启动。

### 运行控制门禁

验证新版实际Python版本/库白名单、策略交易运行入口、09:25:10及三个检查点调度、断线
缓存新鲜度、重复实例防护、停止策略后活动委托处置和客户端内急停。外部Redis或kill文件
不能继续被视为内置策略的有效急停；Web无法观察时必须显示不可观测，不能显示healthy。

## 4. 首个探针的交付与现场步骤

诊断源码：[qmt-inner-readonly-probe.py](../reference/qmt-inner-readonly-probe.py)。
该文件是人工复制到客户端的诊断材料，不是生产入口，不安装到节点，也不是交易策略。
使用Python3.6语法，仅导入sys和datetime，不导入qsys或第三方库。

本机先计算源码SHA-256并留在本地审计记录；填写参数后重新计算所运行版本的hash。
客户端内复制代码后的完整性须人工复核，不能只凭模型名称认定版本一致。

新版获准安装后：

1. 记录客户端显示版本、运行日期和运行模式。新建独立诊断模型，使用编辑器调试入口，
   不启用模型自动运行，不替换现有策略，不填真实账户或导入生产配置。
2. 填写 CLIENT_BUILD、PROBE_DATE=当日YYYYMMDD、HISTORY_END=明确已收盘的交易日。
   日期默认留空，缺失或当日/未来历史截止值一律拒绝。最多两个公开证券代码。
3. 对有历史图表的公开证券运行一次。init只报告环境与函数存在性；handlebar仅在最后bar
   读取一次tick、101根本地日线和2根本地分钟数据。没有回调时记录未执行，不能判通过。
4. 历史读取固定不复权、fill_data=False、subscribe=False和明确截止时刻。
   不主动下载或订阅；空本地缓存、旧签名不支持参数时如实报告，不改默认值重试。
5. 人工复制诊断输出到本地审计记录；仅含公开市场样本，不包含账户号、委托或完整配置。
   不把客户端日志设计成自动导出信号桥，不用文件/网络/共享内存绕过限制。
6. 结束诊断模型，确认其未设自动运行。每条记录标明是在新版实测还是离线夹具生成。

输出中的presence_only仅代表名称存在，sample_only仅代表取得了待核验样本；末尾始终为
incomplete。Python版本、行情样本也不能证明账号查询、单位、时效、定时精度或重启恢复。
探针不查询账户，不下单、不撤单、不注册交易回调，不注册timer，不验证任意文件/网络禁止行为。
账号/历史成交等后续探针单独设计，并限制敏感信息输出。

## 5. 数据连续性与切换准备

- Tushare主链可继续，但其晚到新股分钟补采、QMT限价核对以及每日分钟/竞价归档分别记录风险。
  不把内置策略取得行情等同于Linux还能归档；必须另有允许的数据出口或新的真实直取源。
- Paper使用的实时行情也来自现有xtdata网关；回测历史仍在，但模拟盘未来实时连续性不可假定。
- 升级/切换前重新核对当前券商持仓、活动委托、资金冻结、旧代理与watchdog/计划任务的
  自动恢复行为，提出精确停止清单，避免新旧两个策略同时管理账户。
- 尚未通过三道门禁时，周一不得把未验收新策略视为可接管；应急人工计划与生产开关调整
  单独交维护者决策。现有授权不因本文件扩大，本轮未执行任何停止或激活。
- 保留原库、历史数据、原制品及失败记录；不尝试旧版登录绕过、GUI自动点击下单、共享内存
  或进程注入。代码迁移、诊断通过、仿真和真实券商闭环分别留证。

## 6. 下一步与可行性裁决

第二阶段本地准备新增：

- [生产依赖指纹清单](../reference/qmt-inner-production-baseline.json)：21:04只读采样的13个
  关键模块、筛选后的有效参数/风控/费用；当时全部源码hash与开发树一致。
- [计算输入与恢复契约](../reference/qmt-inner-input-contract.md)：明确准备金与实际费用、
  定量/保护价缓冲及runtime预算的差别，列出数据对象、缺失语义和12项待执行恢复场景。
- [独立计算核心](../reference/qmt-inner-decision-core.py) 与本地生成器：从既有AST提取
  冻结S6/S7、仓位定量与退出函数，保留原表达式并绑定原文件hash，无交易生命周期入口。
  同输入差分覆盖三值逻辑、阈值边界、缺失、实际0.03/0.005缓冲、涨停封顶和退出优先顺序。
  候选池/入场/数据/订单/状态/风控仍未移植；纯计算一致不等于策略或新版现场通过。

当前可交付：诊断源码、离线边界测试、官网下载对象指纹、生产基线和验证矩阵。
当前缺少：新版现场能力、正式输入路径、状态恢复与客户端内急停证据。

下一步先在新版执行第4节，再逐字段/逐恢复场景裁决。门禁通过后才实现交易版，按同一输入
对照候选→分支→S6/S7→数量/价格→退出，最后才是经维护者明确确认的真实委托验收。
若必要真值无法合法进入，则明确报告当前江海环境无法等价全自动迁移，同时评估正式替代平台；
不以取消过滤器代替迁移。

## 官方接口依据

- [行情函数](https://dict.thinktrader.net/innerApi/data_function.html)：get_market_data_ex
  的fill_data/subscribe、字段与返回结构；init读取历史可能只见本地数据。
- [变量约定](https://dict.thinktrader.net/innerApi/variable_convention.html)：不同运行模式与ContextInfo。
- [交易函数](https://dict.thinktrader.net/innerApi/trading_function.html)：委托/撤单、查询与历史查询。
- [常见问题](https://dict.thinktrader.net/innerApi/question_answer.html)：Python库白名单、
  ContextInfo语义、快速交易与客户端缓存。通用文档不能替代江海2.1.33.0实测。
