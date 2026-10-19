# 项目交接入口

> 快照时间：2026-10-20 01:00（Asia/Shanghai）。这是接手入口，不替代 GitHub、节点和机器可读
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

- 共享 checkout：`c328b8c85e857bae4b35ad3ee3247afc263f00ce`（PR #439），工作区干净；固定入口
  **v29**（`systemd-runtime-paths-v1`），旧版 v28 备份在
  `/data/qsys/var-deployments/issue434-436-20261019/`。
- 独立组件 current：web、health-observer、catalog-verify 为 `dfd65bf`（PR #437）；
  service-checks 为 `ac22996`；daily-digest、backtest-worker 为 `3c182a7`；storage-report 为 `5a54901`。
  paper、shadow-core、tushare-sync、daily-archive、premarket-limits、limit-up-shadow、reboot-drill
  仍跑在共享 checkout 上（没有组件 drop-in）。
- 最近一次发布证据（workflow / Deployment / 节点 record 三方 success，record 都用实际
  validator 校验过）：Linux 39236856031 / 7171244616、39287419932 / 7179296712；observer
  39237136934 / 7171286699；Web 39237450942 / 7171338710；catalog-verify 39273505287 / 7177176010。

### 实盘（#137）

- 10-19 14:41 经维护者授权恢复 1 万元实盘，三处停机闸已解除。10-19 18:21 因共享发布重启了
  Live Core，带 `--require-live` 的盘前检查 16 通过 / 0 失败；之后的组件发布都没有改变它的 PID。
- 10-19 09:25 竞价事件因 `STALE_QUOTE` 失败（#426），修复已经上线；按规则不补跑，页面会如实
  显示当日"事件失败"。**10-20 09:25 是修复后第一次真实竞价**，节点已安排 09:28 只读采证
  （`qsys-issue137-auction-20261020`）。
- #433：停机闸状态在页面上不显眼，P1 / ready。

### 数据与定时任务（截至 10-20 01:00）

- 10-19 归档：#434 的 226/NAMESPACE 已修复，补采入库竞价 53 组、分钟 1,340,844 行，跨源校验
  0 阻断。但 canonical `bar_minute_1m` **10-19 还没发布**：19:05 因退市日空分钟失败（#438，已修复
  并发布）；00:45 重跑又碰到午夜后的候选问题（#440，P2，只在手动夜间运行时触发）。
  **预期 10-20 15:41 定时归档会同时发布 10-19 和 10-20**，需要核验。
- catalog-verify 新组件 00:47 实测：峰值 1.5G，无 OOM，指标阻断 0；表级健康 10 绿 /
  1 黄（`security_name_history` 已知问题）/ 1 红（`bar_minute_1m`，待上面的 canonical 发布）。
- 10-19 晚间 service-checks 退出码 1，反映的是真实业务失败（早盘竞价、catalog-verify OOM），
  详见 #418。daily-digest 运行成功。

### 在途 Issue（validation / in-progress）

- #434、#435、#438：等 10-20 15:41 归档成功（#435 还需要确认失败标记能恢复）。
- #436：canonical 发布后，catalog-verify 转绿并完成消费者核对。
- #418、#422：建议以 10-20 晚间的干净交易日作为最终验收，并拆分回切 / 故障注入尾项。
- 需要维护者裁决才能收口的：#234、#230、#250、#139（建议关闭），#142（定口径），
  #143（涉及删改生产数据，需要明确授权）。

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
打印配置内容或任何 secret。预期快照是：生产 `c328b8c`、入口 v29、工作区干净、
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
