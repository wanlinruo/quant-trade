# 项目交接入口

> 快照时间：2026-10-22 17:30（Asia/Shanghai）。这是接手入口，不替代 GitHub、节点和机器可读
> 契约的实时状态。执行任何写操作前必须重新核验。

## 先读什么

按以下顺序建立上下文：

1. GitHub 未关闭 Issue 列表，重点看各 Issue 的最新评论；`status:*` 标签就是当前阶段；
2. #137（实盘上线清单）、#330（独立制品与发布总跟踪）、#127（数据治理总跟踪）；
3. `docs/deploy/production-linux-release.md`：生产 Linux 发布规则，以及 10-19 归档修复的发布顺序；
4. `docs/reference/deployment-contract.md`、`deploy/deployment.toml`、`deploy/component-releases.json`：
   机器可读的部署边界与独立组件清单；
5. `.github/workflows/deploy-production-linux.yml`、`deploy-production-component.yml`：实际的 GitHub 门禁；
6. `deploy/linux/qsys-production-deploy-entrypoint.py`：节点最终执行与代码回滚逻辑；
7. `docs/deploy/linux-core.md`、`docs/deploy/windows-node.md`：节点实况和约束。

发生冲突时，优先级是：维护者最新明确指示 → GitHub Issue 最新记录 → 当前 workflow/
节点入口和 manifest → 本快照 → 其他历史文档。不得用旧文档覆盖维护者的新要求。

## 10-22 17:30最新交接（覆盖下文10:52历史快照）

- **#497/#499已关闭，15/15 manifest组件已独立生产部署**；Shadow保持inactive/enabled/live/true，
  reboot-drill保持dry-run，不能称全部业务激活或#330完成。逐项current清单：
  `/data/qsys/var-deployments/issue497-20261022/all-component-currents.json`。
- main/test与生产共享基线`c741c5f7871eef01ab4a01bbce9325ec84ee085d`，固定入口v31。
  Live current同SHA，User/Group=qsys-live-core（UID976），PID3385058/NRestarts0，包内入口，
  WorkingDirectory=/；仍1万元/live/true，严格preflight16/16。Web/digest/observer/checks current
  `06cb7c1`；Shadow current`1e7fd0e`；其余current见清单，不用旧列表覆盖它。
- Live重试dry39753936011、正式39754118067、Deployment7248389752及实际record validator
  三方success。第一次39753509764因缺空live_rejects目录失败，自动补偿到共享入口；失败记录保留。
  #504直接脚本导入修复已通过PR#505及正式39752058392发布；#506承接目录检查提前到停旧前。
- 稳定marker `/etc/qsys/components/live-core/state-binding-v1` **已激活并必须保留**。最新state为
  `/home/ubuntu/qsys/var/live-core/var/live-state.sqlite3`，kill/lock/workspace也在该私有根。
  共享补偿入口同读最新私有状态，**不得移除marker或复制旧库覆盖新事实**。原库未移动或改symlink，
  与停旧一致性备份逐值相等；新库保留63事件/63策略记录/2历史成交，历史行无丢失/改写。
- 四个真实生产消费者已自动切读新库/kill，各自账号对主库及父目录只读，9 sessions/0pending。
  Live仅四张journal目录可写；完整配置不可读，旧state/Workbench/runs/日线不可写。
  证据`final-consumers.json`、`final-state-continuity.json`在issue497审计根。
- 分层证据：hosted真实安装器/代码补偿/部分成交连续性39739215169通过；生产第一次真实失败补偿
  与重试成功分别记录，不能把失败workflow写成成功。没有生产故障注入或重复交易实例探针。
- **#475 17:00自然任务失败**：17:02–17:05更新主数据/派生后，301718.SZ分钟补采Windows SSH
  握手被reset（rc255），收敛入口exit1。发生于Live切换之前，不归因于Live部署；失败journal保留，
  不reset-failed、不造分钟。19:00自然重试/19:15采证仍按既有timer，未手动恢复数据。
- #485今日15:40自然任务/18:15审计、#469 19:40/19:50、#495明日10:00/10:05仍须据实际证据
  收口；#488 Redis强制权限、#492 Web Unix权限、#482维护/异常通知区分继续保留。
  #137首笔自然费用仍待。#330以这些实际剩余项推进，不因15/15计数关闭。
