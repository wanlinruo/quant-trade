# qsys：个人量化交易系统

qsys 是一套面向 A 股的自建量化交易系统，覆盖数据采集与治理、策略研究、事件驱动回测、
模拟/影子运行、实盘交易、监控告警和跨 Linux/Windows 发布。项目的目标不是堆叠策略，
而是让同一套策略与交易语义能够被验证、复现、审计，并在真实资金链路中失败关闭。

> 系统包含真实交易能力。示例配置永久禁用下单；任何生产发布、账户操作或安全配置变更，
> 都必须遵循生产门禁和回滚流程。

## 项目背景

项目最初用于替代“聚宽策略运行时 → Redis → Windows → miniQMT”的前半段平台能力。
现阶段数据、策略、回测、运行时与运维控制面已经自建；Windows 侧继续保留轻量的 QMT
行情与执行适配层。

主要解决以下问题：

- 数据、撮合和费用模型可解释，不依赖黑盒平台。
- 历史研究与实盘使用同一套策略、约束和订单语义。
- 日线、分钟线、竞价 Tick、停复牌、涨跌停价与基本面数据可追溯、可质检。
- Linux 决策与 Windows 执行解耦，跨节点故障能够熔断、恢复和对账。
- 每次代码发布、数据发布、研究运行和交易事件均留下可审计证据。

## 主要功能

| 领域 | 当前能力 |
|---|---|
| 数据 | Tushare 日线与参考数据、QMT 分钟线/竞价 Tick、Parquet 分区、DuckDB/Polars 查询、数据目录与质量规则 |
| 策略 | 显式上下文、参数校验、结构化首板策略、统一交易约束与调度 |
| 回测 | 事件驱动撮合、费用/滑点、公司行为、绩效指标、运行产物与对照报告 |
| 交易 | 模拟盘、影子盘、实盘核心、Redis Streams 协议、QMT 执行代理、行情网关 |
| 风控 | 账户保护、仓位与流动性限制、心跳门禁、kill switch、超时熔断、日终对账 |
| 研究 | 不可变数据快照、隔离 Research Runner、可审计作业协议，以及首板、竞价、门禁归因和固定样本外验证 |
| 运维 | FastAPI 监控台、运行记录、健康时刻轴、告警、日报、备份与故障演练 |
| 发布 | 基于 commit SHA 的测试/生产门禁、组件影响分析、不可变组件制品、Linux/Windows 原子发布与回滚证据 |

## 架构结论

当前系统适合继续采用 **模块化单体 + 少量强隔离服务**，而不是把每个 Python 目录都拆成
微服务。独立服务的判断标准应是：是否需要独立的故障域、权限、资源上限、生命周期或发布
节奏。仅仅“代码可以复用”不构成拆服务理由。

这种形态兼顾了两点：

- 业务语义集中在一个版本化 `qsys` 包中，避免回测、研究和实盘各自实现一套规则。
- 运行时按风险边界独立：数据写入、交易决策、券商执行、Web、研究任务互不取得不必要权限。

### 当前系统拓扑

下图同时标出生产运行形态和已经完成验收的测试研究平面。虚线表示能力已经实现，但生产
Research Runner 尚未获得激活批准；它不表示生产服务正在运行。

