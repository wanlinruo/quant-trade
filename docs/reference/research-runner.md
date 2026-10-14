# Research Runner 作业协议

本协议是 #285 的第二阶段基础：把 Web 控制面与研究执行进程之间的共享 SQLite 队列替换为
窄而可审计的文件协议。当前仅注册 `verify-snapshot` 端到端任务，用于验证发布、身份、快照、
状态和产物链；现有 Web 回测尚未切换，生产服务也尚未安装。

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

生产接入前仍需完成：独立 `qsys-research` 用户、systemd/cgroup unit、只读 bind mount、CI wheel
安装与 SHA 证明、网络封锁、Web 提交/查看适配，以及真实回测 task 的迁移和压力演练。
