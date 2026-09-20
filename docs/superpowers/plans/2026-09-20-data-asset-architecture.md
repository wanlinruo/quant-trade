# 数据资产架构、表级血缘与健康模型实施计划

- 创建日期：2026-09-20
- 状态：阶段 0–2 已完成；阶段 3–8 代码完成；阶段 9–10 完成；阶段 11 门禁完成、生产切换待条件满足
- 设计依据：`docs/superpowers/specs/2026-09-20-data-asset-architecture-design.md`
- 需求分支：`feat/data-asset-architecture`
- 起始基线：`origin/test` @ `3375584f69d254210d5b67a4fe23d09cc7330960`

---

## 一、交接摘要

本任务不是单纯改页面，而是把当前“按来源接口落表”的数据层逐步改造成：

1. 来源原始层可审计；
2. 规范层按市场语义和粒度分表；
3. 实时与历史定稿隔离；
4. 字段、表和关系三层质量可见；
5. 页面能解释表的用途、上下游、健康和结构优化依据。

不得一次性重命名或删除现有表。每个新规范表必须经历契约、回填、对账、影子读取、消费者切换和
旧表冻结六个阶段。

## 二、全程约束

- 从 `test` 创建功能分支，小步提交；不要在本需求分支合入无关修复。
- 生产数据、warehouse、密钥、真实账户和大体积样本不得提交。
- 任何数据迁移脚本默认只写新目标，禁止原地覆盖旧表。
- 不得为了让健康变绿而放宽阈值、跳过规则或把失败降为警告。
- 未确认语义的字段写成 UNKNOWN/待查，不能编造解释。
- 原始来源表不能成为新的生产查询依赖。
- 历史接口默认不含实时状态，不允许静默回退或混源。
- 每次改变策略可见数据前，必须输出逐键、逐字段和策略输入对账。
- 计划中的目标名称是逻辑契约；物理切换前应通过查询门面保持兼容。

## 三、阶段 0：建立基线与盘点产物

### 工作

1. 记录实施起点 SHA、Python/依赖版本、warehouse 只读统计和现有测试结果。
2. 从 `schema.ALL`、`schema.READ_ONLY`、`REGISTRY` 和查询方法生成现有数据集清单。
3. 为每张表记录：字段、粒度候选、主键候选、分区、时间范围、行数、来源和调用点。
4. 搜索所有直接文件读取与 `WarehouseQuery` 调用，形成消费者清单。
5. 保存日线、分钟线、复权和策略输入的可复现摘要，不提交真实全量数据。

### 验证

- 盘点集合与 `schema.ALL + schema.READ_ONLY` 精确相等。
- 每个公开查询方法和直接 Parquet 读取点都有消费者归属。
- 当前全量测试通过，或已记录与本任务无关的基线失败。

### 完成标准

后续执行者能回答“现有每张表谁写、谁读、多少数据、什么粒度”，且盘点可由脚本重复生成。

## 四、阶段 1：表契约与依赖注册表

### 工作

1. 在 `qsys.data.catalog` 增加表级模型：
   - `DatasetLayer`：RAW / CANONICAL / REALTIME / DERIVED / OPERATIONAL；
   - `DatasetRole`：DIMENSION / FACT / EVENT / SNAPSHOT / FACTOR / LOGICAL；
   - `DatasetStatus`：ACTIVE / FROZEN / OPERATIONAL；
   - `Dataset`；
   - `DatasetDependency`；
   - Join 基数、依赖类型和必需性枚举。
2. 为全部现有表登记用途、粒度、主键、时间列、分区、状态、构建入口和消费方。
3. 从字段级 `Computed.upstream` 自动推导跨表边，并与显式依赖注册表核对。
4. 对不能安全确认的 Join 键或粒度显式登记待查，不允许猜测。

### 守护测试

- 所有物理和逻辑数据集恰好登记一次。
- 主键和时间列必须存在于 Schema。
- 字段血缘推导出的跨表边都有依赖契约。
- 依赖契约不得引用不存在的数据集或形成非法循环。
- RAW 表不得被登记为生产策略的直接消费者入口。

### 完成标准

表关系图可以只依赖注册表生成，不需要模板硬编码。

## 五、阶段 2：健康策略与三层质量结果

### 工作

1. 增加 `DatasetHealthPolicy`、`HealthStatus`、`HealthDimension` 和原因模型。
2. 新增表级检查结果与关系级检查结果 Schema；保留现有 `metric_checks` 历史不变。
3. 实现表级规则：Schema、主键非空、唯一性、粒度、分区、时效和字段状态汇总。
4. 实现关系级规则：Join 基数、匹配率、孤儿行、日期同步、上游发布后下游刷新和独立重算。
5. 为稠密日表、事件表、区间表、冻结表、实时表和运行日志分别实现健康策略。
6. 实现四态门禁：RED -> GRAY -> YELLOW -> GREEN 的判定流程；其中 RED 优先，缺少必需证据时不得为绿。

