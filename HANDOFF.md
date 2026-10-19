# 项目交接入口

> 快照时间：2026-10-20 01:24（Asia/Shanghai）。这是接手入口，不替代 GitHub、节点和机器可读
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

- 共享 checkout：`ee7b0a34ba1e3f9ad8b84aa8f37531dae2024b9b`（PR #441），工作区干净；固定入口
  **v29**（`systemd-runtime-paths-v1`），旧版 v28 备份在
  `/data/qsys/var-deployments/issue434-436-20261019/`。
- 独立组件 current：web、health-observer、catalog-verify 为 `dfd65bf`（PR #437）；
  service-checks 为 `ac22996`；daily-digest、backtest-worker 为 `3c182a7`；storage-report 为 `5a54901`。
  paper、shadow-core、tushare-sync、daily-archive、premarket-limits、limit-up-shadow、reboot-drill
  仍跑在共享 checkout 上（没有组件 drop-in）。
- 最近一次发布证据（workflow / Deployment / 节点 record 三方 success，record 都用实际
  validator 校验过）：Linux 39292155034 / 7179993822（dry-run 39292039046）；observer
  39237136934 / 7171286699；Web 39237450942 / 7171338710；catalog-verify 39273505287 / 7177176010。

### 实盘（#137）

- 10-19 14:41 经维护者授权恢复 1 万元实盘，三处停机闸已解除。10-19 18:21 因共享发布重启了
  Live Core，带 `--require-live` 的盘前检查 16 通过 / 0 失败；之后的组件发布都没有改变它的 PID。
- 10-19 09:25 竞价事件因 `STALE_QUOTE` 失败（#426），修复已经上线；按规则不补跑，页面会如实
  显示当日"事件失败"。**10-20 09:25 是修复后第一次真实竞价**，节点已安排 09:28 只读采证
  （`qsys-issue137-auction-20261020`）。
- #433：停机闸状态在页面上不显眼，P1 / ready。

### 数据与定时任务（截至 10-20 01:24）

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
- **#442（P2 / ready）**：目录与分钟表详情仍各需约 30 秒，首次 20 秒客户端超时。
  页面实际显示上述黄项且覆盖到 10-19；此性能问题单独跟踪，不能写成已经解决。

### 在途 Issue（validation / in-progress）

- #434、#435、#436、#438、#440：本轮已完成代码、正式发布与手动数据/消费者验收，证据见 Issue。
- #418、#422：继续等待 10-20 自然交易日定时验收；回切、previous-release / 故障注入仍未执行。
  节点已安排 **10-20 20:45 只读采证**（`qsys-issue440-natural-20261020.timer`），覆盖当日
  归档、catalog、checks、日报、分区与页面，不触发业务作业。结果届时写入审计目录下
  `natural-timer-20261020-2045.json`；未到时间不得当作通过。09:28 的 #137 竞价采证仍保留。
- 需要维护者裁决才能收口的：#234、#230、#250、#139（建议关闭），#142（定口径），
  #143（涉及删改生产数据，需要明确授权）。

### 本轮授权与证据

维护者明确指示“全权授予你部署和发布”，本轮据此完成 #440 发布及 10-19 数据恢复。
发布 PR #441 由 Codex 代行逐文件自审；test CI 39291429145、测试部署 39291676091、
PR CI 39291812341、生产 dry-run 39292039046、正式发布 39292155034 均 success。
GitHub Deployment 7179993822 success，节点 `issue440-20261020-release-linux/record.json`
经实际 validator 校验成功，三方一致后才启动归档。本轮没有故障注入、删备份、改资金或交易开关。

以上 SHA 和健康状态会变化。接手时至少重新查看 Issue、Actions、远端分支和节点只读状态。

## 不可擅自跨越的授权边界

- 未获得维护者新的明确确认，不得触发 `dry_run=false`。
- 未获得维护者明确确认，不得执行生产自动回滚故障注入演练。
- 不能把“继续”“按建议处理”或本交接文档解释为新的生产变更确认。
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
打印配置内容或任何 secret。预期快照是：生产 `ee7b0a3`、入口 v29、工作区干净、
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
