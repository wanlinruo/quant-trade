# 生产 Linux 发布

执行后续发布前，先阅读仓库根目录 `HANDOFF.md` 和 GitHub #154 最新评论。
本文描述稳定流程；具体 SHA、run ID 和待授权操作以交接快照及实时检查为准。

生产发布只通过 GitHub Actions 的 `Deploy production Linux` 手工触发。合并或推送
`main` 不会自动修改生产节点。

## 发布输入

- `target_sha`：`main` 历史中的完整 40 位 SHA。
- `components`：默认 `auto`；显式填写时必须与 manifest 计算出的 Linux 组件完全一致。
- `dry_run`：默认开启，只验证门禁并输出计划。
- `confirmation`：固定填写 `DEPLOY_PRODUCTION`。

正式发布前必须先成功执行同一目标的 dry-run。GitHub workflow 和节点固定入口会分别
校验目标来源；节点还会按 Git tree 查找测试环境的成功发布记录，避免 `test -> main`
squash 后 SHA 改变导致证据断链。

## 权限边界

GitHub production environment 使用独立 Ed25519 密钥。公钥在 `ubuntu` 的
`authorized_keys` 中带 `restrict` 和固定命令，只能调用生产部署入口，不能获得 shell；
testing key 也不能调用生产入口。

这套机制不会关闭或替换维护者的密码登录、个人 SSH key 登录，也不调整 Windows 的
密码、RDP 或 Autologon。部署密钥轮换只替换带 `qsys-github-production` 标记的单独一行。

## 执行与回滚

节点只重启 manifest 判定为受影响且发布前已经处于 active 的服务；不会启动原本
inactive 的服务。发布后校验受影响服务和本机 `8420/healthz`。代码、依赖安装或健康
检查失败时，自动恢复发布前 SHA、同步旧依赖并重启同一组服务。数据和 schema 迁移
不在自动反向回滚范围内，因此包含迁移的发布必须另行制定操作方案。

每次正式发布写 GitHub Deployment、Actions Summary 和节点 `record.json`。dry-run
只产生 Actions 日志与 Summary，不冒充一次生产 Deployment。

## 应急访问

日常不再通过交互式 SSH 执行 `git reset`、依赖同步或服务重启。维护者的密码/密钥
SSH 保留用于只读排查，例如：

```bash
git -C /home/ubuntu/qsys status --short --branch
systemctl status qsys-monitor.service --no-pager
curl --fail http://127.0.0.1:8420/healthz
```

若自动入口本身不可用，先保留现场和发布记录，再由维护者明确批准应急写操作。