### 关键测试

- 真实性阻断失败即使覆盖 100% 仍为红。
- 必需规则未运行或结果过期为灰。
- 已知缺陷为黄，不能因为已经挂账而显示绿。
- 事件表合法 0 行不失败。
- 冻结表不因没有新日期而失败，但校验和变化必须失败。
- 上游更新而下游未刷新时关系为红。
- 错误 Join 基数、重复键和孤儿行能被捕获。

### 完成标准

任一健康状态都能返回维度、规则、检查范围、证据时间和具体原因。

## 六、阶段 3：规范基础资料

### 目标数据集

- `security_master`
- `security_name_history`
- `trading_calendar`

### 工作

1. 明确当前快照、点观测和历史区间三种有效性，不互相填充。
2. 从 `instruments` 和 `name_history` 构建新规范表；`name_snapshots` 继续作为冻结证据，不能扩张为推断历史。
3. 交易日历只认登记来源，不自行用工作日推算。
4. 增加稳定查询接口并保留旧接口适配层。

### 对账

- 主键集合与行数；
- 上市、退市、板块和名称区间逐字段；
- 区间重叠、断档和公告日有效性；
- 使用基础资料的股票池结果。

### 回滚

查询门面切回旧方法；新表保留作为未发布产物，不覆盖旧表。

## 七、阶段 4：规范历史日线及窄表拆分

### 目标数据集

- `bar_daily`
- `valuation_daily`
- `share_capital_history`
- `adjustment_factor_daily`

### 工作

1. 逐列确定 `tushare_daily` 28 列的目标归属、单位、有效期和来源字段。
2. `bar_daily` 只保存前收、OHLC、成交量和成交额等行情事实。
3. 将估值、市值、换手率拆入 `valuation_daily`。
4. 将股本按生效日口径归一到 `share_capital_history`，不得把每日快照和区间事实混为一谈。
5. 将价格累计因子和股本累计因子归一到 `adjustment_factor_daily`，保留不同用途。
6. 原始来源记录保留在审计层；规范表选择的来源通过批次 Manifest 和血缘记录。
7. 增加窄表查询方法；旧 `daily_bars()` 暂时适配新接口但不立即切换生产消费者。

### 对账

- `bar_daily` 与现生产日线逐键、逐字段、单位和 null 分布一致。
- 估值、股本和因子分别核对键集合、覆盖区间和来源。
- 复权价格、成交量调整和除权日行为与旧路径一致。
- 同一查询只扫描目标窄表字段，不再扫描 28 列宽表。

### 性能验收

- 日线单标的长区间；
- 多标的短区间；
- 全市场单日；
- 记录扫描文件、扫描列、耗时和峰值内存，与基线比较。

### 回滚

保留旧 `tushare_daily` 查询路径，通过配置或门面切回；禁止删除旧分区。

## 八、阶段 5：规范参考事实、状态和派生表

### 目标数据集

- `price_limit_daily`
- `security_status_daily`
- `market_flags_daily`
- `market_regime_daily`

### 工作

1. 将涨跌停价格规范为独立事实表。
2. 明确 ST、停复牌和退市整理的来源、稠密/稀疏表示及有效期后，再构建统一状态视图或物理表。
3. 将 `daily_flags` 的派生逻辑改为只依赖规范事实表。
4. 将 `market_regime` 改为只依赖规范行情、状态、因子和指数数据。
5. 保持未知为 null，不用名称或价格启发式补真值。

### 对账

- 每个派生字段独立重算；
- 与现有 flags/regime 逐键比较；
- 对差异按来源、口径、缺失和错误分类；
- S6/S7 Gate 及候选股票结果必须专项对账。

## 九、阶段 6：统一历史分钟契约

### 目标数据集

- `bar_minute_1m`

### 工作

1. 固定分钟时间戳为收盘时刻，价格单位为元，成交量单位为手，成交额单位为元。
2. 各供应商分钟数据保留在原始层；规范层按明确优先级选择每个 `symbol × session` 的唯一来源。
3. 禁止同一标的日逐分钟混源；来源选择写入行或分区 Manifest。
4. 规范表按交易日分区，分区内按 `(symbol, ts)` 排序并原子发布。
5. 兼容旧年分区和付费 shard 只存在于迁移读取器，不成为新规范。

### 质量门禁

