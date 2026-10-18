# 运行单元分层验收矩阵

快照：2026-10-18，开始 #414 试点时（代码基准 `c1a1b27`）。这是证据清单，
不是现场状态承诺；最新执行结果以 #414/#330 的记录为准，生产操作前重新盘点。
“代码完成”仅表示入口、组件规格和 CI 制品完成，不表示账号已创建或生产已切换。

| 组件 | 代码/制品 | 独立预生产运行与回滚 | 生产激活/回滚证据 | 主要剩余项 |
|---|---|---|---|---|
| catalog-verify | 完成 | 未单列独立预生产证据 | #346 已有生产发布/实跑记录；不可将框架 mock 当逐组件故障注入证据 | 核对当前现场与完整回滚验收 |
| service-checks | #372 完成 | 待 | 待 | 只读配置/Redis、账号、记录/告警读者权限 |
| storage-report | #375 完成 | 本轮 #414 试点；成功记录前仍算待 | 待 | v12→v26 控制面、专用账号、历史目录、timer、首次回切 |
| daily-digest | #378 完成 | 待 | 待 | 跨组件报告输入、读取权限与新 namespace |
| health-observer | #381 完成 | 待 | 待 | 独立状态、最小 Redis/Windows 只读权限 |
| daily-archive | 完成 | 待 | 待 | Warehouse 单写锁、专属暂存/记录、Windows 下载权限 |
| premarket-limits | #387 完成 | 待 | 待 | Warehouse 单写锁、配置权限、任务验真 |
| tushare-sync | #390 完成 | 待 | 待 | 写锁、晚到标的补采链、独立 Windows key |
| backtest-worker | #393 完成 | 待 | 待 | Workbench 共享权限、队列领取/取消/恢复与 Web 联合切换 |
| web | #396 完成 | 待；整体 Web test 部署不能替代独立制品验收 | 待 | 旧较晚 drop-in 冲突、Workbench/worker、只读研究/榜单路径 |
| paper | #399 完成 | 待 | 待 | Workbench/协议、账号及与 Web/worker 的联合权限 |
| shadow-core | #399 完成 | 待 | 待 | 独立状态、Redis ACL、急停与历史连续 |
| limit-up-shadow | #404 完成 | 待 | 待 | 非空 SQLite 完整迁移、暂停 timer、Web 只读消费者联动 |
| reboot-drill | #408/#410 完成 | 待；仅 mock 不计真实运行 | 待 | 专用 SSH/known_hosts、只允许基线的 unit、逐次演练审批 |

14 个规格中，13 个尚无生产独立迁移验收记录；catalog-verify 有生产记录，不代表其所有
独立预生产和故障注入维度都已完成。不能用“3069 项测试通过”填满各组件运行/回滚格子。

以下单独跟踪，不混入 14 个组件的计数：

| 工作域 | 证据/现状 | 剩余项 |
|---|---|---|
| Research Runner | #285 已有 test staging/压力证据 | 生产激活、快照周期与权限、消费者迁移 |
| auction/Gate 月度研究 | #406 复用 Runner，代码完成 | 同区间结果、全量资源/输入和实际调度迁移 |
| Live Core | 主要业务组件尚未独立改造 | 最后迁移；协议、订单/成交/心跳连续与即时回切 |
| systemd/platform | #412 已防止已知 drop-in 覆盖 | 更早累加指令、site 配置、平台/组件独立回滚与现场验收 |
| 组件级预生产 | #414 先做 storage-report 单组件试点 | 实际证据成功后再推广，保留整体集成测试 |
| Windows / 物理故障域 | 独立工作域 | 协议兼容矩阵与 #138 容量评估；不将容器当独立内核 |

## 本轮验收口径

#414 使用一次性 GitHub-hosted Linux，消费两个不同的、已合并 main 且 test CI 成功的
真实 storage-report 制品。各自 lockfile/venv、专用 Linux 用户、systemd oneshot、只读测试
夹具和专属写目录；执行基线、升级、回切、候选失败、恢复、写入拒绝，并核对旁路服务身份。
证据绑定 source/tree/artifact/wheel、报告、历史连续性和各阶段返回码。

该试点验证组件制品和运行边界，以及试点编排的 current 回切，不冒充生产 restricted gateway /
installer 已实际切换。它不连接生产主机、SSH、Redis 或市场数据，不证明生产盘容量或完整目录
扫描性能；生产账号/ACL、原历史、实际 timer 与现场回滚仍须独立授权验收。
