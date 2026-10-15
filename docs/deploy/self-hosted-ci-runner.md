# 同机隔离 self-hosted CI Runner

状态：#304 实施契约。普通 CI 与生产 Linux 共用物理机，但不共享身份、容器引擎、
文件、网络权限或凭据。Deploy、Align、Direct push audit、Research stage/drill 和全部
production workflow 继续固定在 GitHub-hosted `ubuntu-24.04`。

## 1. 安全边界

- `qsys-ci` 是无登录、无 sudo 的专用 UID，只运行 rootless Podman；它不属于 `docker`
  组，也不能访问 `/var/run/docker.sock`。
- 每次由 GitHub JIT API 注册一个 runner，只处理一个 job；job 后 `podman --rm` 销毁
  容器、overlay 和 workspace。不复用可被前一 job 污染的常驻 runner。
- 唯一宿主挂载是一次性 JIT 配置。生产的 `/data/qsys`、`/etc/qsys`、`/opt/qsys`、
  SSH 目录、Docker socket、warehouse、状态库和云元数据均不挂载。
- nftables 按 `qsys-ci` UID 拒绝 loopback（仅放行 systemd-resolved 的 DNS stub）、
  RFC1918、link-local、云元数据地址及宿主机地址；rootless 网络同时设置
  `allow_host_loopback=false`。
- 仓库管理 token 由 root-only 控制器读取，不进入容器。它必须是只授权本仓库且仅有
  repository Administration(write) 的 fine-grained token（或同权限 GitHub App token）。
- 仓库为 public 时控制器拒绝注册。外部 fork PR 无论变量设置与否都使用 hosted runner。

GitHub 明确警告 public repository 的 fork 可在 self-hosted runner 上执行危险代码；因此
顺序固定为：隔离面预装但禁用、#137/#138 门禁通过、仓库恢复 private、最后启用路由。

## 2. 同机资源契约

生产机只有 4 vCPU / 7.5 GiB；`qsys-tushare-sync` 与
`qsys-catalog-verify` 实测峰值约 5.1–5.7 GiB。因此 runner 不允许与生产高水位任务并发：

- admission 要求可用内存不少于 3 GiB、系统盘空闲不少于 15 GiB、I/O full PSI
  `avg10 <= 5%`、无 failed unit，且 live-core/monitor 均 active；
- sync、catalog verify、daily archive、auction/gate study 任一 active 时拒绝注册；
- 每日 18:25（Asia/Shanghai）强制停止 runner，18:25–21:00 不注册，确保 19:02–20:30
  的生产链路优先。被终止的 CI job 允许失败，生产任务不得因 CI 等待；
- systemd 与 Podman 双层限制为 2 GiB soft / 2.5 GiB hard、swap 512 MiB、CPU 1.5 核、
  PIDs 512、低 CPU/IO weight，单次最长 2 小时；同一时刻只提供一个 runner。

这些限制意味着晚间 CI 会排队或被中断，这是同机方案相对于独立节点的明确代价。

## 3. 构建与预装（保持禁用）

镜像固定 Ubuntu 24.04 amd64 digest，Actions runner 固定 `2.338.0` 及官方 SHA-256。
从已审核的精确仓库 SHA 在生产机执行：

```bash
sudo .github/ci-runner/install.sh \
  --repository wanlinruo/quant-trade \
  --host-address 110.42.205.144
```

安装器创建 UID/subuid、rootless Podman、nftables、systemd units 并构建固定镜像，最后
明确 disable 两个 timer。安装器本身拒绝 18:00–21:00 以及生产批任务 active 时执行。
此阶段不配置 token、不创建 `enabled`，不会向 GitHub 注册。

## 4. 激活门禁

只有以下证据齐备才能激活：

1. #137 小额实盘观察期完成且无未处置 P0；
2. #138 在相同 cgroup 限制下完成 CPU、内存、I/O、磁盘和生产 timer 冲突的负向压测；
3. 仓库已恢复 private，Actions/ruleset/environment 设置复核完成；
4. nftables 实测无法访问宿主、Redis、Windows/QMT、metadata 与生产 HTTP 端点；
5. rootless 容器实测看不到 Docker socket、生产路径、SSH key 和 token。

激活时由管理员把窄权限 token 写入 root-only 文件，并写入带验收依据的门禁文件：

```bash
sudo install -o root -g root -m 0400 /dev/stdin \
  /etc/qsys/ci-runner/github-token
sudo install -o root -g root -m 0444 /dev/stdin \
  /etc/qsys/ci-runner/enabled
sudo systemctl enable --now qsys-ci-runner.timer qsys-ci-quiesce.timer
```

随后设置路由变量：

```bash
gh variable set QSYS_CI_RUNNER_LABELS --repo wanlinruo/quant-trade \
  --body '["self-hosted","linux","x64","qsys-ci"]'
```

变量不存在时 CI 使用 `ubuntu-24.04`。runner 故障时首先删除变量，避免 job 最长等待
24 小时：

```bash
gh variable delete QSYS_CI_RUNNER_LABELS --repo wanlinruo/quant-trade
```

## 5. 验收与回退

激活后先以无业务差异的 `test` commit 验证 policy、secret-scan、full suite、package、
artifact 和自动部署证据。再验证内存不足、I/O PSI 超限、failed unit、高水位 service、
晚间 blackout、仓库 public、token 权限不足、网络访问生产地址均 fail closed。

连续至少 3 个成功 CI 周期后，记录 run ID、镜像 ID、runner 版本、JIT 销毁日志、资源
峰值和回退演练，才可关闭 #304。异常时：删除路由变量，停止两个 timer 和 service，删除
`enabled`/token，撤销 token；一次性容器无需清理复用。

官方依据：

- [Self-hosted runners reference](https://docs.github.com/en/actions/reference/runners/self-hosted-runners)
- [Secure use reference](https://docs.github.com/en/actions/reference/security/secure-use)
- [REST API endpoints for self-hosted runners](https://docs.github.com/en/rest/actions/self-hosted-runners)
- [Job management hooks](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/run-scripts)