- 交易时段与时间戳集合；
- 每标的日预期 241 根的适用性；
- OHLC、成交量、成交额合法性；
- 分钟聚合到日线的一致性；
- 来源覆盖、重复键和上市前数据隔离；
- PIT `visible_at` 规则。

### 性能验收

- 候选股票单日；
- 单标的一年；
- 全市场单日；
- 跨年小股票池。

## 十、阶段 7：实时数据与收盘发布

### 目标逻辑数据集

- `quote_realtime_latest`
- `bar_daily_realtime`
- `bar_minute_realtime`

### 工作

1. 在现有 Redis quote 流上定义稳定的实时数据契约、时间戳、行情年龄和来源。
2. 从实时 quote 聚合当日 OHLCVA；当前分钟和已完成分钟状态明确区分。
3. 历史与实时 API 分开，组合 API 必须显式 `as_of` 与 `include_live=True`。
4. 增加收盘发布流程：冻结当日状态、执行门禁、写临时分区、校验 Manifest、原子替换。
5. 发布失败时保留实时证据和错误记录，不污染历史规范表。

### 测试

- 盘前、盘中、午休、收盘和跨日状态；
- 当前分钟未完成时不能冒充定稿分钟；
- 回测默认看不到实时状态；
- 发布重试幂等；
- 部分行情、陈旧 tick 和 Redis 不可用时明确失败。

## 十一、阶段 8：查询门面与消费者迁移

### 新接口原则

- `historical_daily_bars(...)`
- `historical_minute_bars(...)`
- `realtime_quotes(...)`
- `realtime_daily_bars(...)`
- `realtime_minute_bars(...)`

旧方法先作为兼容适配器，内部逐步调用新接口。消费者按以下顺序迁移：

1. 数据页面和只读诊断；
2. 研究与脚本；
3. 回测；
4. 模拟交易；
5. 影子实盘；
6. 实盘核心。

每迁移一类消费者都要运行专项场景测试，并记录旧、新输入的键和值差异。不得在一次提交中同时切换
所有生产路径。

## 十二、阶段 9：数据管理页面

### 路由

- `/data/tables`
- `/data/tables/{dataset}`
- `/data/relations/{upstream}/{downstream}`
- `/data/quality/rules/{check_id}`

保留现有 `/data`、`/data/catalog`、指标详情和数据源链接，必要时做兼容跳转。

### 页面

1. 数据表列表默认只显示规范、实时、派生和运行层；原始层折叠。
2. 表名旁显示带文字的红黄绿灰角标。
3. 表详情展示契约、五维健康、覆盖、上下游图、字段、规则、证据和消费者。
4. 关系详情展示 Join 键、基数、匹配率、孤儿行、日期差和刷新状态。
5. 规则详情展示定义、通过条件、局限、实现位置、最近与历史结果。
6. 结构建议展示结论和证据，不提供自动执行按钮。

### 页面验收

任选一张表，用户能回答：用途、粒度、主键、来源、上下游、连接方式、健康问题、消费方和结构建议。

## 十三、阶段 10：结构优化建议器

### 输出

- `MERGE_CANDIDATE`；
- `SPLIT_CANDIDATE`；
- `KEEP_SEPARATE`；
- `INSUFFICIENT_EVIDENCE`。

### 规则

- 合并建议必须满足同粒度、同主键、完整一对一、同节奏、同生命周期、无独立消费者且健康为绿。
- 到货时点不同、可选关系、一对多、复用上游或独立恢复需求时保持独立。
- 混合粒度、有效期、更新节奏、事实与参数因子时提示拆分。
- 任一关键元数据或质量证据缺失时只返回证据不足。

建议器必须是确定性纯函数，并对每条触发依据生成可读说明。

## 十四、阶段 11：切换、冻结与清理

### 切换条件

- 新规范表完整回填；
- 所有必需健康维度非灰且无红；
- 新旧逐键和策略输入对账完成；
- 性能不低于约定基线；
- 测试环境覆盖至少一个完整交易周期；
- 回滚开关和旧数据仍可用。

### 切换动作

1. 固定待发布 commit SHA；
2. 停止目标窗口内的相关写任务；
3. 完成最后增量与对账；
4. 切换查询门面；
5. 恢复服务并观察健康、日志和策略输入；
6. 记录切换批次和 Manifest。

### 旧表处置

- 先标记 FROZEN/AUDIT_ONLY；
- 守护测试禁止新增生产消费者；
- 保留至少一个约定观察期；
- 删除必须另立任务，提供备份、校验和及恢复演练，不包含在本计划内。

## 十五、总体验收矩阵

### 功能

