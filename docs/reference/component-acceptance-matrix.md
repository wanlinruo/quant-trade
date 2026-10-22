# 运行单元分层验收矩阵

快照：2026-10-22 20:12（Asia/Shanghai）。当前生产与历史证据以 #330 和各子 Issue
最新记录为准；写操作前重新核验。**15/15个manifest目标已独立生产部署，Live Core已迁移；Shadow保持停用、reboot-drill保持dry-run，#330尚未整体完成。**

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
| catalog-verify | #469/#479完成 | 真实制品/私有账号隔离636条、0阻断/28警告；峰值1.7G | current `80f7068`、三方success；19:40自然因缺241行失败，获批补齐后20:06实际复验exit0/10绿2黄0红灰 | #469/#479关闭；新旧消费者/原历史/冻结基线通过，原失败证据保留 |
| service-checks | #372/#418/#465完成 | 真实制品/账号隔离试跑见#418 | current `06cb7c1`（#497消费者先行）；10-20自然28项无失败；10-21仅定点存储检查 | 预生产机制验证通过（#453）；新版本当晚自然运行另行观察，不把定点检查当全量验收 |
| storage-report | #375/#465完成 | #414真实双制品试点 | current `b48e84b`；10-21实际账号正式CLI盘点35项/7到期/未登记0 | 原147项经明确批准清理146项，保留项由#467承接；未新增生产回切/故障注入 |
| daily-digest | #490必要读取器先行 | 实际账号可读旧flat及paper/shadow告警；reboot新旧真实记录2条唯一且只读 | current `06cb7c1`、三方success；当前可读Paper自然投递账及Live私有状态 | 外部邮件/收件不由只读验收代替，未手工发送测试通知 |
| health-observer | #381/#422完成 | 真实制品/账号试跑见#422 | current `06cb7c1`、active；健康历史被当前Web/日报读取 | 回切机制预生产验证通过（#453 [39437976513](https://github.com/wanlinruo/quant-trade/actions/runs/39437976513)）；不新增生产故障注入 |
| backtest-worker | #393/#422完成 | 隔离真实制品作业succeeded、报告存在 | current `3c182a7`、active；新旧Web无双消费者，当前健康观测healthy | 无legacy路径（基础unit即独立release），失败补偿预生产验证通过（#453 [39437976513](https://github.com/wanlinruo/quant-trade/actions/runs/39437976513)）；生产队列业务作业不在本轮 |
| web | #396/#422及后续修复 | 真实制品隔离端口/只读验证 | current `b760b5f`、三方success；#492私有Unix身份/Workbench真实ACL与关键页面通过 | runs全量47目录/769文件可读不可写；worker/Paper写者保留；Windows只读key缺失#511 |
| daily-archive | #485完成 | 私有账号/精确写表/原历史hash与真实副本/当前消费者通过 | current `b760b5f`、三方success；10-22自然6步骤/当时1341933行对账及18:15审计通过 | #485关闭；#508复用SSH已发布，20:08只读握手仍reset，稳定性未宣称解决 |
| premarket-limits | #487完成并关闭 | 限权Redis发布入口、实际unit直连拒绝、隔离直取与消费者通过 | current `37bb3c4`、三方success；08:58/09:15/09:24自然均成功，09:35采证通过 | 今日5655条；QMT mismatched0/missing41如实保留，Redis全局残余#488 |
| tushare-sync | #475完成、#476取数限权已关闭 | 私有账号、Windows专属gateway、真实19步骤及当前消费者通过 | current `b760b5f`、三方success；#508有限重试/连接复用已发布，实际两次只读probe通过 | 17:00/19:00自然失败保留；#512获明确批准补齐241条并复验通过；#475/#508仍在进行 |
| paper | #490完成并关闭 | 真实supervisor+2workers、精确Workbench named ACL、配置/原事实与当前Web通过 | current `987175e`、三方success；两账户自然开盘/竞价各完成，09:43healthy/重启0 | 共享Workbench合法写者保留，Paper仅observers；Web多余Unix写组#492已收敛 |
| shadow-core | #493独立休眠部署完成并关闭 | 最终制品/私有UID、真实血缘初始化/心跳、空仓私有state/journal及当前消费者显式阅读通过 | current `1e7fd0e`（#499）、三方success；inactive/enabled/live/true保持 | 不等于业务激活；未来启用及生产消费者切读另验，旧state/kill保持 |
| limit-up-shadow | #463/#479完成并关闭 | 真实制品评分/历史精确比较、账号与ACL探针通过 | current `80f7068`；10-21自然及19:45消费者通过，#479权限修复三方success | 新旧事实保留、当前Web/日报可见且不重复 |
| reboot-drill | #495独立部署完成 | 实际专属key只读基线/拒绝shell与额外命令、私有UID隔离dry-run/当前消费者通过 | current `57562f4`、三方success；实际生产入口交易日拒绝重启、timer已恢复 | #495保持validation：10-23 10:00自然/10:05采证待；未实际重启，不代替灾备验收 |
| live-core | #497/#499完成并关闭 | 私有真实Runtime/当前消费者只读；hosted39739215169真实安装器补偿/部分成交连续性通过 | current `c741c5f`，正式39754118067/Deployment7248389752/实际validator三方success；PID3385058，16/16严格检查 | 最新私有state自动切读；63事件/63策略/2历史成交逐行保留，原库及marker保留；自然费用仍#137，目录前置检查#506已发布并关闭 |

### 10-22迁移结论

- 15个current按实际manifest路径逐项有效，catalog保留既有独立根目录；
  最新清单在`/data/qsys/var-deployments/issue497-20261022/all-component-currents.json`。
- Live Core/Web/Paper/worker身份在Shadow与reboot发布前后保持，Shadow未启动，交易开关/资金未改。
- #485自然验收关闭；#469自然失败后获批修复并人工复验关闭；#475/#508稳定性、#495明日自然timer仍待。
- #497/#499/#506/#492/#512已收口；#488 Redis5强制权限、#511缺key、#482通知语义及上述在途项保留，不以15/15计数关闭#330。

### 10-21历史只读复核

- 实际Web和日报账号/当前制品各读到25条唯一记录（10-19..10-20），重复identity为0，
  包含今晚catalog/checks/日报自然记录；健康读取器各见20个服务，worker观测healthy。
- Web/digest/checks/observer可读各自最小配置，均不能读完整配置；Warehouse/Live状态只读。
  该次快照Workbench仅Web/worker可写（10-22新增Paper精确named ACL，见#490），observer仅其健康库可写；worker不需要部署配置文件。
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

以下工作域单独跟踪：

| 工作域 | 证据/现状 | 剩余项 |
|---|---|---|
| Research Runner | #285 已有 test staging/压力证据 | 生产激活、快照周期与权限、消费者迁移 |
| auction/Gate 月度研究 | #406 复用 Runner，代码完成 | 同区间结果、全量资源/输入和实际调度迁移 |
| Live自然交易 | #497独立迁移已完成，计入15组件 | #137自然首笔费用/交易观察，不能由空仓初始化代替 |
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
