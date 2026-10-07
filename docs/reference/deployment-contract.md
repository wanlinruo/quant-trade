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
`/etc/qsys/deployment-entrypoint.json`。当前只启用测试环境和 `test-runtime`：

```bash
sudo -n /usr/local/libexec/qsys-deploy-entrypoint \
  --environment test --sha <40位完整SHA> --component test-runtime
```

生产组件会失败关闭，待生产发布 workflow 与授权边界在后续 Issue 中完成后才开放。
同一 SHA 重复请求会在依赖同步、服务重启和历史记录之前返回成功，因此不会制造重复
副作用。

## 配置检查与指纹

真实配置仍留在各节点，不进入 Git。检查命令只输出错误代码和指纹：

```bash
uv run python scripts/deployment_contract.py validate-config \
  --environment production \
  --config config/deploy.local.toml
```

配置文件必须在首个 section 前声明 `schema_version = 1`；未知版本和未知顶层 section
都会失败关闭。结构契约同时保存在 `deploy/config.schema.json`。

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
