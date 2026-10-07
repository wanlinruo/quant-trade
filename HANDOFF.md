# 项目交接入口

> 快照时间：2026-10-07（Asia/Shanghai）。这是接手入口，不替代 GitHub、节点和机器可读
> 契约的实时状态。执行任何写操作前必须重新核验。

## 先读什么

按以下顺序建立上下文：

1. GitHub [#154](https://github.com/wanlinruo/quant-trade/issues/154) 最新评论：Linux 发布与回滚验收证据；
2. GitHub [#151](https://github.com/wanlinruo/quant-trade/issues/151)：跨 Linux/Windows CI/CD 总依赖图；
3. `docs/deploy/production-linux-release.md`：生产 Linux 发布规则；
4. `docs/reference/deployment-contract.md` 和 `deploy/deployment.toml`：机器可读部署边界；
5. `.github/workflows/deploy-production-linux.yml`：实际 GitHub 门禁；
6. `deploy/linux/qsys-production-deploy-entrypoint.py`：节点最终执行与代码回滚逻辑；
7. `docs/deploy/linux-core.md`、`docs/deploy/windows-node.md`：节点实况和约束。

发生冲突时，优先级是：维护者最新明确指示 → GitHub Issue 最新记录 → 当前 workflow/
节点入口和 manifest → 本快照 → 其他历史文档。不得用旧文档覆盖维护者的新要求。

## 当前状态

- #154 的生产发布和受控健康失败回滚验收均已完成；最终证据以 Issue 最新评论为准。
- 生产 Linux 稳定 checkout 为 `c1c266e76aaf3b599cbe0753737a52b4be8a676a`，工作区干净。
- 正式生产发布 run `37583240020` 成功，GitHub Deployment `6903543850` 成功；节点记录为
  `/data/qsys/var-deployments/github/github-37583240020-1/record.json`。
- 受控回滚演练 run `37588622279` 按设计因 health gate 失败；GitHub Deployment
  `6904416793` 标记失败，节点记录状态为 `rolled_back`，代码恢复至 `c1c266e`。
- 演练未包含 migration 或 schema 反向操作；生产 `qsys-monitor.service` 已恢复 active，
  `8420/healthz` 返回 200。
- 自动化后续修复已合入：PR #169 补取测试部署基准、PR #170 识别删除路径、PR #171
  支持将干净测试 checkout 精确切换到已验证的 `origin/test` SHA。
- 生产配置已补 `schema_version=1`，旧运行时加载和新契约校验均通过；变更前备份在
  `/data/qsys/var-deployments/issue-154-config-prep/`。

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

## #154 完成状态

已经完成：

- 独立 production GitHub environment、固定 host key、专用 Ed25519 key；
- testing/production 两把 key 使用不同 forced command，均不提供任意 shell；
- 完整 SHA、`main` 祖先、已合并 PR、PR CI、测试 tree 证据、组件集合和节点状态门禁；
- dry-run、GitHub Deployment、Actions Summary、节点 `record.json` 和代码自动回滚实现；
- control-plane-only 变更的节点 attestation；
- 正向 dry-run 与错误确认短语负向演练；
- 正式 `dry_run=false` 发布、GitHub Deployment、节点 record 与发布后健康核验；
- 受控健康失败、自动代码回滚、服务恢复与回滚后健康核验；
- Linux `PasswordAuthentication=yes` 保持不变，Windows 未改动。

后续范围：#155 的 Windows 自动发布是独立工作，不包含在 #154 内。任何新的生产发布或
故障注入仍需维护者针对该次操作明确确认。

## 只读接手检查

这些检查不会授权后续写操作：

```bash
git status --short --branch
git fetch origin main test
git rev-parse origin/main origin/test
gh issue view 154 --comments
gh run list --workflow deploy-production-linux.yml --limit 5
```

Linux 节点只读检查应确认生产 SHA、工作区、监控、health 和密码认证状态；不要在检查命令中
打印配置内容或任何 secret。预期快照是：生产 `c1c266e`、工作区干净、
`qsys-monitor.service` active、`8420/healthz` 为 200、`PasswordAuthentication=yes`。

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
