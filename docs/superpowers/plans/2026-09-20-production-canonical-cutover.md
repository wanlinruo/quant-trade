# 生产环境 canonical 全面切换实施方案

- 创建日期：2026-09-20
- 状态：阶段 A–E 代码完成并通过全量测试，待测试环境部署与生产回填/切换
- 工作分支：`feat/production-canonical-cutover`
- 开发基线：`origin/test @ ba9e470318b33c0e7132db4fd6e2d0854d91dd72`
- 当前生产版本：`main @ 0cfd36ec5be0a5adf034e46a9f56b296ebbbe0a4`
- 生产代码回滚点：`3375584f69d254210d5b67a4fe23d09cc7330960`
- 设计依据：`docs/superpowers/specs/2026-09-20-data-asset-architecture-design.md`
- 前置计划：`docs/superpowers/plans/2026-09-20-data-asset-architecture.md`

---

## 一、决策与目标

维护者已明确授权在生产环境完成 canonical 全面切换，并接受在生产验证中发现问题后立即修复。
当前没有启用实盘核心，因此本轮的直接业务风险主要是数据缺失、研究/回测中断、模拟盘或影子盘
输入异常，而不是实际资金委托。

本任务的目标不是只把环境变量改成 `canonical`，而是同时完成以下闭环：

1. 规范表覆盖现有消费者实际可查询的完整历史范围；
2. 盘前、盘后和分钟归档任务能持续增量发布规范表；
3. 表级和关系级健康证据能够自动生成；
4. 研究、回测、Web、paper、shadow 和未来启动的 live core 都显式使用 canonical；
5. 切换后执行新旧对账、策略输入摘要、性能和服务验收；
6. legacy 数据暂不删除，保留明确、可演练的一键回滚路径。

本任务不包含：

- 开启真实下单；
- 删除 legacy 表或历史备份；
- 用启发式填补未知数据；
- 为通过门禁而降低质量阈值。

## 二、当前生产事实基线

### 2.1 代码与服务

- 生产仓库已部署 `0cfd36e`，工作区干净；PR 为 `#31`。
- `qsys-monitor`、`qsys-paper`、`qsys-shadow-core` active。
- `qsys-live-core` inactive，必须在本任务结束时仍保持 inactive。
- `/healthz` 返回 `{"status":"ok"}`。
- `QSYS_MARKET_DATA_FACADE` 当前未设置，因此所有已重启消费者仍使用 legacy 默认值。
- 生产节点全量测试通过。

### 2.2 已生成的 canonical 数据

生产首次验证范围为 2026-09-14 至 2026-09-18：

- 日线新旧各 27,754 行，零键差异、零字段差异；
- 分钟线新旧各 6,688,714 行，零键差异、零字段差异；
- 策略输入共比较 6,855,766 项，双侧 SHA-256 完全一致；
- canonical 日线查询约 40.3ms，legacy 约 67.0ms；
- canonical 分钟查询约 1610.6ms，legacy 约 1633.0ms；
- 12 张必需表中 11 张为 green，`security_name_history` 因 7 个重叠和 246 个断档为 yellow；
- 该五日范围的 `audit_cutover.py` 八项门禁均通过，报告 `ready=true`。

证据位于生产节点：

```text
/home/ubuntu/qsys/var/cutover/0cfd36e/
```

### 2.3 当前覆盖不足

不能把五日 `ready=true` 解释成全历史已经可切。当前物理覆盖为：

- `security_master`、`security_name_history`：已完整从现有来源构建；
- `trading_calendar`：仅 2026-09-14 至 2026-09-18；
- `bar_daily`、`valuation_daily`、`adjustment_factor_daily`：2026-04-17 至 2026-09-18；
- `share_capital_history`：现有来源全量；
- `price_limit_daily`、`security_status_daily`、`market_flags_daily`、
  `market_regime_daily`：主要覆盖 2026-04-17 至 2026-09-18；
- `bar_minute_1m`：2026-09-14 至 2026-09-18；
- `dataset_checks`：已有本次五维健康证据；
- `relation_checks`：尚未生成，当前甚至没有物理目录。

现有来源覆盖：

- `tushare_daily`：1990-12-19 至 2026-09-18；
- `price_limits_daily` / `stock_st_daily`：2022-08-04 至 2026-09-18；
- `daily_flags` / `market_regime`：2023-01-03 至 2026-09-18；
- 分钟来源日期并集：2025-06-12 至 2026-09-18，共 313 个有数据日期；
- 当前 `/data` 剩余约 44 GiB，执行全量分钟回填前必须再次估算目标体积。

### 2.4 已知问题