```mermaid
flowchart LR
    subgraph DEV[Mac / GitHub Actions]
        CODE[单一代码库<br/>qsys Python 包]
        CI[CI / exact-SHA 门禁]
        ART[版本化制品<br/>manifest + wheel hash]
        CODE --> CI
        CI --> ART
    end

    subgraph LINUX[生产 Linux]
        DATA[数据定时任务<br/>采集 / 归档 / 质检]
        WH[(可变生产 Warehouse<br/>Parquet + DuckDB)]
        CATALOG[Catalog Verifier<br/>独立 release / venv / current]
        WEB[监控台 / Job API]
        CORE[Live / Shadow Core<br/>策略 + 风控 + 状态]
        OBS[健康观察与日报]

        DATA -->|排他写锁| WH
        WH --> CATALOG
        WH --> WEB
        WH --> CORE
        CORE --> OBS
        WEB --> OBS
    end

    subgraph TEST_RESEARCH[已验收的隔离研究平面]
        TWH[(测试 Warehouse)]
        SNAP[(不可变 Snapshot<br/>manifest + content hashes)]
        TWEB[Test Web / Job API]
        JOBS[(job request / status)]
        RUNNER[Research Runner<br/>独立账号 + cgroup + 私有网络]
        OUT[(日志 / 产物 / 资源证据)]

        TWH -->|原子发布| SNAP
        TWEB --> JOBS
        JOBS --> RUNNER
        SNAP -->|只读固定 snapshot_id| RUNNER
        RUNNER --> OUT
        OUT -->|只读| TWEB
    end

    subgraph WINDOWS[云 Windows]
        REDIS[(Redis Streams / 状态)]
        GATEWAY[只读行情网关]
        AGENT[执行代理]
        QMT[miniQMT / xtquant]

        QMT --> GATEWAY
        AGENT <--> QMT
        GATEWAY <--> REDIS
        AGENT <--> REDIS
    end

    CI -->|共享 Linux 发布路径| LINUX
    ART -->|独立不可变制品| CATALOG
    ART -.生产激活仍冻结.-> RUNNER
    CI -->|经 Linux 协调发布| WINDOWS
    CORE <--> |SSH 隧道后的 Redis 协议| REDIS
    DATA <--> |SSH 驱动归档| QMT
    OBS <--> REDIS
    WEB -.只读状态.-> REDIS
```

当前已经清晰的边界：

- Windows 行情网关只依赖 `xtdata`，没有账户和下单接口。
- Windows 执行代理负责订单翻译、回报和本地安全闸，不包含策略逻辑。
- Linux live core 是交易决策与风控主体；券商不可用或心跳异常时失败关闭。
- 数据任务用统一写锁串行写生产 Warehouse；隔离研究作业只读绑定 `snapshot_id` 的不可变
  快照，不再读取一个会移动的 `warehouse/current`。
- Research Runner 使用独立系统账号、独立作业目录、只读快照挂载、私有网络和
  Memory/CPU/IO/超时限制；Web 只提交、取消和只读展示作业，不执行 Runner worker。
- Runner 请求固定记录 code SHA、wheel SHA-256、snapshot ID/content SHA、参数和实际 cgroup
  上限；状态、日志、产物 hash、退出码和资源峰值均可审计。
- 部署契约将改动映射到 Linux Web、常驻服务、数据任务、systemd、Windows agent 和
  Windows gateway 等组件，支持按影响范围发布。
- `catalog-verify` 已采用独立制品、release 目录、venv、`current` 原子指针和组件级回滚；
  Windows agent/gateway 也有各自的原子应用发布边界。

### 独立发布能力现状

“部署契约能识别组件”和“组件拥有独立制品与运行环境”是两个不同成熟度。当前并非所有
服务都已完全独立发布：

