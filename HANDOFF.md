# 项目交接入口

> 快照时间：2026-10-20 22:25（Asia/Shanghai）。这是接手入口，不替代 GitHub、节点和机器可读
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

## 当前状态

### 生产版本

- 共享checkout：`98fff8b837a08b21acb1ff9826a154a50df704e4`（#444），干净；固定入口v29。
- 独立Web current：`fe6d52dffdb4e45e58c450cc66f54d6aa237c60b`（PR#452）。
  catalog/observer仍`dfd65bf`，checks `ac22996`，digest/worker `3c182a7`，storage `5a54901`。
- 最近正式发布均已核对workflow/Deployment/实际validate-record三方success：
  #444 Linux39419956053/7199251758；#448 Web39421535317/7199505475；
  #449 Web39424192480/7199941830。Web回滚点2a291ef及其他回退副本均保留。
- 7/14独立激活、7/14尚未迁移；组件分层验收以`docs/reference/component-acceptance-matrix.md`
  和#330最新记录为准，不用激活数量替代完整回滚验收。

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

- **维护者10-20裁决：独立组件只前进不回退**，异常直接修复发布；legacy回切只作一次性预生产验证
  （AGENTS.md §4）。#453已按此收口：#455/#456新增`Verify component rollback preproduction`，
  run 39437976513对5个组件验证激活→legacy→注入失败自动恢复→重新激活，全部success；
  托管runner不证明生产ACL/业务作业。#418/#422随之关闭，生产故障注入仍由#454承接（blocked）。
- 未新增生产gateway命令；不得通过手工恢复控制文件做生产回切。
- 实际Web/日报账号各读到25条唯一运行记录、20个健康服务；新旧记录可见且不重复。
  最小配置保持隔离；checks显式禁用订单，Web的Workbench绝对路径与源相对路径等价。
- 10-20自然归档15:40:53–16:09:43成功，原始分钟/canonical各1,341,803行；只证明行数，
  不把该快照当逐字段对账。19:40 catalog：636条/0阻断/28警告；20:00 checks：28项/
  数据和策略未通过均0，二者业务记录warning；20:30日报succeeded。
- 20:45只读采证已实际生成：
  `/data/qsys/var-deployments/issue440-20261020/natural-timer-20261020-2045.json`。
  新历史不得删除；故障注入另由#454逐次审批。

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

- #330仍为独立化总跟踪；#453已收口关闭，#454生产故障注入blocked。
- #137/#126继续自然交易费用和30日容量观察；#123/#122/#283/#138/#250按维护者排期。
- #304/#234/#230/#139/#142/#143保持各自外部/口径阻塞，不擅自购买、删数据或更改认证入口。
- 历史数据恢复#434/#435/#436/#438/#440、页面#442/#433/#448/#449、参数#444均已闭环。

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
打印配置内容或任何 secret。预期快照是：共享生产 `98fff8b`、独立Web `fe6d52d`、入口 v29、工作区干净、
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