1. `market_regime_daily.index_ma20_slope_5d` 曾出现 `2.22e-16` 的纯浮点尾差；策略摘要按
   12 位小数归一后完全一致。关系/重算规则应使用明确容差，不能使用不受控的精确浮点相等。
2. 休市期间 market 心跳每 30 秒一次，而 `live_preflight.py` 固定要求 10 秒内新鲜；重启
   shadow 时可能先失败数次再自动恢复。应让 preflight 按心跳声明的期望间隔判断。
3. `qsys-service-checks.service` 有既有失败记录，切换前需要区分真实缺数、历史记录和测试数据。
4. 当前健康生成器只写 `dataset_checks`，没有生产关系检查生成器。
5. 当前日常任务只维护 legacy 表。若现在直接切换，下一交易日 canonical 会停止更新。

## 三、目标数据流

```text
盘前 Tushare stk_limit
  -> legacy price_limits_daily
  -> canonical price_limit_daily
  -> Redis 发布

盘后 Tushare 同步
  -> legacy 来源/派生表
  -> canonical reference + daily + state
  -> dataset_checks + relation_checks
  -> service-checks

QMT/付费分钟归档
  -> legacy/raw minute sources
  -> canonical bar_minute_1m（日分区）
  -> 分钟/日线聚合对账

canonical consumers
  -> Web / research / backtest / paper / shadow
  -> live core（仅配置，不启动）
```

任何 canonical 发布失败都必须让对应服务失败并留下运行记录。切换后禁止静默读取 legacy，
但允许运维人员通过显式回滚开关整体切回 legacy。

## 四、实施阶段

### 阶段 A：建立统一 canonical 发布协调器

新增 `qsys.data.pipeline.canonical_publish`，提供三个幂等入口：

```python
publish_premarket(warehouse, day)
publish_postclose(warehouse, provider, day, next_session)
publish_minutes(warehouse, start, end)
```

要求：

- 直接调用 Python pipeline，不从 Python 中再 shell 调用 CLI；
- 在写任何目标前完成全部来源 preflight；
- 只写 canonical 新目标，不修改 legacy 来源；
- 每个分区临时写入、校验后原子替换；
- 返回结构化计数、耗时和对账结果，供 `ServiceRun` 记录；
- 同日重跑幂等；
- partial failure 可定位到数据集和日期；
- 同一分区禁止两个发布者并发写入。

接入点：

1. `scripts/tushare_premarket_limits.py`
   - legacy 涨跌停价完整且 Redis 发布成功后，发布当日 `price_limit_daily`；
   - canonical 发布失败时任务失败，paper/shadow 对缺价继续执行买单拒绝安全策略。
2. `scripts/tushare_daily_sync.py`
   - legacy 同步和派生完成后，发布 reference、daily、state；
   - 生成表级、关系级健康证据；
   - canonical 失败不得被降级为 warning。
3. `scripts/daily_archive.py`
   - 分钟导入和质量检查完成后，发布涉及日期的 `bar_minute_1m`；
   - “来源已齐、无需下载”的提前返回路径也必须执行 canonical 发布；
   - `--only-auction` 不触发分钟发布。

### 阶段 B：可恢复的全历史回填器

新增 `scripts/backfill_canonical.py`，禁止直接用一次性全表加载完成所有年份。

功能要求：

- `--datasets` 选择数据族；
- `--start`、`--end` 限定范围；
- 日线按年、分钟按日处理；
- `--resume` 根据 checkpoint 跳过已验证分区；
- 每个分区写 manifest：来源、行数、键摘要、字段摘要、文件 hash、代码 SHA、耗时；
- 磁盘剩余空间低于配置阈值时在写入前失败；
- 已存在分区先核对 manifest；内容一致直接跳过，不一致才重新构建；
- 中断后不留下被查询层识别的半成品；
- 最终输出覆盖、对账和失败分区清单。

计划回填范围：

| 数据族 | 起点 | 终点 | 分块 |
|---|---:|---:|---|
| reference/calendar | 1990-12-19 | 至少未来 366 天 | 年 |
| daily/valuation/factor | 1990-12-19 | 最新已收盘日 | 年 |
| share capital | 来源全量 | 来源全量 | 全表事件 |
| price/status | 2022-08-04 | 最新已收盘日 | 年 |
| flags/regime | 2023-01-03 | 最新已收盘日 | 年 |
| minute | 2025-06-12 | 最新有来源日 | 日 |

状态与派生回填必须预先包含回看窗口。验收区间的第一天不能同时作为计算历史的第一天，避免
`prev_close`、首板和市场生态出现边界假差异。

### 阶段 C：关系健康证据

为注册表中的必需关系实现生产生成器，并写入 `relation_checks`：