| 运行单元 | 独立影响分析/重启范围 | 独立制品、环境与版本指针 | 当前状态 |
|---|---|---|---|
| Catalog Verifier | 是 | 是 | 已在生产完成组件级发布，不切换共享 checkout、不重启 Live Core |
| Research Runner | 是 | 是 | test 节点已完成 staging、权限与压力验收；生产 unit 默认不激活 |
| Windows Agent / Gateway | 是 | 是（应用级原子 release） | 可分别计划、切换、健康验证和回滚 |
| Linux systemd 配置 | 是 | 不适用 | 可按实际变化文件 apply/verify/rollback，并恢复 enable/active 状态 |
| 观测与只读运维组件 | 是 | 是 | Service Checks、Storage Report、Daily Digest、Health Observer 已有独立 exact-SHA 制品；生产激活分别受门禁 |
| Daily Archive | 是 | 是 | 已有独立制品、工作目录与运行记录边界；warehouse/Windows 最小权限和首次激活仍需单独审查 |
| Backtest Worker | 是 | 是 | Web 已只保留提交、取消与展示；worker 有独立 exact-SHA 制品及仅限 workbench/runs 的写边界，生产账号与首次激活待单独审批 |
| Premarket Limits | 是 | 是 | 已有独立制品、运行记录与 Warehouse 写锁边界；Tushare 配置、Redis 最小 ACL 和首次激活仍需单独审查 |
| Web、Live/Paper/Shadow | 是 | 否 | 仍共享生产 checkout 和 Python 环境；Web/Paper 共享 SQLite 所有权需先拆分 |
| 其余数据定时任务 | 是 | 否 | 仍主要共享生产 checkout；按写权限与发布节奏继续拆分 |
| Test runtime | 单一 `test-runtime` | 否 | 仍是集成测试用的整体运行时 |

因此，系统已经具备了把高风险运行单元逐个迁移为独立发布组件的控制面和首批真实闭环，
但“所有服务完全独立部署”仍是后续演进方向。下一批需继续处理 Web/Paper 状态所有权和
Tushare 收盘同步；Live Core 涉及实盘策略与状态兼容，必须最后按更严格门禁处理。

## 已落地的隔离研究架构

