# 独立 self-hosted CI Runner

状态：#304 实施契约。本文只覆盖普通 CI；测试/生产部署仍使用独立的
GitHub-hosted 执行面和既有受限 SSH gateway。

## 1. 固定安全边界

- Runner 使用独立 Linux x64 VM，建议 Ubuntu 24.04、4 vCPU、8 GiB 内存、40 GiB
  系统盘；不得与生产 Linux、Windows/QMT 或测试部署节点共机。
- 专用系统账号 `qsys-ci`，无 sudo、无交互 shell；只允许出站访问 GitHub、PyPI
  及当前锁文件实际依赖的下载源。
- 节点不保存生产/测试部署 SSH key、GitHub PAT、券商/Redis/Tushare 凭据，不挂载
  `/data/qsys`、`/etc/qsys`、warehouse、生产状态库或云元数据接口。
- `CI` workflow 只有 `actions/checks/contents/deployments: read`。Deploy test、Align
  test、Direct push audit、Research stage/drill 与所有 production workflow 固定留在
  `ubuntu-24.04`，不共享 `qsys-ci` 主机或凭据。
- 仓库仍为 public 时不启动 self-hosted runner。GitHub 官方明确警告公开仓库的 fork
  PR 可持久化攻陷 self-hosted 主机；安全顺序是先安装但保持离线，再切 private，最后
  注册/启动并启用路由。
- 同仓库 `test` push 和同仓库 PR 可以进入 `qsys-ci`；外部 fork PR 无论仓库变量如何
  配置，都回退到 GitHub-hosted `ubuntu-24.04`。

## 2. 工作流切换契约

`.github/workflows/ci.yml` 读取仓库变量 `QSYS_CI_RUNNER_LABELS`：

```json
["self-hosted","linux","x64","qsys-ci"]
```

变量不存在或为空时，全部 CI 继续使用 `ubuntu-24.04`。这使工作流变更可先由现有
hosted CI 验证；runner 离线时必须先删除变量，防止 job 等待至 GitHub 的 24 小时上限。

启用：

```bash
gh variable set QSYS_CI_RUNNER_LABELS \
  --repo wanlinruo/quant-trade \
  --body '["self-hosted","linux","x64","qsys-ci"]'
```

回退：

```bash
gh variable delete QSYS_CI_RUNNER_LABELS --repo wanlinruo/quant-trade
```

## 3. 安装与注册

从 GitHub Runner 设置页取得当前 Linux x64 archive URL 与官方 SHA-256。版本和哈希必须
记录在 #304；不要使用“latest”脚本，也不要把注册 token 写入磁盘、shell history 或
Issue。注册 token 一小时失效。

在全新独立 VM 上下载 archive 后，从已经审核的精确仓库 SHA 取得安装器与 hook，执行：

```bash
read -rsp 'registration token: ' QSYS_CI_REGISTRATION_TOKEN
export QSYS_CI_REGISTRATION_TOKEN
sudo --preserve-env=QSYS_CI_REGISTRATION_TOKEN \
  scripts/install_ci_runner.sh \
  --archive /tmp/actions-runner-linux-x64.tar.gz \
  --sha256 '<official-sha256>' \
  --repository wanlinruo/quant-trade \
  --name qsys-ci-01
unset QSYS_CI_REGISTRATION_TOKEN
```

安装器会校验 archive、拒绝存在生产路径的主机、创建无登录账号、固定 `qsys-ci`
标签、关闭 runner 自更新，并安装 root-owned 的 job start/completed hook。安装结束只创建
service，不启动。Runner 升级时新建 VM 或从干净镜像重建，不原地覆盖未知状态。

仓库切为 private 后启动 runner，并核对：

```bash
sudo systemctl start 'actions.runner.*.service'
gh api repos/wanlinruo/quant-trade/actions/runners \
  --jq '.runners[] | {name,status,busy,labels:[.labels[].name]}'
```

## 4. 清理、日志和健康

- 每个 job 开始前 hook 检查 Linux/x64、workspace containment、生产路径和敏感环境变量；
  任一不满足即以非零退出，job 不执行。
- 每个 job 结束后 hook 只清空 `GITHUB_WORKSPACE` 与 `RUNNER_TEMP`，并验证两者位于
  `/var/lib/qsys-ci-runner/_work` 内；路径逃逸时拒绝删除。
- runner 应用和 hook 由 root 持有，job 账号不可修改；工作目录由 `qsys-ci` 持有。
- journald 至少保留 30 天；每日检查 service active、GitHub runner online、根盘使用率、
  最近成功 job 和 runner 版本。Runner offline 超过 10 分钟告警。
- 禁止在该节点启用 Docker socket、生产 SSH agent 或共享缓存。依赖缓存若以后启用，
  只能按 lockfile hash 隔离，PR 不得写入跨信任域缓存。

## 5. 验收顺序

1. 当前 public 状态下合入工作流、hook、测试与本文；变量保持未设置。
2. 创建独立 VM，验证无生产路由/挂载/凭据，runner 安装但保持 stopped。
3. 将仓库恢复 private，重新核对 Actions、fork、ruleset 与环境设置。
4. 启动 runner，确认 `qsys-ci` 标签 online；设置 `QSYS_CI_RUNNER_LABELS`。
5. 对一个无业务差异的 `test` commit 运行 policy、secret-scan、test、package；核对四个
   check、制品 SHA、test 自动部署和 release evidence 与 hosted 结果一致。
6. 执行负向测试：runner offline、hook 发现生产路径、workspace 逃逸、磁盘高水位；均应
   fail closed。再删除变量验证 hosted 回退。
7. 连续至少 3 个成功 CI 周期后，记录 run ID、runner 名称/版本、host 镜像、清理日志和
   回退演练，方可关闭 #304。

## 6. 故障处置

发现离线、污染、异常网络连接、升级失败或清理 hook 失败时：

1. 立即删除 `QSYS_CI_RUNNER_LABELS`，后续 CI 回到 hosted；
2. 停止 runner service，在 GitHub 设置/API 删除该 runner；
3. 不在原机“清理后继续用”，销毁 VM 并从固定镜像重建；
4. 若怀疑 token 或 `GITHUB_TOKEN` 泄漏，撤销相关凭据并审计同期 workflow；
5. 记录时间、受影响 run、镜像/runner 版本、处置和重新上线证据。

官方依据：

- [Self-hosted runners reference](https://docs.github.com/en/actions/reference/runners/self-hosted-runners)
- [Secure use reference](https://docs.github.com/en/actions/reference/security/secure-use)
- [Running scripts before or after a job](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/run-scripts)
- [REST API endpoints for self-hosted runners](https://docs.github.com/en/rest/actions/self-hosted-runners)
