# 项目交接入口

> 快照时间：2026-10-07（Asia/Shanghai）。这是接手入口，不替代 GitHub、节点和机器可读
> 契约的实时状态。执行任何写操作前必须重新核验。

## 先读什么

按以下顺序建立上下文：

1. GitHub [#154](https://github.com/wanlinruo/quant-trade/issues/154) 最新评论：当前实施状态与未完成项；
2. GitHub [#151](https://github.com/wanlinruo/quant-trade/issues/151)：跨 Linux/Windows CI/CD 总依赖图；
3. `docs/deploy/production-linux-release.md`：生产 Linux 发布规则；
4. `docs/reference/deployment-contract.md` 和 `deploy/deployment.toml`：机器可读部署边界；
5. `.github/workflows/deploy-production-linux.yml`：实际 GitHub 门禁；
6. `deploy/linux/qsys-production-deploy-entrypoint.py`：节点最终执行与代码回滚逻辑；
7. `docs/deploy/linux-core.md`、`docs/deploy/windows-node.md`：节点实况和约束。

发生冲突时，优先级是：维护者最新明确指示 → GitHub Issue 最新记录 → 当前 workflow/
节点入口和 manifest → 本快照 → 其他历史文档。不得用旧文档覆盖维护者的新要求。

## 当前状态

- `main` 与 `test` 均为 `514ae39d8466470f3374bd08002754108c0f1bfb`。
- 生产 Linux checkout 仍为 `01e41414903f0f55414f935adbe9116001ebdc56`；尚未正式发布上述目标。
- #154 为 `status:in-progress`，不是已完成状态。
- 工作流实现已由 PR #160、#163 合入。
- CI run `37574939589` 成功。
- 自动测试发布 run `37575050426` 成功；节点记录为
  `/data/qsys-test/var/deployments/github-37575050426-1/record.json`。
- 生产 dry-run `37575107404` 成功：目标 `514ae39`、组件 `linux-data-jobs`、
  `restart_units=[]`、rollback target `01e4141`。
- run `37575616469` 是故意使用错误确认短语的负向演练；它按预期失败，节点步骤全部跳过，
  不代表系统故障。
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

## #154 已完成与未完成

已经完成：

- 独立 production GitHub environment、固定 host key、专用 Ed25519 key；
- testing/production 两把 key 使用不同 forced command，均不提供任意 shell；
- 完整 SHA、`main` 祖先、已合并 PR、PR CI、测试 tree 证据、组件集合和节点状态门禁；
- dry-run、GitHub Deployment、Actions Summary、节点 `record.json` 和代码自动回滚实现；
- control-plane-only 变更的节点 attestation；
- 正向 dry-run 与错误确认短语负向演练；
- Linux `PasswordAuthentication=yes` 保持不变，Windows 未改动。

仍未完成：

1. 经维护者明确确认后执行 `dry_run=false` 的正式生产发布；
2. 经维护者明确确认后执行受控健康失败和自动代码回滚演练；
3. 核验正式发布/回滚的 GitHub Deployment、节点 record、最终 SHA、服务与 health；
4. 证据写回 #154，满足验收标准后才关闭 Issue；
5. #155 的 Windows 自动发布是后续独立工作，不包含在 #154 内。

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
打印配置内容或任何 secret。预期快照是：生产 `01e4141`、工作区干净、
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

接手者应先向维护者复述：当前生产 SHA、目标 SHA、dry-run 证据、预计重启范围、rollback
target、两项待授权操作，以及密码登录保留要求。信息不一致时停止写操作，先更新 Issue。