- 日线到估值、涨跌停、状态、flags 的 `(symbol, dt)` 一对一关系；
- flags 到 regime 的交易日聚合关系；
- 分钟到日线的 `(symbol, trading_day)` 多对一关系；
- 基础资料到事实表的 symbol 外键；
- 上游发布时间与下游刷新时间；
- 匹配率、孤儿行、重复键、基数和日期同步。

门禁要求：所有 required 关系必须有未过期证据，且无 red/gray。不能因为表级健康为绿而忽略
关系证据缺失。

### 阶段 D：修复两个运行期问题

1. 浮点对账：
   - 为有计算含义的浮点字段登记绝对/相对容差；
   - 键、枚举、布尔、字符串仍严格相等；
   - 报告最大绝对差、最大相对差和超阈值行数；
   - 禁止用全表统一 round 掩盖真实差异。
2. 休市心跳 preflight：
   - 解析 `Heartbeat.expected_interval_s`；
   - 容许值与健康观察器使用同一策略和余量；
   - 交易时段仍保持严格阈值；
   - 增加休市、盘前、盘中、午休和未来时间戳测试。

### 阶段 E：全消费者切换

生产建立统一环境文件，例如：

```text
/etc/qsys/market-data.env
QSYS_MARKET_DATA_FACADE=canonical
```

所有消费者 unit 通过 `EnvironmentFile=` 使用同一个开关：

- `qsys-monitor.service`（包含 Web 发起的回测 worker）；
- `qsys-paper.service`；
- `qsys-shadow-core.service`；
- `qsys-live-core.service`（配置 canonical，但继续 inactive）。

CLI 入口增加显式 `--market-data-facade legacy|canonical`，默认读取相同环境变量。切换后逐项确认
进程 `/proc/<pid>/environ` 中确实为 canonical，不能只看配置文件。

切换顺序：

1. Web / research / backtest；
2. paper；
3. shadow；
4. 为 inactive 的 live core 写入 canonical 配置但不启动；
5. 运行全套 smoke、A/B、健康和服务检查；
6. 观察一个完整交易日后，将应用代码默认值改为 canonical；
7. legacy 回滚配置继续保留。

## 五、验证矩阵

### 5.1 自动测试

- 发布协调器：成功、来源缺失、部分失败、重跑、并发和原子性；
- 三个生产入口脚本的 hook 顺序与失败语义；
- 回填 checkpoint、resume、磁盘门禁、manifest 和中断恢复；
- 关系基数、孤儿、重复键、刷新滞后；
- 浮点容差边界；
- 休市/交易时段 heartbeat preflight；
- legacy/canonical 禁止静默回退；
- 全量 `uv run pytest -q`、`git diff --check` 和入口脚本导入测试。

### 5.2 数据验收

- 每张 canonical 表的覆盖上下界与来源一致；
- 每分区行数、主键集合、null 分布和字段摘要一致；
- 日线、分钟、复权、估值、状态、flags、regime 分别对账；
- 分钟聚合到日线量价；
- 全历史随机抽样与边界日期专项抽样；
- `dataset_checks` 和 `relation_checks` required 项无 red/gray；
- 策略输入摘要一致；
- 代表性回测业务产物逐字节一致。

### 5.3 服务验收

- `/healthz`、主要 Web 页面和回测创建/读取；
- paper/shadow 心跳与策略事件；
- 候选股票、涨跌停价缓存、风控拒单和订单顺序；
- 定时任务下一次触发后 canonical 日期自动推进；
- `service-checks` 无未解释失败；
- live core 仍 inactive、真实下单仍关闭。

## 六、切换门禁

全面切换必须同时满足：

1. 全历史回填器报告无失败分区；
2. 所有生产消费者需要的查询范围均有 canonical 数据；
3. 下一次盘前和盘后增量演练成功；
4. 表级和 required 关系级健康无 red/gray；
5. 新旧逐键逐字段对账通过；
6. 策略输入和代表性回测通过；
7. 性能与峰值内存不劣于约定阈值；
8. 生产 SHA 固定、工作区干净；
9. rollback 命令已演练；
10. live core 保持 inactive。

维护者已接受在生产发现问题后修复，但这不意味着跳过失败关闭：门禁失败时可以继续修数据或代码，
不能把失败结果改写成成功后强行切换。

## 七、回滚方案

回滚只改变读取路由，不删除 canonical 数据，也不反向覆盖 legacy：

1. 把统一环境文件改为 `QSYS_MARKET_DATA_FACADE=legacy`；
2. 重启 monitor、paper、shadow；
3. 保持 live core inactive；
4. 核对三个进程的实际环境变量；
5. 检查 `/healthz`、paper/shadow 状态和最近日志；
6. 记录回滚原因、时间、代码 SHA 和数据范围。

