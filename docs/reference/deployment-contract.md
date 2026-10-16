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
要求 v10 及 `systemd-apply-v1`、`systemd-changed-artifacts-v1`、`systemd-preview-v1`、
`systemd-rollback-v1`、`systemd-enable-state-v1`、`monitor-shadow-state-v1` 与
`monitor-shadow-command-v1`、`monitor-research-command-v1`、`component-release-v1`；旧入口必须先由维护者运行
`scripts/install_production_deployment_entrypoint.sh` 显式升级，不能继续执行并产生假成功。
安装脚本会先把旧入口、dispatcher 和配置备份到
`/data/qsys/var-deployments/bootstrap-backups/`。

`linux-systemd` 不是路径标签，而是有实际 apply/verify/rollback 的部署组件：目标 SHA 中
本次实际变化的生产 unit、timer、行情门面环境文件与 drop-in 会先备份 `/etc` 实况，再按
白名单安装；不会借一次无关 timer 发布顺带覆盖其他现场差异。随后
执行 `daemon-reload`，只重启安装前 active 且确受影响的 timer/非 oneshot service。白名单
`SYSTEMD_ENABLE_ON_INSTALL` 中的新 timer 会显式 `enable --now`；dry-run 必须把它列入
`enable_units`。安装后逐文件 SHA256 等价检查、`systemd-analyze verify`、LoadState、enabled
与 active 状态缺一不可。失败时恢复原文件、重新 reload，并精确恢复发布前 enabled/active
状态；备份路径写入 deployment record 的 rollback target。`qsys-web-test.service` 属于测试环境；`qsys-shadow-core.service` 与
`qsys-live-core.service` 是需按站点用户和路径修改的模板，三者均不属于生产安装集合。若
一次生产变更只涉及这些排除项，入口会失败关闭，不能把未部署内容记录为成功。
dry-run 会列出这次实际安装的 `/etc` 目标，以及因内容变化将在正式发布中重启、保持
inactive 或首次启用的 unit，不能仅依赖 manifest 的常规服务列表推断 systemd 副作用。
`qsys-zz-limit-up-shadow-web.conf` 会安装成排序最后的 monitor drop-in，显式把生产只读榜单
SQLite 传给 Web 进程，并把 `/data/qsys/var-prod` 保持为只读路径；部署同时删除旧的较早排序
drop-in。该变更必须重启 monitor，且入口会检查 systemd 合并后的最终 `ExecStart` 确实保留
`--limit-up-shadow-state`，避免其他现场 drop-in 再次覆盖参数。
同一最终命令还必须显式包含 #210 冻结研究目录；研究页只读该目录中的紧凑证据，
不会把大型训练集或全量预测复制到 Web 状态目录。

## 组件级不可变发布

生产运行单元最终必须拥有各自的 release 目录、虚拟环境、当前版本指针和回滚目标；
共享仓库只保存配置与运维入口，不再同时充当所有进程的代码发布目录。#137 约束实盘
策略、Live Core 和资金风险，但不作为无关数据、Web、研究组件发布的总门禁。

首个迁移组件是 `catalog-verify`：CI 为 test 分支的精确 SHA 生成
`qsys-catalog-verify-<SHA>`，其中 wheel 保留标准的 distribution/version/tag 文件名；
wheel 文件名与 SHA-256、Git tree、`pyproject.toml`、`uv.lock`、入口点和 CI 身份全部写入
manifest。节点 stage 后使用：

```bash
scripts/install_catalog_verify_release.sh <完整提交SHA>
```

release 位于 `/opt/qsys-data/releases/catalog-verify/<SHA>`，`current` 以原子 symlink
切换。安装器拒绝在质检运行中切换，验证 release 后才安装专属 systemd drop-in；不会
执行 `git reset`，也不会重启 Live Core、Web、Paper、Health Observer 或 Windows 链路。
基础 unit 保留共享 checkout 入口，因而仅合入代码不会提前切换生产执行路径；必须先由
受审计的 staging 网关放置并验证 release，再显式运行安装器。