- 核心市场数据按基础、日线、分钟、实时、复权、估值、状态和派生清晰定位。
- 表、字段、来源、规则和消费方可双向跳转。
- 原始层默认折叠但可审计。

### 正确性

- Schema、主键、粒度、覆盖、真实性和关系检查均有自动测试。
- 历史查询不会混入实时未定稿值。
- 不存在无提示的多来源回退。
- 派生数据可由规范上游独立重算。

### 性能

- 日线查询只扫描所需年份和列。
- 分钟查询只扫描所需日期分区。
- 没有按单只股票产生不可控小文件。
- 基准结果记录扫描量、耗时和内存，并在 CI 或专项回归中设置合理上限。

### 运维

- 发布、重跑和回滚幂等。
- Manifest 能定位每个分区的来源、Schema 和校验和。
- 红黄绿灰状态都能追溯到规则与证据。

## 十六、建议提交拆分

后续实现不要把整个计划压成一个提交。建议至少按以下主题拆分：

1. `feat(data): add dataset and dependency contracts`
2. `feat(data): add table and relation health checks`
3. `feat(data): add canonical reference datasets`
4. `feat(data): split canonical daily market datasets`
5. `feat(data): normalize historical minute bars`
6. `feat(data): separate realtime and finalized bars`
7. `refactor(data): migrate consumers to canonical queries`
8. `feat(web): add data asset and health views`
9. `test(data): add reconciliation and performance gates`

每个提交必须可独立测试和回滚。

## 十七、交接记录

### 当前状态

- 2026-09-20：阶段 0–2 已完成，阶段 3–7 的 Schema、构建/发布、查询门面和对账代码已完成。
- 2026-09-20：阶段 8 已完成稳定历史/实时门面、差异审计及数据页/只读诊断入口；生产消费者未切换。
- 当前改动仍在 `feat/data-asset-architecture` 未提交工作区；未修改当前或生产 warehouse。
- 阶段 3–7 的目标环境回填、实时联调、真实对账、性能门禁和消费者切换尚未执行。

### 已确认决策

- 表优先的信息架构；
- 原始层默认折叠；
- 规则首版只读可追溯；
- 健康采用红黄绿灰门禁；
- 合并/拆分采用静态证据化建议；
- 实时与历史定稿隔离；
- 迁移采用并行、对账、影子读取和渐进切换。

### 下一位执行者的第一步

在具备完整来源表的目标或临时环境中依次运行阶段 3–7 构建/发布脚本，保存逐键逐字段对账和性能证据；
门禁未通过前不要切换生产消费者，也不要冻结或删除旧表。

### 每次交接必须追加

```text
日期：
执行分支与 SHA：
完成阶段：
修改文件：
执行命令与测试结果：
数据对账摘要：
新发现问题：
未完成事项：
下一步：
回滚点：
```

不得覆盖历史交接记录；按时间顺序追加。

### 2026-09-20 阶段 0–1 交接

```text
日期：2026-09-20
执行分支与 SHA：feat/data-asset-architecture @ aaf1f83（其后为当前未提交工作区）
完成阶段：阶段 0 基线与盘点；阶段 1 表契约与依赖注册表
修改文件：catalog/types.py、catalog/datasets.py、catalog/inventory.py、catalog/__init__.py、
          scripts/inventory_data_assets.py、两组守护测试、盘点基线 JSON
执行命令与测试结果：uv run pytest -> 1842 passed, 2 warnings in 53.07s
数据对账摘要：28 个物理数据集与 schema.ALL + schema.READ_ONLY 精确相等；
              2 个逻辑数据集已登记；字段血缘推导 19 条跨表边且全部有显式关系契约；
              本地 warehouse 仅通过 Parquet footer 读取统计，未读取或修改数据行。
新发现问题：ingest_log 的稳定唯一键尚不能安全确认，已显式登记 UNKNOWN；
            paid_minute_bars 当前仍被统一日内查询直接消费，阶段 6 前作为冻结规范输入登记，
            不能提前标成纯 RAW 审计表。
未完成事项：阶段 2–11；本次未创建新 Schema、规范表或迁移脚本。
下一步：实现 DatasetHealthPolicy、表级/关系级结果 Schema 与四态门禁。
回滚点：aaf1f83a07eecf671b72cf4cd0d20e04b51b7c24
```

### 2026-09-20 阶段 2 交接

