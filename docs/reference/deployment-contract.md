# 部署契约

`deploy/deployment.toml` 是测试与生产发布共同使用的机器可读契约。它定义环境分支、
配置相对路径、证据目录，以及每类代码变化对应的节点、Linux unit、Windows app/task、
健康检查和回滚方式。后续 Actions workflow 与节点执行器必须读取这份文件，不得各自维护
另一份服务映射。

## 生成部署计划

计划只接受两个完整的 40 位小写 commit SHA，避免把部署输入退化成会移动的分支名：

```bash
uv run python scripts/deployment_contract.py plan \
  --environment test \
  --base 0000000000000000000000000000000000000000 \
  --target 1111111111111111111111111111111111111111
```

实际 SHA 必须存在于当前仓库。变更路径可以命中多个组件，例如公共 Redis transport 会
同时命中 Linux 常驻服务、Windows agent 和 Windows gateway。文档、测试和审计产物被
显式列为不需要运行时部署；其他未知路径使命令以退出码 2 失败，必须先补 manifest，
不能静默猜测。

测试环境只有 `test-runtime` 目标，永远不会生成 Windows task 或生产 unit。生产计划
按照组件返回最小确定集合，但带 `manual_gate=true` 的依赖、systemd 或 watchdog 变更
仍需人工确认。

节点不直接向 Actions 暴露 shell。Linux 固定入口通过
`scripts/install_deployment_entrypoint.sh` 安装到工作树外的
`/usr/local/libexec/qsys-deploy-entrypoint`，配置固定在
`/etc/qsys/deployment-entrypoint.json`。测试入口只启用测试环境和 `test-runtime`：

```bash
/usr/local/libexec/qsys-deploy-entrypoint deploy \
  --environment test --sha <40位完整SHA> --component test-runtime \
  --run-id <GitHub-run-attempt> --actor <GitHub用户> --repository <owner/repo>
```

入口由节点部署用户执行；它内部只对固定的 `systemctl` 操作使用免密 sudo。不要用 root
运行整个入口，否则 Git 的只读 deploy key、仓库 ownership 与审计身份都会错位。
GitHub 使用另一把测试专用 key；其 `authorized_keys` 条目强制进入
`/usr/local/libexec/qsys-deploy-ssh`，只接受 `status`、`deploy`、`attest` 和 `record`，
不提供交互 shell、端口转发或 agent 转发。`attest` 只用于可信 planner 判定无测试运行时
组件的 control-plane-only tree；它校验 exact `origin/test`、健康与存储隔离，记录证据但
不切换代码或重启服务。执行运行时部署时，固定入口先校验 exact `origin/test`，再从该
完整 SHA 读取部署脚本；因此脚本自身的 checkout 修复可跨 squash/对齐后的非祖先历史
生效，同时不会执行浮动分支或节点旧 checkout 中的部署逻辑。

生产使用完全独立的安装脚本、入口、配置和 key：

- `scripts/install_production_deployment_entrypoint.sh`；
- `/usr/local/libexec/qsys-production-deploy-entrypoint`；
- `/etc/qsys/production-deployment-entrypoint.json`；
- forced command `/usr/local/libexec/qsys-production-deploy-ssh`。

生产入口只接受 `status`、`deploy`、`windows-deploy` 和 `record`。它再次校验 `main` 来源、成功测试 tree、
精确组件集合、干净工作区与节点健康；testing key 不能调用生产入口。生产发布仍必须由
维护者通过 `Deploy production Linux` 手工触发，不能把入口已安装理解为自动发布授权。
同一 SHA 重复请求会在依赖同步、服务重启和历史记录之前返回成功，因此不会制造重复
副作用。

固定生产入口通过 `status` 声明版本和 capability。Linux/Windows workflow 在计算计划前
要求 `systemd-apply-v1` 与 `systemd-rollback-v1`；旧入口必须先由维护者运行
`scripts/install_production_deployment_entrypoint.sh` 显式升级，不能继续执行并产生假成功。
安装脚本会先把旧入口、dispatcher 和配置备份到
`/data/qsys/var-deployments/bootstrap-backups/`。

`linux-systemd` 不是路径标签，而是有实际 apply/verify/rollback 的部署组件：目标 SHA 的
生产 unit、timer、行情门面环境文件与 drop-in 会先备份 `/etc` 实况，再按白名单安装；随后
执行 `daemon-reload`，只重启安装前 active 且确受影响的 timer/非 oneshot service。安装后
逐文件 SHA256 等价检查、`systemd-analyze verify`、LoadState 与原 active 状态缺一不可。
失败时恢复原文件、重新 reload 并恢复原 active 状态；备份路径写入 deployment record 的
rollback target。`qsys-web-test.service` 明确不属于生产安装集合。

Windows 发布同样不信任节点上已安装的旧协调器：固定生产入口从已经通过上述门禁的目标
SHA 提取 Linux 协调器，协调器再从同一 SHA 提取 Windows 控制器并上传执行。健康探针以
切换后应用的 `src` 设置 `PYTHONPATH`，并同时要求目标文件清单、`DEPLOYED_SHA`、进程和
切换后新心跳通过；失败时恢复两个应用各自的部署前 SHA，并在退出前恢复 watchdog。

## 配置检查与指纹

真实配置仍留在各节点，不进入 Git。检查命令只输出错误代码和指纹：

```bash
uv run python scripts/deployment_contract.py validate-config \
  --environment production \
  --config config/deploy.local.toml
```

配置文件必须在首个 section 前声明 `schema_version = 1`；未知版本和未知顶层 section
都会失败关闭。结构契约同时保存在 `deploy/config.schema.json`。测试节点从
`config/deploy.test.example.toml` 初始化本地 `config/deploy.local.toml`，模板自身不含
凭据且永久禁用下单。

测试配置额外强制：

- `safety.mode="disabled"` 且 `orders_enabled=false`；
- 不得登记生产账户；
- Redis DB 不得使用默认 DB 0；
- 所有 Stream 名必须以 `test:` 开头；
- workbench 路径必须是测试专用路径。

传入 `--against` 时还会拒绝两套环境共享 Redis endpoint+DB、Stream 名、workbench 或
本地 kill 文件。`public_sha256` 对密码、token、webhook、账号等字段只编码 set/unset，
不会因秘密内容变化而泄漏可供离线猜测的普通 hash。节点可用仅保存在本机的随机 key：

```bash
--fingerprint-key-file /etc/qsys/config-fingerprint.key
```

生成 `private_hmac_sha256`，以发现包括凭据轮换在内的完整配置漂移。key 文件不得进入
仓库、Actions artifact 或部署日志。

## 部署证据

每次发布按 `deploy/record.schema.json` 写 `record.json`，至少包含：

- GitHub run、environment、actor、PR、commit SHA 与 tree SHA；
- 各节点部署前后版本、组件和配置指纹；
- 每项健康检查及结果；
- 各节点 rollback target；
- 数据/schema 迁移摘要。

验证命令：

```bash
uv run python scripts/deployment_contract.py validate-record record.json
```

记录拒绝密码、token、账户、webhook 等敏感形状的字段。代码发布只能记录迁移摘要，
不得把数据回滚混入代码回滚。
