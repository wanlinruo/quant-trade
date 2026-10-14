# Research Runner 作业协议

本协议是 #285 的 Runner 基础：把 Web 控制面与研究执行进程之间的共享 SQLite 队列替换为
窄而可审计的文件协议。当前仅注册 `verify-snapshot` 端到端任务，用于验证发布、身份、快照、
状态和产物链；现有 Web 回测尚未切换。

## 所有权边界

```text
<job-store>/
├── inbox/       Web 只创建不可变 job request
├── control/     Web 只创建取消标记
├── jobs/        Runner 独占写状态、日志和产物；Web 只读
└── rejected/    Runner 隔离格式错误或 hash 不符的请求
```

请求由 `request_sha256` 绑定，重复 job ID 不允许覆盖。Runner 用目录创建和文件 rename 原子
认领任务，因此 Web 不需要取得 Runner 状态目录或研究产物的写权限。

## Job request

每个请求固定记录：

- 完整 40 位 `code_sha` 和 CI wheel 的 SHA-256；
- 确定的 `snapshot_id` 和 snapshot content SHA-256；
- allowlist task 名称和不含秘密的 JSON 参数；
- 实际 Runner cgroup 的 MemoryMax、CPUQuota 与 IOWeight；
- 单项任务超时。

Runner 启动时绑定一个代码/wheel 身份和一组 cgroup 上限。由于当前 Runner 单并发，任务子进程
继承的服务 cgroup 就是该任务的资源边界；请求中的内存、CPU 与 IO 必须与服务声明完全一致，
避免 manifest 声称了一个实际上没有执行的更窄限制。任务超时可以小于服务上限，并由 Runner
主动终止整个子进程组。

## 状态和恢复

状态只能执行：

```text
claimed -> running -> succeeded | failed | cancelled | interrupted
claimed -----------→ failed | cancelled | interrupted
```

Runner 重启时，遗留的 `claimed`/`running` 作业进入 `interrupted`，不自动重放。研究可能包含
昂贵运算或外部副作用；只有显式重新提交一个新 job ID 才会再次执行。

成功作业生成 artifact manifest，记录 request hash、每个产物的大小与 SHA-256、产物集合 hash，
以及 `run.log` 的大小与 SHA-256。Web 只能展示这些文件，不能修改状态或伪造成功产物。

## 安全边界

- task 名称由 Runner 内置 allowlist 映射，不接受任意 shell 或任意模块名。
- 子进程使用参数数组启动，不经过 shell；工作目录固定为作业目录。
- 请求拒绝 password、token、secret、private key、account 等秘密形状参数。
- Runner 在执行前重新验证 wheel hash 与快照 manifest 身份；快照发布时执行全部文件 hash
  校验，`verify-snapshot` 任务可按需复验，避免每个研究作业开始前重复扫描整个 Warehouse。
- Runner 不需要 Redis、券商配置、生产状态库或 Warehouse 写权限。

## systemd 隔离契约

`deploy/systemd/qsys-research-runner.service` 固定使用 `qsys-research` 身份，单并发子进程继承
`MemoryHigh=3G`、`MemoryMax=4G`、`CPUQuota=100%`、低 CPU/IO 权重与较低调度优先级。
Runner 的 OOM 分数高于实盘服务；systemd-oomd 发生内存压力时优先终止 Runner cgroup，且启动
频率受限，避免超限后反复拉起。服务通过
`BindReadOnlyPaths` 只读看到 `/data/qsys/warehouse-snapshot-store`，唯一可写路径是
`/data/qsys/research-jobs`；可变生产 Warehouse、生产状态和部署配置显式不可见。服务同时使用
私有网络命名空间、IP deny-all、空 capability 集和 systemd 内核保护，因此没有 Redis、券商
或互联网访问路径。

发布目录必须是 `/opt/qsys-research/releases/<code_sha>`，由 root 拥有且不能被 group/other
写入；`current` 只由安装脚本原子切换。Runner 启动时解析并固定 wheel 的规范路径与 SHA-256，
运行中切换 `current` 不会改变既有进程的代码身份。准备好版本化 wheel/venv 和快照后，由维护
窗口显式执行：

```bash
scripts/install_research_runner_isolation.sh <完整提交SHA>
```

`test` 分支成功 CI 会上传 `qsys-research-<完整提交SHA>` artifact，内含规范名
`dist/qsys.whl` 与 `release.json`。Manifest 绑定 source commit/tree、wheel、`pyproject.toml`、
`uv.lock` 的 SHA-256 及 CI run identity；安装脚本会在切换 `current` 前重新验证全部绑定关系。
只能从该 SHA 的成功 CI run 下载 artifact，不能用开发机临时构建的 wheel 替代。

脚本创建系统账号和分权目录：Web 身份只能写 `inbox/control`，Runner 身份独占
`jobs/rejected`，然后安装无秘密的代码 SHA 环境文件。脚本只 stage 版本，绝不自动启动服务。

## 与 #137 实盘观察期的门禁

#137 的 10,000 元、至少 5 个交易日人工盯盘结束且维护者明确放行前，不得在生产创建
`/etc/qsys/research-runner.activation-approved`，也不得启用 Runner。代码开发、CI、测试节点的
负向权限与压力验证可以继续，不能阻塞或改变 live core、Windows agent、订单 Redis 和生产
状态库。

观察期结束后仍须先完成压力演练并安排维护窗口。显式批准和激活必须是两个可审计操作：

```bash
printf 'issue=137 approved_at=<UTC时间>\n' | \
  sudo tee /etc/qsys/research-runner.activation-approved
sudo systemctl enable --now qsys-research-runner.service
```

停用与回滚不删除作业证据，并撤销激活批准：

```bash
sudo systemctl disable --now qsys-research-runner.service
sudo rm -f /etc/qsys/research-runner.activation-approved
scripts/install_research_runner_isolation.sh <上一版本完整提交SHA>
```

生产接入前仍需完成：CI wheel 安装与来源证明、Web 提交/查看适配、真实回测 task 的迁移、
测试节点负向权限验证和压力演练。仓库内 unit/安装脚本不会自行接触或修改生产节点。