```text
日期：2026-09-20
执行分支与 SHA：feat/data-asset-architecture @ aaf1f83（其后为当前未提交工作区）
完成阶段：阶段 2 健康策略、表级/关系级结果与四态门禁
修改文件：data/schema.py、catalog/health.py、catalog/datasets.py、catalog/operational.py、
          catalog/rules_contract.py、data/query.py、健康模型及既有守护测试
执行命令与测试结果：uv run pytest -> 1861 passed, 2 warnings in 53.77s
数据对账摘要：metric_checks 保持原样；新增 dataset_checks（14 列）与
              relation_checks（18 列），字段字典现精确覆盖 285 个落盘字段；
              未执行生产 warehouse 写入。
新发现问题：健康结果已有纯检查、持久化和查询接口，但尚未接入阶段 9 的 Web 页面；
            ingest_log 主键仍为 UNKNOWN，不以猜测键产生假绿状态。
未完成事项：阶段 3–11；规范基础资料尚未创建或切换消费者。
下一步：阶段 3 新增 security_master、security_name_history、trading_calendar，
        先并行构建和对账，再增加稳定查询门面，不切换生产消费者。
回滚点：aaf1f83a07eecf671b72cf4cd0d20e04b51b7c24
```

### 2026-09-20 阶段 3 代码交接

```text
日期：2026-09-20
执行分支与 SHA：feat/data-asset-architecture @ aaf1f83（其后为当前未提交工作区）
完成阶段：阶段 3 规范基础资料的 Schema、并行构建、查询门面和对账代码
修改文件：data/schema.py、pipeline/canonical_reference.py、providers/tushare.py、
          data/query.py、catalog/reference.py、catalog/datasets.py、
          scripts/build_canonical_reference.py 及相关测试
执行命令与测试结果：uv run pytest -> 1871 passed, 2 warnings in 63.06s；
                  真实 Tushare 数据仅在 /tmp 临时仓库演练
数据对账摘要：新增 security_master、security_name_history、trading_calendar；
              在 /tmp 临时仓库用真实 Tushare 来源演练：5,904 / 13,865 / 13,162 行，
              三表逐键逐字段零差异；未对当前或生产 warehouse 执行回填或写入。
新发现问题：真实 name_history 报告 7 个重叠、246 个断档，工具只报告不填补；
            当前本地 warehouse 的 instruments 是缺 as_of 的旧 Schema，且无 name_history，
            新增预检会在任何写入前明确拒绝，不能以当前日期补造观测日。
未完成事项：目标环境回填、真实逐键对账和完整观察期；阶段 4–11。
下一步：在目标环境运行 scripts/build_canonical_reference.py，确认三张表对账；
        未通过前不得切换消费者，也不得开始删除或冻结旧表。
回滚点：aaf1f83a07eecf671b72cf4cd0d20e04b51b7c24
```

### 2026-09-20 阶段 4 代码交接

```text
日期：2026-09-20
执行分支与 SHA：feat/data-asset-architecture @ aaf1f83（其后为当前未提交工作区）
完成阶段：阶段 4 规范历史日线窄表的 Schema、并行构建、查询门面和精确对账代码
修改文件：data/schema.py、pipeline/canonical_daily.py、data/query.py、
          catalog/canonical.py、catalog/datasets.py、catalog/rules_contract.py、
          scripts/build_canonical_daily.py 及相关测试
执行命令与测试结果：uv run pytest -> 1881 passed, 2 warnings in 66.00s
数据对账摘要：新增 bar_daily、valuation_daily、share_capital_history、
              adjustment_factor_daily 四张规范表；构建器逐目标检查键唯一、非空并执行
              逐键逐字段精确对账；当前本地来源不完整，因此未写当前或生产 warehouse，
              也未切换 daily_bars 等既有消费者。
新发现问题：当前环境不具备完整 tushare_daily/share_capital 来源，无法诚实完成真实回填与性能测量；
            构建预检会在首次写入前拒绝缺失来源、列或冲突键。
未完成事项：阶段 3–4 的目标环境回填、真实对账、性能门禁和观察期；阶段 5–11。
下一步：在具备完整来源数据的目标或临时环境运行 scripts/build_canonical_daily.py，
        保存行数、耗时和精确对账结果；门禁通过后才能进入影子读取和消费者切换。
回滚点：aaf1f83a07eecf671b72cf4cd0d20e04b51b7c24
```

### 2026-09-20 阶段 5 代码交接