若仅 canonical 增量任务失败而消费者尚未切换，不需要回滚代码；修复并重跑目标分区即可。若代码部署
失败，生产代码回滚点为 `3375584f...`，但数据回滚必须另行决定，禁止用 Git 操作猜测数据状态。

## 八、建议提交拆分

1. `docs(data): plan production canonical cutover`
2. `feat(data): add resumable canonical backfill`
3. `feat(data): publish canonical data from scheduled jobs`
4. `feat(data): generate relation health evidence`
5. `fix(live): align preflight with heartbeat cadence`
6. `fix(data): add typed reconciliation tolerances`
7. `test(data): cover production canonical cutover`
8. `ops(data): add shared facade environment configuration`

每个提交必须独立通过相关测试，不把数据回填产物提交到 Git。

## 九、当前下一步

1. 先实现阶段 A 和 B，并在临时仓库跑自动测试；
2. 合入 `test`、通过 CI 和测试环境部署；
3. 生产部署仍保持 legacy；
4. 在生产运行可恢复全历史回填；
5. 实现并生成关系证据；
6. 修复已知运行期问题；
7. 执行阶段 E 的全面切换与核查。

## 十、交接记录规范

每次工作结束必须在本文件末尾追加，不覆盖旧记录：

```text
日期与时间：
执行分支与 SHA：
完成阶段：
修改文件：
执行测试：
生产数据修改：
当前 canonical 覆盖：
对账与健康摘要：
服务与读取模式：
新发现问题：
未完成事项：
下一步：
代码回滚点：
数据恢复点：
```

### 2026-09-20 初始交接

```text
日期与时间：2026-09-20 22:40 CST 后
执行分支与 SHA：feat/production-canonical-cutover @ ba9e470（从 origin/test 创建）
完成阶段：生产部署 0cfd36e；五日 canonical 构建、健康、性能、策略输入与切换审计
修改文件：本实施方案
执行测试：生产节点全量 pytest 通过；五日 audit_cutover ready=true
生产数据修改：新增 canonical 表和 dataset_checks；未覆盖 legacy；未生成 relation_checks
当前 canonical 覆盖：日线/状态主要为 2026-04-17..09-18；分钟为 09-14..09-18；日历为五日
对账与健康摘要：6,855,766 个策略输入一致；11 green、1 yellow；无 red/gray
服务与读取模式：monitor/paper/shadow active 且仍为 legacy；live core inactive
新发现问题：关系证据生成器缺失；增量发布未接入；休市心跳与 preflight 阈值不一致；浮点尾差
未完成事项：阶段 A–E
下一步：实现统一发布协调器和可恢复全历史回填器
代码回滚点：生产 3375584f69d254210d5b67a4fe23d09cc7330960
数据恢复点：legacy 表保持原状；canonical 为新增目标，可按 manifest 单独重建
```

### 2026-09-20 代码实施交接

```text
日期与时间：2026-09-20 23:22 CST
执行分支与 SHA：feat/production-canonical-cutover @ dcd34cbbcd708ce98fbefc63ff834a9ad32f919c
完成阶段：A 增量发布、B 可恢复回填、C 关系证据、D 浮点/心跳修复、E 统一门面开关代码
修改文件：canonical_backfill/publish、cutover_health/audit、消费者门面、入口 CLI、systemd drop-in、测试与本方案
执行测试：uv run pytest -q 全量通过；相关定向测试通过；git diff --check 通过
生产数据修改：无；本批次仍只修改开发分支代码
当前 canonical 覆盖：沿用初始交接记录，生产尚未执行全历史回填
对账与健康摘要：审计新增 7 条 canonical required 关系硬门禁；缺失、过期、red/gray 均阻断
服务与读取模式：生产仍为 legacy；统一 canonical env/drop-in 已就绪但尚未安装；live core 应继续 inactive
新发现问题：原登记中 3 条聚合依赖基数/键方向错误，已修正为 dt 多对一；消费者存在交易日、证券池、涨跌停旁路，已收口
未完成事项：推送分支、合入 test、部署测试环境、生产全历史回填、生成全范围证据、合入 main、生产切换与验收
下一步：先部署 test 并做 smoke；随后在生产 legacy 模式回填和审计，通过后安装统一 env/drop-in 并依次重启 monitor/paper/shadow
代码回滚点：生产仍为 3375584f69d254210d5b67a4fe23d09cc7330960；本分支可逐提交回退
数据恢复点：回填器 manifest 支持按已验证分区 resume/rebuild；不覆盖 legacy
```