- 本轮普通发布明确授权仍有效，不扩展到交易开关、Windows重启、生产故障注入或删除备份。

## 10:52历史快照（以上述最新交接为准）

### 生产版本

- 共享checkout：`98fff8b837a08b21acb1ff9826a154a50df704e4`（#444），干净；固定入口v30。
- 独立Web current：`34f9cf647926153982178de69fddb803c93653ac`（PR#471，#463），既有回退副本保留。
- Windows live_app（执行代理）与paper_app（行情网关）仍为`43f3f81`（#283）；本轮只新增
  专属取数/只读基线forced-command入口，未重启QMT/代理、未改任务XML/登录与恢复配置。
- catalog/limit-up-shadow `80f7068`、observer `dfd65bf`，checks/storage `b48e84b`，worker `3c182a7`；
  digest/paper `987175e`，tushare-sync `2e89e66`，daily-archive `e005bab`，premarket-limits `37bb3c4`，
  shadow-core `6e4393a`，reboot-drill `57562f4`。Web仍`34f9cf6`。
- **14/14个manifest目标已有独立制品与生产单元**；Shadow仍inactive/enabled/live/true，
  reboot-drill仍--dry-run，不能称全部业务激活或#330整体完成。catalog的既有独立根目录为
  `/opt/qsys/catalog-verify/`，其余在`/opt/qsys/components/`，计数以manifest为准，不只glob后者。
- 最新现场current清单：`/data/qsys/var-deployments/issue495-20261022/all-component-currents.json`。
  分层验收见`docs/reference/component-acceptance-matrix.md`与#330最新记录，所有历史副本保留。

### 实盘（#137）

- 仍为1万元实盘上限，未改资金/交易开关；Live Core PID1748883，启动于10-19 18:21:47，
  本轮所有组件发布及#444共享发布均未重启它。组件发布前后严格preflight通过。
- 10-20 09:25:10竞价事件completed，09:28只读采证无错误；首笔自然股票费用/入场成本仍待，
  #137保持monitoring，不用零成交代替费用验收。
- #433停机闸只读状态与布局已发布；三处均有效解除才显示解除，过期/异常立即未知。
  来源默认折叠、两级导航紧邻。#448服务页延迟、#449窄屏SVG溢出均已发布验收关闭。
- #444实际安装unit显式--dry-run，真实交易日分支exit0、拒绝重启Windows；timer恢复正常，
  failed units=0，旧失败journal保留。实际重启演练仍未授权。

### 本轮迁移收尾与未决项

- #463/#476/#479/#487/#490/#493已关闭；盘前10-22 08:58/09:15/09:24三次自然任务成功，
  09:35采证通过。5655条今日限价直取，QMT校验mismatched0/missing41如实保留，不称全覆盖。
- Paper #490与必要日报读取器联合发布均为`987175e`，各自三方success；Paper实际私有UID、
  supervisor+2workers，MainPID3011011、重启0。10-22两个运行账户开盘/竞价各2项completed，
  09:43采证healthy、Web200、日报可读2条真实投递账；不能据此代替维护者外部收件验收。
  原账户/订单/成交事实保留；Workbench保留原owner，Paper精确named ACL，没有加入workbench组。
- #493 Shadow正式39704790276/Deployment7241246964与7241250075/实际validate_record三方success。
  独立私有身份/配置/状态已生效，仍inactive/enabled/live/true；旧状态/kill/其他peer保持。
  隔离真实初始化与当前消费者显式新路径只读通过，不等于生产启动/自然策略事件或切读路径。
  未来启用需另行明确授权，并联合切换实际生产消费者，不能为验收改成shadow/false。
- #495 reboot-drill正式39712705553/Deployment7242347709与7242350244/实际validate_record三方success。
  实际私有unit保持--dry-run/readonly-baseline；Windows专属key可读基线、shell/SFTP/额外命令拒绝。
  原授权/ACL/boot/关键进程/任务XML保持，未执行重启或外发测试通知。隔离真实基线、Web/日报
  新旧记录只读与实际生产入口交易日门禁通过；**timer已恢复active/enabled**，原定义不变。
  status:validation：10-23 10:00首次新版本自然timer待，10:05节点只读采证已安排。