```text
日期：2026-09-20
执行分支与 SHA：feat/data-asset-architecture @ aaf1f83（其后为当前未提交工作区）
完成阶段：阶段 5 规范价格限制、证券状态、市场标志与市场生态的 Schema、并行构建、查询门面和对账代码
修改文件：data/schema.py、pipeline/canonical_state.py、data/query.py、
          catalog/canonical.py、catalog/datasets.py、catalog/rules_contract.py、
          catalog/rules_valid.py、scripts/build_canonical_state.py 及相关测试
执行命令与测试结果：阶段 5 定向测试 27 passed；uv run pytest -q 全量通过，2 warnings；
                  项目环境未安装 ruff，未执行 ruff 静态检查
数据对账摘要：新增 price_limit_daily、security_status_daily、market_flags_daily、
              market_regime_daily；样例仓库中四表逐键逐字段零差异；生产消费者仍读取旧表，
              未执行当前或生产 warehouse 回填。
新发现问题：当前环境缺少阶段 3–5 的完整目标来源，不能诚实完成全量真实对账、性能测量及
            S6/S7 Gate 和候选股票专项对账；退市整理稀疏表缺失与“无事件”已由预检明确区分。
未完成事项：阶段 3–5 的目标环境回填、真实对账、性能门禁、S6/S7 与候选结果专项对账和观察期；阶段 6–11。
下一步：在具备完整来源数据的目标或临时环境依次运行 build_canonical_reference.py、
        build_canonical_daily.py、build_canonical_state.py，保存对账与性能证据；
        门禁通过后再开始影子读取，未通过前不得切换消费者。
回滚点：aaf1f83a07eecf671b72cf4cd0d20e04b51b7c24
```

### 2026-09-20 阶段 6 代码交接

```text
日期：2026-09-20
执行分支与 SHA：feat/data-asset-architecture @ aaf1f83（其后为当前未提交工作区）
完成阶段：阶段 6 规范一分钟线 Schema、标的日唯一选源、单位统一、日分区原子发布、查询门面和写后对账代码
修改文件：data/schema.py、data/storage.py、data/query.py、pipeline/canonical_minute.py、
          catalog/canonical.py、catalog/datasets.py、catalog/rules_contract.py、
          catalog/rules_valid.py、scripts/build_canonical_minute.py 及相关测试
执行命令与测试结果：阶段 6 及关联定向测试 76 passed；uv run pytest -q 全量通过，2 warnings
数据对账摘要：新增 bar_minute_1m；按 paid > Tushare 显式覆盖 > JoinQuant 显式覆盖 > QMT
              为每个 symbol × session 选择唯一来源；成交量统一为手，保留 source 和 visible_at；
              样例仓库写后逐键逐字段零差异，旧 minute_bars/intraday 消费者未切换。
新发现问题：Polars hour/minute 提取值为 Int8，计算分钟序号前必须提升到 Int32，否则 09:30 会溢出；
            D-20 的付费源数值差异仍未结案，规范层只沿用已验证的“不平移源标签、延后一分可见”策略，
            不宣称其与 QMT 数值等价；当前仓库没有真实历史分钟源，不能执行全量性能测量。
未完成事项：目标环境全量构建、241/240 根适用性审阅、日线聚合与来源覆盖门禁、上市前隔离复核、
            四类性能验收和观察期；阶段 7–11。
下一步：在完整分钟仓库按小日期窗口运行 scripts/build_canonical_minute.py，先审阅来源分布、
        nonstandard_counts 和 D-20 样本，再扩到年度；全部门禁通过前不得切换消费者。
回滚点：aaf1f83a07eecf671b72cf4cd0d20e04b51b7c24
```

### 2026-09-20 阶段 7 实时契约交接

```text
日期：2026-09-20
执行分支与 SHA：feat/data-asset-architecture @ aaf1f83（其后为当前未提交工作区）
完成阶段：阶段 7 实时逻辑契约、Redis 原子最新快照、实时聚合与收盘事务式发布代码
修改文件：data/realtime.py、live/config.py、paper/quotes.py、scripts/run_market_gateway.py、
          pipeline/realtime_close.py、scripts/publish_realtime_close.py、catalog/datasets.py 及相关测试
执行命令与测试结果：阶段 7 定向测试全部通过；uv run pytest -q 全量通过，2 warnings
数据对账摘要：登记 quote_realtime_latest、bar_daily_realtime、bar_minute_realtime 三个逻辑数据集；
              最新报价包含市场时刻、接收时刻、年龄和来源；累计成交量由股统一为手；
              当前分钟显式 finalized=false，观测不完整时 partial=true，默认查询不返回为定稿分钟。
新发现问题：仅有周期性累计快照时，进程启动所在分钟无法证明拥有完整分钟增量，必须标 partial，
            不能为了凑齐分钟线把启动前累计成交量塞入当前分钟；Redis 空响应不能覆盖上一份证据；
            收盘发布必须同时保护年度日线文件和分钟日分区，第二目标提交失败时两者都要回滚。
未完成事项：目标环境 Redis/QMT 联调、全天候选生成、真实收盘演练与观察期；阶段 8–11。
下一步：用隔离 warehouse 和完整交易日快照生成候选 JSON，运行 publish_realtime_close.py 做失败注入与
        幂等重试演练；确认 Manifest 和双目标回滚证据后，再进入阶段 8 查询门面与消费者迁移。
回滚点：aaf1f83a07eecf671b72cf4cd0d20e04b51b7c24
```