[#285](https://github.com/wanlinruo/quant-trade/issues/285) 已完成并关闭。它没有把系统全面
微服务化，而是落地了两个必要边界：**不可变快照发布边界**和**隔离研究执行边界**。

```mermaid
flowchart LR
    subgraph BUILD[代码交付]
        REPO[单一代码库]
        PKG[CI 构建的版本化 wheel<br/>绑定 commit SHA]
        REPO --> PKG
    end

    subgraph DATA_PLANE[数据平面]
        INGEST[数据采集与质检]
        PUBLISH[Snapshot Publisher<br/>唯一快照发布者]
        SNAP[(不可变快照<br/>snapshot_id + manifest + hashes)]
        CURRENT[current 原子指针]

        INGEST --> PUBLISH
        PUBLISH --> SNAP
        PUBLISH --> CURRENT
        CURRENT --> SNAP
    end

    subgraph RESEARCH_PLANE[研究平面]
        API[Web / Job API<br/>只提交与查看]
        JOBS[(作业 manifest / 状态)]
        RUNNER[Research Runner<br/>独立账号 + cgroup]
        ART[(研究日志与产物<br/>hash 可审计)]

        API --> JOBS
        JOBS --> RUNNER
        PKG --> RUNNER
        SNAP -->|只读挂载指定 snapshot_id| RUNNER
        RUNNER --> ART
        ART -->|只读展示| API
    end

    BUS[(生产 Redis / 券商链路)]
    PROD[(生产 Warehouse / 状态库)]
    RUNNER -.禁止访问.-> BUS
    RUNNER -.禁止访问或写入.-> PROD
    RUNNER -.禁止写入.-> SNAP
```

### 服务边界与所有权

| 运行单元 | 独占职责 | 可读 | 可写 | 独立部署/资源 |
|---|---|---|---|---|
| Snapshot Publisher | 校验并原子发布不可变数据快照 | 源 Warehouse | 新快照与 `current` 指针 | 协议和 CLI 已实现；测试 staging 已实证，生产发布周期尚未启用 |
| Live Core | 策略决策、组合状态与交易风控 | 生产 Warehouse、Redis 行情/回报 | 自有状态库、订单 Stream | 独立 systemd unit；代码仍在共享 Linux 发布环境 |
| Market Gateway | 从 QMT 读取行情并响应报价请求 | QMT 行情 | Redis 行情与心跳 | Windows 独立应用/任务；无下单权限 |
| Execution Agent | 委托执行、回报、账户快照与本地熔断 | 订单 Stream、QMT 账户 | QMT 委托、回报 Stream、自有 ledger | Windows 独立应用/任务；无策略逻辑 |
| Web / Job API | 监控、查询、创建/取消研究作业 | 状态库、产物、快照元数据 | 作业 inbox/control | 不执行 Runner worker；生产需显式配置后才开放写路由 |
| Research Runner | 按 manifest 执行 allowlist 研究作业 | 指定 wheel、指定快照 | 专属作业目录 | 独立用户/release；Memory/CPU/IO/并发/超时硬限制；生产激活冻结 |
| Health Observer | 跨服务只读探测与告警 | 心跳、状态和运行记录 | 自有健康时刻轴 | 独立常驻服务；故障不影响交易主链 |

关键规则：

1. **复用代码，不共享高风险进程。** 交易规则、费用、数据访问和策略接口继续来自同一
   `qsys` 包；Web 与 Research Runner 分进程、分身份运行。尚未迁移的 Linux 单元仍共享
   checkout/环境，不能把目标原则误写成已经完成的事实。
2. **数据通过版本传递。** 作业接收 `snapshot_id`，而不是模糊的 `warehouse/current` 路径；
   manifest 至少包含完整日期、schema 版本、分区清单和内容 hash。
3. **每个写入只有一个所有者。** 快照、交易状态、执行 ledger、研究产物和作业控制库分别
   由对应服务写入，其他服务只读。
4. **跨故障域用稳定契约。** 跨节点继续使用版本化 Redis 消息；跨进程数据使用 snapshot
   manifest 和 job manifest；进程内模块继续使用 Python 类型，不为拆分而引入网络调用。
5. **发布逐步独立且版本可追溯。** 已迁移组件按独立制品升级和回滚；未迁移组件仍走共享
   Linux 发布路径。两者都记录完整 commit SHA、tree、配置指纹和部署证据。
6. **研究权限默认拒绝。** Runner 不持有券商账号、Redis 订单 Stream 凭据、生产状态库写权
   或 Warehouse 写权；资源超限只能终止自己的作业。

### #285 已交付能力

1. snapshot manifest、不可变目录、全文件 hash、原子 `current` 发布和回滚协议；
2. Web/Runner 窄作业协议、固定身份的 CI wheel、allowlist task 和首个真实
   `structural-backtest`；
3. 独立账号、只读 bind mount、生产路径/状态/Redis/IPv4 默认拒绝；
4. systemd/cgroup 的 MemoryHigh/MemoryMax/无 swap、CPU、IO、优先级、超时和单并发限制；
5. 原子状态机、取消与重启恢复、日志、产物 manifest、资源峰值和失败原因审计；
6. 测试节点两次完整快照一致性、CPU/IO 压力、真实 cgroup OOM 和 Live Core/Test Web
   连续性验收。

生产 Runner 仍遵循单独的激活批准与维护窗口，不因 #285 关闭而自动安装、enable 或启动。

快照协议见 [`docs/reference/warehouse-snapshots.md`](docs/reference/warehouse-snapshots.md)，
Web/Runner 作业边界见 [`docs/reference/research-runner.md`](docs/reference/research-runner.md)。

## 代码结构

```text
src/qsys/
├── core/       与市场和数据源无关的类型、时钟、事件、组合与约束
├── data/       数据访问、存储、目录、采集流水线与质量规则
├── markets/    A 股市场规则
├── strategy/   策略接口、参数、调度和结构化策略实现
├── backtest/   事件驱动回测、撮合、费用、滑点、指标和产物
├── paper/      模拟交易运行时与报价客户端
├── live/       实盘/影子运行时、消息、风控、状态与 Redis 传输
├── agent/      Windows QMT 执行代理
├── research/   研究数据集、模型与归因逻辑
├── web/        FastAPI 监控台和工作台
└── ops/        健康、告警、运行记录、日报和部署辅助

scripts/        各运行单元入口、数据作业、研究与运维命令
strategies/     可加载的策略入口
deploy/         机器可读部署契约、systemd unit 与跨节点发布控制器
docs/           设计、数据口径、研究证据、部署和运行手册
tests/          单元、集成、契约与回归测试
```

依赖方向原则：`core` 不依赖具体数据源或券商；策略通过显式上下文访问数据、组合和订单；
Windows agent 不依赖策略；Web 只编排和展示，不承载交易业务规则。

## 数据与状态

- 生产数据仓库位于数据盘，主要使用 Parquet 分区；DuckDB/Polars 用于查询和计算。
- 实时请求、命令、回报、状态与心跳通过 Redis Streams/keys 传递。
- live/shadow、执行代理、Web 工作台和健康观察各自使用独立 SQLite 状态库。
- 数据写任务通过全局 warehouse 写锁串行；Research Runner 读取显式不可变快照。Live Core、
  Web 和尚未迁移的数据消费者当前仍读取生产 Warehouse，不能假设全系统已切到 snapshot。
- `daily-archive`、`premarket-limits` 与 `tushare-sync` 已具备独立 exact-SHA 制品边界；
  生产账号、固定入口和首次激活仍需分别授权，不能把代码合入视为现场切换。
- 实时交易事实不能被研究结果覆盖；研究产物进入独立目录并标注代码、数据和参数版本。

## 环境与发布

| 环境 | 用途 | 约束 |
|---|---|---|
| Mac | 开发、本地测试和小规模研究 | 不承担 24×7 生产职责 |
| CI Linux | 普通 Actions policy、扫描、测试与构建 | 生产机上的 rootless 单任务容器；独立 UID/cgroup/网络，无生产挂载或部署凭据；晚间高水位强制停机 |
| Test Linux | `test` 分支自动部署后的集成验证 | 禁止下单；与生产 Redis、状态和路径隔离 |
| Production Linux | 数据任务、Web、策略核心与运维；预留隔离 Runner | 生产发布必须从 `main` 手工触发；Runner 还需独立激活批准 |
| Production Windows | miniQMT、行情网关、执行代理与 watchdog | 无策略逻辑；应用独立原子发布和回滚 |

`deploy/deployment.toml` 是服务影响范围和部署边界的机器可读事实来源，但不是“所有组件
都有独立制品”的声明。CI 对完整 SHA 执行策略检查、secret 扫描、全量测试和构建；测试部署
成功后，生产发布仍需显式人工门禁。独立组件发布的当前白名单与证据链见
[`docs/reference/deployment-contract.md`](docs/reference/deployment-contract.md)。

## 本地开发

要求 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync --locked
uv run pytest
uv build
```

运行命令默认不得视为生产授权。需要接入真实数据或节点时，使用本地私有配置，并先阅读相应
部署手册；不要把 token、账号、密码、私钥或完整生产配置写入仓库和日志。

## 文档入口

- [系统原始设计](docs/superpowers/specs/2026-09-06-personal-quant-system-design.md)
- [部署契约](docs/reference/deployment-contract.md)
- [独立 self-hosted CI Runner](docs/deploy/self-hosted-ci-runner.md)
- [Warehouse 不可变快照](docs/reference/warehouse-snapshots.md)
- [Research Runner 作业与隔离协议](docs/reference/research-runner.md)
- [服务管理与每日质检](docs/reference/service-management.md)
- [策略数据依赖](docs/reference/strategy-data-dependencies.md)
- [实盘运行手册](docs/deploy/live-trading.md)
- [Linux 节点手册](docs/deploy/linux-core.md)
- [Windows 节点手册](docs/deploy/windows-node.md)
- [生产发布清单](docs/deploy/production-release-checklist.md)
- [文档索引](docs/reference/README.md)

项目实时状态、验收证据和后续工作以 GitHub Issues、Actions、部署记录及节点健康检查为准；
历史设计文档用于解释决策，不应覆盖当前机器可读契约和现场事实。