组件隔离的验收不是“新进程能启动”，而是发布前后 Live Core 的 PID、启动时间、部署
SHA 和心跳均不变化。后续 Web、数据同步、Health Observer 依次迁移到同一协议。Redis
消息、snapshot schema、策略/风控语义或状态库迁移仍属于跨组件兼容变更，不能借组件发布
绕过联合审查。

生产组件制品只能通过手工 `Deploy production component` workflow 发布。workflow 从目标
SHA 对应的成功 test push CI 下载精确命名 artifact，计算传输 ZIP 的 SHA-256，并通过受限
SSH stdin 交给 `component-deploy`；节点拒绝额外文件、路径穿越、symlink、重复成员、超限
大小和 hash 不符。节点再次验证目标属于 `main`、存在相同 tree 的成功测试部署记录，并在
独立 venv 中按锁文件安装依赖后校验 wheel manifest，最后才切换 `current`。

切换前后各执行一次 `live_preflight --require-live`，同时固定比较 Live Core 的 MainPID、
单调启动时间和共享生产 checkout SHA。任一字段变化或心跳门禁失败，组件指针回滚；部署
记录把这些字段放入 `before` / `after`，workflow 再做一次独立断言。该路径不接受通用组件
名，目前只允许 `catalog-verify`。

### 2026-10-16 catalog 组件发布复盘与固定约束

首次把 `catalog-verify` 从共享 checkout 切到不可变 release 时连续暴露了两个既有边界：

- 仓库中的 production entrypoint 已升级到 v12，但常规 Linux manifest 把固定入口列为控制面
  ignored path；生产 checkout 更新不能证明 `/usr/local/libexec` 中实际执行的入口也已更新。
- 历史维护曾用 root 写入 ubuntu 的共享 uv cache。在 `fs.protected_hardlinks=1` 下，默认
  hardlink 模式会让 release venv 得到调用用户不能收口权限的 inode。只修 `uv sync` 仍不够，
  后续 `uv pip install` 必须继承完全相同的隔离环境。

以后组件发布必须遵守以下顺序：

1. 在触发完整 workflow 前，比较已安装固定入口与目标 SHA 对应文件的 SHA-256，并调用只读
   `status` 核对 `entrypoint_version` 和 capability。control-plane-only tree 不会生成 Linux
   runtime component；这种空计划必须失败关闭，再走带备份、精确 worktree 和哈希复核的
   bootstrap，不能把它误判为普通生产重部署。
2. 构建 release venv 的所有 uv 子命令必须使用同一个显式 environment，包括
   `UV_PROJECT_ENVIRONMENT=<release>/venv` 和 `UV_LINK_MODE=copy`。禁止假设 cache owner、挂载点、
   hardlink 支持或历史维护方式一致，也禁止用 `sudo uv` 污染应用账号 cache。
3. stage 验收必须覆盖完整链路：依赖同步、项目 wheel 安装、入口可执行、manifest 复核及最终
   `chmod -R go-w`。前一步成功不能替代最终不可变权限收口。
4. `CalledProcessError` 写入 deployment record 时必须保留捕获到的 stderr（无 stderr 才退回
   stdout），并限制长度。只有返回码而没有第一条工具诊断会迫使现场重复执行同一失败命令。
5. 每次失败先验证 `before/after` 组件指针、Live Core PID/单调启动时间、生产 checkout 与
   failed units，再决定是否重试。指针未切换且 live identity 未变化时，应记录为安全失败，
   不得笼统描述为生产服务故障。

对应事件证据保存在 #346、#354、#355，以及 Actions runs `38888344457`、`38889119287`、
`38892672095`。这些 run ID 用于复盘，不替代未来发布必须生成的新 dry-run 和 deployment record。

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