- #485 daily-archive current`e005bab`，正式39642942325三方success；6步骤真实任务成功，
  7天质检0阻断、canonical9,387,766行逐键逐字段对账及当前消费者通过；10-22 15:40自然任务待，
  18:15只读采证已安排，不能提前关闭。
- #475 tushare-sync current`2e89e66`，真实19步骤/限权Windows取数/当前消费者通过；
  10-22 17:00/19:00自然任务及19:15采证待。#469 catalog当前`80f7068`，19:40任务/19:50采证待。
- 下一主项 **#497 Live Core最后独立迁移**：生产仍共享`98fff8b`/Ubuntu，PID1748883保持。
  当前只做方案/依赖与隔离准备，不在交易时段顺手重启；先联查Web/日报/observer/checks的
  live-state及kill/preflight/共享journal写者，停止旧生产者后再取最新一致性状态，防止漏订单/成交。
  现有14组件通道要求Live身份不变，不能关闭全局peer保护或借其他组件发布绕过它。
- #488：Redis5经无密码loopback隧道访问、无ACL，代码只GET不等于强制只读；不擅自升级/重启Redis。
  #492：Web既有Unix workbench组还可写var/runs，unit沙箱阻断不等于Unix权限收敛，待单独修复。
  #482运维通知维护/失败区分仍未实现，不宣称全部异常告警根因已解决。
- #485/#487/#490/#493/#495证据根为`/data/qsys/var-deployments/issue<编号>-20261022/`；
  #473首次缓存权限失败、#495首次timer门禁拒绝等证据均保留。新失败先摘要/定点修复，未变CI不重复跑。

### 数据恢复历史证据（10-20 01:24快照，当前状态以上文为准）

- 10-19 归档恢复：#434 运行目录、#438 退市日、#440 未收盘候选问题均已修复并发布。
  10-20 01:13:47–01:16:47 手动归档成功，按最近已收盘日 10-19 执行；竞价候选 195 组、
  分钟 33,381 组全部已齐，6 个交易日跨源校验 0 阻断。canonical 10-19 已发布；没有请求或
  写入未收盘的 10-20 数据。生产恢复前保留 20 个分区备份及 SHA-256，审计目录
  `/data/qsys/var-deployments/issue440-20261020/`，代码回滚点 `c328b8c`，数据不随代码反向恢复。
  本次仅发布 linux-data-jobs，Live Core PID 1748883 / 启动时间 10-19 18:21:47 保持不变。
- canonical 10-19 与原始分钟线均为 **1,340,844 行 / 5,564 只**，重复键 0；写后对账
  source_only / target_only / mismatched 均为 0。旧 canonical 分区、原始竞价和分钟文件 hash
  未变，20 份备份完整。已知 D-23 未结，不切换回测分钟源。
- catalog-verify 01:17:22–01:18:22 成功，无 OOM：636 条字段结果、阻断 0、既有警告 28；
  支持的 12 张规范表为 **10 绿 / 2 黄 / 0 红 / 0 灰**，7 条关系全绿。
  黄项如实保留：名称区间 246 个已知问题；分钟 8 个非 240/241 根标的日。
  其他未覆盖表仍可灰色，不能将本结果表述为全仓库已验收。
- service-checks 按 10-19 业务日回看：01:18:44–01:19:29，28 项、数据失败 0、策略失败 1；
  catalog 两项旧失败显示 repaired，10-19 早盘 STALE_QUOTE 保留，存储保留告警保留。
  使用临时最小配置禁用额外告警发送，随后删除临时配置；归档与质检 runtime drop-in 均已移除，
  实际 unit 与执行前逐字比较一致。没有 reset-failed 或改写历史。
- 当前独立 Web 与日报的实际账号/venv 均能读取 15 条新旧记录，identity 无重复；Web 归档
  详情已消除启动失败 banner，服务/实盘/回测/表页均 HTTP 200。16 项实盘只读检查全通过，
  当前空仓 / 活动委托 0、三处停机闸未生效、启用资金仍 1 万元；systemd failed unit 为 0。
