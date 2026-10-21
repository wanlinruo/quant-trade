# 运行单元分层验收矩阵

快照：2026-10-21 20:05（Asia/Shanghai）。当前生产与历史证据以 #330 和各子 Issue
最新记录为准；写操作前重新核验。**8/14 独立生产激活，不能表述为8个组件完整回滚验收通过。**

**维护者 2026-10-20 裁决：独立组件只前进不回退**，异常直接修复发布，legacy 回切不作为生产回滚
手段，只作一次性预生产验证（AGENTS.md §4）。据此 #453 不建生产回切入口、不做生产 legacy 现场切换。
#453 一次性预生产验证 [39437976513](https://github.com/wanlinruo/quant-trade/actions/runs/39437976513)（main `c784c7a`）：5 个组件以当前生产 SHA 的真实制品、目标 SHA 自带
安装器与生产 entrypoint `_rollback_component()`，按生产白名单路径完成 激活 → legacy → 注入
daemon-reload 失败后安装器自动恢复 legacy → 重新激活，有效身份、release 与 state 标记均保留。
托管 runner 不证明生产账号/ACL/真实数据；web/worker 只验证 `--help` 级可执行，不含业务作业。
previous-release 不为验收造版本，待自然发布覆盖。#454已按维护者裁决关闭（not planned），
不再将生产legacy回切/故障注入列为完成独立化的门槛；组件自带失败补偿保持。

| 组件 | 代码/制品 | 独立预生产 | 生产激活/自然运行 | 隔离与主要剩余项 |
|---|---|---|---|---|
| catalog-verify | #469/PR#472完成 | 最终制品、真实10-21输入、专属账号隔离实跑636条/0阻断/28警告；峰值1.7G | current `3e40c21`，正式三方success；首次新版本自然任务待10-22 19:40 | 专属账号/最小配置/五处写集合已生效；#473缓存权限失败证据保留，未放宽门禁 |
| service-checks | #372/#418/#465完成 | 真实制品/账号隔离试跑见#418 | current `b48e84b`（#465）；10-20自然28项无失败；10-21仅定点存储检查 | 预生产机制验证通过（#453）；新版本当晚自然运行另行观察，不把定点检查当全量验收 |
| storage-report | #375/#465完成 | #414真实双制品试点 | current `b48e84b`；10-21实际账号正式CLI盘点35项/7到期/未登记0 | 原147项经明确批准清理146项，保留项由#467承接；未新增生产回切/故障注入 |
| daily-digest | #378/#422完成 | 真实制品print/隔离输出见#422 | current `3c182a7`；10-20 20:30自然记录succeeded | 回切机制预生产验证通过（#453 [39437976513](https://github.com/wanlinruo/quant-trade/actions/runs/39437976513)）；previous待自然第二版本 |
| health-observer | #381/#422完成 | 真实制品/账号试跑见#422 | current `dfd65bf`、active；健康历史被当前Web/日报读取 | 回切机制预生产验证通过（#453 [39437976513](https://github.com/wanlinruo/quant-trade/actions/runs/39437976513)）；不新增生产故障注入 |
| backtest-worker | #393/#422完成 | 隔离真实制品作业succeeded、报告存在 | current `3c182a7`、active；新旧Web无双消费者，当前健康观测healthy | 无legacy路径（基础unit即独立release），失败补偿预生产验证通过（#453 [39437976513](https://github.com/wanlinruo/quant-trade/actions/runs/39437976513)）；生产队列业务作业不在本轮 |
| web | #396/#422及后续修复 | 真实制品隔离端口/只读验证 | current `34f9cf6`（#463）；#442/#433/#448/#449/#283/#460生产验收通过 | 回切机制预生产验证通过（#453 [39437976513](https://github.com/wanlinruo/quant-trade/actions/runs/39437976513)，legacy unit 用 stub 代表节点文件） |
| daily-archive | 完成 | 待 | 仍共享；10-20自然作业succeeded | 独立迁移未执行，不能把数据恢复当迁移完成 |
| premarket-limits | #387完成 | 待 | 仍共享 | 独立迁移待 |
| tushare-sync | #390完成 | 待 | 仍共享 | 独立迁移待 |
| paper | #399完成 | 待 | 仍共享 | 独立迁移待 |
| shadow-core | #399完成 | 待 | 仍共享；live模式下inactive | 独立迁移待 |
| limit-up-shadow | #404/#463完成 | 真实制品评分/历史精确比较、账号与ACL探针通过 | current `34f9cf6`；10-21 19:35自然成功，19:45报告通过 | #463已关闭；新旧库事实保留，Web/日报读取新榜单与记录、无重复且只读 |
| reboot-drill | #408/#410完成 | 仅mock不计真实演练 | 仍共享；#444参数修复实际dry-run交易日分支成功 | 独立迁移、休市月度基线/真实恢复未验收；真实故障逐次审批 |

### 本轮只读复核

- 实际Web和日报账号/当前制品各读到25条唯一记录（10-19..10-20），重复identity为0，
  包含今晚catalog/checks/日报自然记录；健康读取器各见20个服务，worker观测healthy。
- Web/digest/checks/observer可读各自最小配置，均不能读完整配置；Warehouse/Live状态只读。
  Workbench仅Web/worker可写，observer仅其健康库可写；worker不需要部署配置文件。
- 最小配置选中字段核对：日报/observer与源一致；Web的Workbench绝对路径和源相对路径
  解析到同一目录；checks显式orders_enabled=false是只读组件隔离，mode与源一致。
  不为“同步”打开checks的订单能力，也不在文档记录配置值或秘密。
- 20:45节点采证已生成：归档/日报业务记录succeeded，catalog/checks为warning，
  相关页面HTTP200、无启动失败banner。自然任务退出成功不等于无业务警告。
- 常驻peer身份不变、failed units=0；共享checkout `98fff8b`，交易控制和1万元上限未改。

历史审计：`/data/qsys/var-deployments/service-checks-20261018/`、
`/data/qsys/var-deployments/issue330-batch-20261018/`、
`/data/qsys/var-deployments/issue440-20261020/natural-timer-20261020-2045.json`。
本轮消费者/权限复核见 #418/#422 结案记录；#453 已按裁决收口，#454按裁决关闭，未执行维度不伪装为已验收。

以下单独跟踪，不混入 14 个组件的计数：

| 工作域 | 证据/现状 | 剩余项 |
|---|---|---|
| Research Runner | #285 已有 test staging/压力证据 | 生产激活、快照周期与权限、消费者迁移 |
| auction/Gate 月度研究 | #406 复用 Runner，代码完成 | 同区间结果、全量资源/输入和实际调度迁移 |
| Live Core | 主要业务组件尚未独立改造 | 最后迁移；协议、订单/成交/心跳连续、失败补偿与向前修复 |
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