### 2026-09-20 阶段 8 稳定门面交接

```text
日期：2026-09-20
执行分支与 SHA：feat/data-asset-architecture @ aaf1f83（其后为当前未提交工作区）
完成阶段：阶段 8 稳定历史/实时查询门面、新旧输入差异审计、数据页与只读诊断入口
修改文件：data/access.py、data/query.py、web/data_views.py、scripts/audit_query_facade.py 及相关测试
执行命令与测试结果：阶段 8 及关联定向测试 49 passed；uv run pytest -q 全量通过，2 warnings
数据对账摘要：historical_daily_bars 只读 bar_daily，historical_minute_bars 只读 bar_minute_1m，
              均禁止来源回退；facade_reconciliation 对旧/新日线和分钟逐键逐字段比较；
              样例仓库新旧输入零差异，生产消费者仍保持旧入口。
新发现问题：当前分钟的 ts 是未来一分钟收盘标签；若合并查询只返回历史十列，会丢失状态并伪装成定稿，
            因此 include_live=True 的分钟结果强制附带 finalized/partial，且仍须显式 as_of；
            当前本地 warehouse 未完成规范表回填，不能安全迁移研究、回测或交易消费者。
未完成事项：在目标环境运行新旧门面对账；依次迁移研究脚本、回测、模拟交易、影子实盘和实盘核心；
            每类消费者的专项场景测试及切换观察期；阶段 9–11。
下一步：先在目标环境运行 audit_query_facade.py；只有 daily/minute 两组 matches=true 时，
        才迁移研究与回测消费者，且每次只切一类。
回滚点：aaf1f83a07eecf671b72cf4cd0d20e04b51b7c24
```

### 2026-09-20 阶段 9 数据管理页面交接

```text
日期：2026-09-20
执行分支与 SHA：feat/data-asset-architecture @ aaf1f83（其后为当前未提交工作区）
完成阶段：阶段 9 表级资产目录、表详情、关系详情、规则详情和兼容导航
修改文件：web/asset_views.py、web/app.py、web/templates/data_*.html、web/templates/base.html、
          scripts/run_monitor.py、tests/web/test_data_asset_pages.py
执行命令与测试结果：阶段 9 页面专项测试 7 passed；既有数据页与指标字典回归 31 passed；
                  uv run pytest -q 全量通过，2 warnings
页面验收摘要：/data/tables 默认隐藏 raw 层并显示文字化红黄绿灰状态；表详情集中展示用途、粒度、
              主键、来源、五维健康、覆盖、上下游、字段、规则、证据、消费者和只读结构建议；
              关系详情展示 Join 键、基数、匹配率、孤儿行、检查范围与刷新状态；规则详情展示定义、
              通过条件、局限、实现位置及结果历史。缺少或过期证据严格显示灰色。
新发现问题：阶段 10 建议器尚未实现，因此结构建议固定为 INSUFFICIENT_EVIDENCE，并明确列出原因；
            页面不提供任何自动执行按钮。目标仓库尚未生成完整五维和关系证据，投产后会以灰色为主。
未完成事项：阶段 10 确定性结构优化建议器；目标环境健康检查补跑与页面人工验收；阶段 11 切换清理。
下一步：实现纯函数建议器及其边界用例，将建议和逐条触发证据接入现有表详情的只读建议区域。
回滚点：aaf1f83a07eecf671b72cf4cd0d20e04b51b7c24
```

### 2026-09-20 阶段 10 结构优化建议器交接