- **#442（P2 / in-progress）**：已实测确认持续更新的 live_equity 使全仓缓存失效，重复扫描
  约 19 亿行分钟数据。修复改为按表精确缓存、后台统计与明确的待更新/失败提示，见
  `docs/reference/data-catalog-latency.md`。本条记录代码推进状态，生产激活与实际时延以
  #442 最新发布证据为准；不能把本地测试写成生产已经解决。

### 在途 Issue

- #330仍为总跟踪：14个manifest组件已独立部署，#497承接Live Core；#485/#475/#469及#495
  保留各自自然验收，#488/#492保留残余权限，不因计数完成提前关闭总项。
- #137/#126继续自然交易费用和30日容量观察；#123/#122/#283/#138/#250按维护者排期。
- #304/#234/#230/#139/#142/#143保持各自外部/口径阻塞，不擅自购买、删数据或更改认证入口。
- 历史数据恢复#434/#435/#436/#438/#440、页面#442/#433/#448/#449、参数#444已闭环。

### 本轮授权与证据

维护者明确指示“全权授予你部署和发布”，本轮据此完成 #440 发布及 10-19 数据恢复。
发布 PR #441 由 Codex 代行逐文件自审；test CI 39291429145、测试部署 39291676091、
PR CI 39291812341、生产 dry-run 39292039046、正式发布 39292155034 均 success。
GitHub Deployment 7179993822 success，节点 `issue440-20261020-release-linux/record.json`
经实际 validator 校验成功，三方一致后才启动归档。本轮没有故障注入、删备份、改资金或交易开关。

以上 SHA 和健康状态会变化。接手时至少重新查看 Issue、Actions、远端分支和节点只读状态。

## 不可擅自跨越的授权边界

- 本轮普通部署与发布已有维护者“全权授予”明确授权，执行前仍核对精确候选、受控 workflow 与现场门禁；该权限不扩展到故障注入、交易开关或不可逆数据处置。
- 未获得维护者明确确认，不得执行生产自动回滚故障注入演练。
- 不能仅凭“继续”“按建议处理”或本交接文档推定超出现有明确授权范围的新许可。
- Linux 与 Windows 都必须保留维护者的密码登录能力。
- 不得关闭 `PasswordAuthentication`，不得删除个人 SSH key，也不得更改 Windows
  Administrator 密码、RDP、Sysinternals Autologon 或 QMT 自动恢复配置。
- 不得把密码、私钥、token、完整配置或其他秘密写入 Git、Issue、Actions 日志或交接文档。
- 不得把代码回滚扩展成数据/schema 自动反向操作。

## 只读接手检查

这些检查不会授权后续写操作：

```bash
git status --short --branch
git fetch origin main test
git rev-parse origin/main origin/test
gh issue list --state open
gh run list --workflow deploy-production-linux.yml --limit 5
```

Linux 节点只读检查应确认生产 SHA、工作区、监控、health 和密码认证状态；不要在检查命令中
打印配置内容或任何 secret。预期快照是：共享生产 `98fff8b`、独立Web `34f9cf6`、入口 v30、工作区干净、
`qsys-live-core` / `qsys-monitor.service` active、`8420/healthz` 为 200、`PasswordAuthentication=yes`。

## GitHub 与节点名称

- Workflow：`Deploy production Linux`；只允许 `workflow_dispatch`，不会因 `main` push 自动发布。
- Environment：`production`，部署分支策略仅允许 `main`。
- Variables：`PRODUCTION_DEPLOY_HOST`、`PRODUCTION_DEPLOY_USER`。
- Secrets：`PRODUCTION_DEPLOY_SSH_KEY`、`PRODUCTION_DEPLOY_KNOWN_HOSTS`。
- production forced command：`/usr/local/libexec/qsys-production-deploy-ssh`。
- production entrypoint：`/usr/local/libexec/qsys-production-deploy-entrypoint`。
- production entrypoint config：`/etc/qsys/production-deployment-entrypoint.json`。
- production records：`/data/qsys/var-deployments/github/<run-id>/record.json`。

只记录名称，不尝试读取或回显 secret。部署 key 轮换只能替换带
`qsys-github-production` 标记的授权行，不能影响其他登录方式。

## 交接完成判据

接手者应先核对：当前生产 SHA、最近发布/回滚节点记录、服务与 health、Windows 心跳，
以及密码登录保留要求。信息不一致时停止写操作，先更新 Issue。