```text
日期：2026-09-20
执行分支与 SHA：feat/data-asset-architecture @ aaf1f83（其后为当前未提交工作区）
完成阶段：阶段 10 确定性结构优化建议器及数据管理页面接入
修改文件：data/catalog/advisor.py、data/catalog/__init__.py、web/asset_views.py、
          web/templates/data_table_detail.html、web/templates/data_relation_detail.html、
          tests/data/catalog/test_advisor.py、tests/web/test_data_asset_pages.py
执行命令与测试结果：建议器、表级契约、健康模型和 Web 相关测试 65 passed；
                  uv run pytest -q 全量通过，2 warnings
建议摘要：纯函数只接收不可变标准化证据，输出 MERGE_CANDIDATE、SPLIT_CANDIDATE、
          KEEP_SEPARATE 或 INSUFFICIENT_EVIDENCE，并为每个触发项保留可读理由；缺少主键、
          消费者、质量或关系证据时，证据不足优先于任何结构推断。Web 适配层从表契约、来源节奏、
          字段有效期、消费者、五维健康及关系检查提取信号，表详情与关系详情均只读展示结论。
新发现问题：现有目标仓库尚未补齐五维与关系检查结果，因此多数线上结论在补跑前会保持证据不足；
            这是门禁行为，不应通过默认绿色或启发式猜测绕过。
未完成事项：目标环境健康与关系证据补跑；阶段 11 消费者切换、冻结与清理；完整交易周期观察。
下一步：按阶段 11 条件先做只读切换审计，生成规范表回填、健康门禁、新旧输入、性能和回滚开关
        的统一检查报告；任何条件未满足都不得切换生产消费者或删除旧表。
回滚点：aaf1f83a07eecf671b72cf4cd0d20e04b51b7c24
```

### 2026-09-20 阶段 11 切换门禁交接

```text
日期：2026-09-20
执行分支与 SHA：feat/data-asset-architecture @ aaf1f83（其后为当前未提交工作区）
完成阶段：阶段 11 显式双路门面、只读切换审计和失败关闭门禁；未执行生产切换或旧表清理
修改文件：data/cutover.py、scripts/audit_cutover.py、tests/data/test_cutover.py
执行命令与测试结果：切换门面、审计与关联测试 31 passed；uv run pytest -q 全量通过，2 warnings；
                  git diff --check 与 py_compile 通过
门禁摘要：QSYS_MARKET_DATA_FACADE 默认 legacy，只接受 legacy/canonical，canonical 路径禁止回退；
          audit_cutover 同时检查固定且干净的 commit、规范表回填、新旧逐键逐值对账、全部必需健康维度、
          查询耗时/扫描文件/扫描列/峰值内存、策略输入摘要、完整交易周期及旧数据回滚可用性。
本地实审：2026-09-15 至 2026-09-19 的报告 ready=false，八项门禁全部未通过：工作区 dirty，
          日线与分钟 old/new 均为 0/0，12 张规范表健康均为 gray，缺性能和策略输入证据，
          完整交易日为 0，所选窗口无旧日线/分钟回滚数据。脚本以非零状态退出并确认未执行切换。
未完成事项：在目标环境完成规范表回填和健康/关系检查；生成性能与策略输入证据；覆盖至少一个完整
            交易周期；固定干净发布 SHA 后重新审计；随后才可逐类消费者切换和观察。
旧表处置：保持现状。现有冻结证据表继续只读；仍在生产使用的 tushare_daily/minute_bars 不得提前冻结；
          删除不在本计划范围内，必须另立带备份、校验和与恢复演练的任务。
下一步：把目标环境生成的 performance.json 与 strategy.json 交给 scripts/audit_cutover.py；只有
        ready=true 才进入停写窗口、最后增量、逐消费者切换和观察记录。
回滚点：aaf1f83a07eecf671b72cf4cd0d20e04b51b7c24
```

### 2026-09-20 阶段 11 策略摘要稳定性修复交接

```text
日期：2026-09-20
执行分支与 SHA：feat/data-asset-architecture @ aaf1f83（其后为当前未提交工作区）
完成事项：修复策略输入证据在逻辑值相同但 Arrow 物理 dtype、chunk 或 IPC schema 不同时产生假差异的问题
修改文件：data/cutover_evidence.py、本实施计划
执行命令与测试结果：阶段 11 定向测试 8 passed；uv run pytest -q -> 1931 passed, 2 warnings；
                  python -m compileall、git diff --check 通过
修复摘要：策略摘要现按输入族、稳定字段名和规范化逻辑值生成；整数/浮点等值不再因物理表示不同而
          产生不同摘要，真实字段值漂移仍由逐键逐字段对账与摘要共同拦截。
本地实审：重新执行 2026-09-15 至 2026-09-19 切换审计，ready=false；仍因工作区未固定、
          规范表未回填、健康证据为灰、缺性能和策略证据、无完整交易周期及旧数据回滚输入而失败关闭，
          未执行消费者切换、冻结或清理。
未完成事项：目标环境回填和健康/关系检查；性能与策略输入证据；至少一个完整交易周期观察；
            固定干净发布 SHA 后重新审计和逐类消费者切换。
下一步：在具备完整来源表的目标环境生成 performance.json 与 strategy.json，并重新运行
        scripts/audit_cutover.py；ready=true 前保持 legacy 门面和旧表可回滚。
回滚点：aaf1f83a07eecf671b72cf4cd0d20e04b51b7c24
```
