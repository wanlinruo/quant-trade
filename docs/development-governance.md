# qsys 工程化开发与发布规范

状态：拟定，面向 GitHub Free 私有仓库、单人维护模式。合并到 `main` 并完成
本地推送保护、CI 审计与双环境部署后生效。

适用范围：`wanlinruo/quant-trade` 仓库、Mac 开发机、GitHub、Linux 生产节点。

## 1. 不可违反的原则

1. `main` 是唯一生产分支，`main` 的每个提交都必须可部署。
2. `test` 是唯一集成测试分支，允许日常直接 push，并自动部署到测试环境。
3. 禁止直接 push、force-push 或删除 `main`；进入 `main` 的所有变更必须通过 PR。
4. `main` PR 经维护者本人逐页自审、确认 CI 与测试环境验收后合并。
5. Mac 是唯一开发环境；GitHub 是唯一代码中转；Linux 只从 GitHub 拉取。
6. Linux 上禁止手改受 Git 管理的文件。配置、密钥、warehouse 和运行状态不入库。
7. 生产与测试必须使用独立端口、配置、状态库、锁文件和 Redis namespace。
8. 测试环境不得连接真实委托流，不得复用生产账户，不得写生产 warehouse。
9. 所有部署只能快进到已经通过 CI 的精确 commit SHA；禁止部署浮动工作区。
10. 不允许以“紧急”为由绕过；紧急修复仍走 `fix/*` PR。
11. GitHub Free 私有仓库无法提供付费的服务端分支保护，因此 `main` 的“强制”由
    Mac `pre-push` hook、只读 Linux key、PR/CI 审计和发布脚本共同实现；仓库 Owner
    在 GitHub 网页上的最终权限无法由免费方案彻底消除。

## 2. 分支模型

| 分支 | 来源 | PR 目标 | 用途 | 生命周期 |
|---|---|---|---|---|
| `main` | `test` 或紧急 `fix/*` PR | — | 生产真值，手工确认后部署生产 | 永久 |
| `test` | `feat/*`、`fix/*`、同步后的 `main` | `main` | 集成测试，可直接 push，自动部署测试环境 | 永久 |
| `feat/<slug>` | 最新 `test` | 本地 squash 到 `test` | 新需求、功能开发 | 合入后删除 |
| `fix/<slug>` | 通常最新 `test` | 本地 squash 到 `test` | 普通缺陷修复 | 合入后删除 |
| `fix/<slug>` | `main` | `main` | 生产紧急修复 | 合并后必须同步回 `test` |
| `docs/<slug>` | 最新 `test` | 本地 squash 到 `test` | 纯文档 | 合入后删除 |
| `chore/<slug>` | 最新 `test` | 本地 squash 到 `test` | 工具、依赖、运维维护 | 合入后删除 |
| `refactor/<slug>` | 最新 `test` | 本地 squash 到 `test` | 不改变外部行为的重构 | 合入后删除 |

Git 分支名不能以 `/` 开头，所以正确名称是 `feat/xxx`、`fix/xxx`，不是
`/feat/xxx`、`/fix/xxx`。

`<slug>` 只允许小写字母、数字、点、下划线和短横线，必须表达目的，例如：

```text
feat/paper-account-export
fix/live-fill-recovery
docs/deployment-policy
chore/upgrade-fastapi
```

禁止使用 `dev`、`tmp`、`new`、`test2`、人名或日期作为无法说明目的的分支名。

## 3. 标准开发流程

### 3.1 新需求

```bash
git switch test
git pull --ff-only origin test
git switch -c feat/<slug>

# 小步提交、持续测试
uv run pytest
git push -u origin feat/<slug>
```

功能完成后直接集成到 `test`，不创建 PR：

```bash
git switch test
git pull --ff-only origin test
git merge --squash feat/<slug>
git commit -m "feat(scope): 功能摘要"
git push origin test
```

`test` push 后自动运行 CI 并部署测试环境。测试验收通过后，再创建唯一需要人工自审的
`test -> main` 发布 PR。禁止把 `feat/*` 直接合并到 `main`。

### 3.1.1 发布合并后把 `main` 同步回 `test`

仓库只允许 squash merge，`test -> main` 合并会在 `main` 上生成一个新提交，内容与
`test` 相同但历史分叉。合并后立即对齐，否则下一次集成无法快进、节点部署脚本也会拒绝：

```bash
git fetch origin
git diff --quiet origin/test origin/main   # 必须无差异，否则先查清楚
git switch test && git reset --hard origin/main
git push --force-with-lease=test:<合并前 origin/test 的 SHA> origin test
```

`test` 是集成分支，允许在内容一致的前提下强推对齐；`main` 永远禁止强推。
紧急 `fix/* -> main` 合并后 `test` 通常可直接 `git merge --ff-only origin/main`。
测试节点 `/home/ubuntu/qsys-test` 同步后按 `origin/test` 对齐再部署。

### 3.1.2 部署时的重启范围

移动或重命名模块时，**所有**入口脚本都可能受影响，不只本次改动直接涉及的服务。
`tests/scripts/test_scripts_import.py` 导入全部生产入口脚本；新增入口脚本必须加进它的清单。
部署后逐个确认 `systemctl is-active` 与启动日志，不以「本次只改了 paper」为由跳过其他服务。

### 3.2 普通缺陷

从 `test` 创建 `fix/<slug>`，完成后 squash 到 `test` 并直接 push，随下一次
`test -> main` PR 发布。

### 3.3 生产紧急修复

```bash
git switch main
git pull --ff-only origin main
git switch -c fix/<slug>
```

紧急修复 PR 可以直接指向 `main`，但不得跳过测试、自审或生产发布确认。
合并后立即在 Mac 将最新 `main` 合回 `test` 并直接 push；同步完成前暂停新的发布 PR，
防止生产修复在下一次发布中被覆盖。该同步不要求 PR。

### 3.4 提交规范

提交信息采用 Conventional Commits：

```text
feat(scope): 新功能
fix(scope): 缺陷修复
test(scope): 测试补充
docs(scope): 文档
refactor(scope): 重构
chore(scope): 工程维护
```

一次提交只做一类事情。禁止提交密钥、真实账户、`deploy.local.toml`、运行数据库、
warehouse、临时下载、编辑器文件或无解释的大型二进制文件。

## 4. `main` Pull Request 强制规范

日常合入 `test` 不要求 PR。以下要求只针对 `test -> main` 发布 PR 和紧急
`fix/* -> main` PR。

每个 PR 必须包含：

- 目的与背景；
- 变更范围；
- 风险与失败模式；
- 测试证据；
- 数据/schema/配置影响；
- 部署与回滚方式；
- 页面改动截图（如适用）；
- 数据修复前后行数、键集合与校验摘要（如适用）。

合并条件：

1. CI 全绿；
2. 维护者本人必须在 GitHub `Files changed` 中逐文件检查，而不是只看摘要；
3. PR 的“自审完成”清单必须勾选，所有 conversation 已解决；
4. 自审后若又 push 新 commit，必须重新检查变更并重新勾选；
5. PR head 必须基于最新 `main`；
6. 禁止未解释的测试跳过、降级或阈值放宽；
7. 数据或交易路径变更必须给出明确回滚点；
8. `test -> main` PR 必须附测试环境验收记录和测试环境 commit SHA。
   **当前无法满足**：§9.1 的测试环境自动部署从未建成，没有可引用的验收记录。
   在它建成前，发布 PR 以「生产发布后的健康检查 + 页面实测」替代，并在自审清单里
   如实标注本项未满足——不勾选、不伪装成已验收。

仓库只启用 **Squash merge**，关闭 merge commit 和 rebase merge。PR 标题就是最终
提交信息，合并后自动删除源分支，保持线性历史。

## 5. GitHub Free 单人仓库的执行方式

### 5.1 能力边界

GitHub 官方说明：GitHub Free 的受保护分支适用于公开仓库；私有仓库需要 GitHub Pro、
Team 或 Enterprise。当前仓库保持私有且不付费，因此不能把 Ruleset、required approval、
required status checks 或 Environment required reviewer 当作可用的服务端门禁。

PR 作者也不能给自己的 PR 提供“独立批准”。本项目的 Review 定义为：维护者本人创建 PR
后，在 GitHub `Files changed` 中人工审阅、评论、修正，并勾选 PR template 的自审清单。
它是明确的发布检查点，但不伪装成双人审批。

### 5.2 免费模式的四层保护

1. **Mac 本地硬拦截**：版本化 `.githooks/pre-push`，只拒绝向 `main` 直接
   push，并通过 `core.hooksPath=.githooks` 安装。`test` 和工作分支允许推送。
2. **Linux 权限隔离**：Linux 使用 GitHub 只读 deploy key，本身不能 push。
3. **GitHub PR + CI**：`test` push 直接运行 CI；只有进入 `main` 的变更通过 PR 展示
   diff 并运行检查。红灯时规范上禁止合并或部署。
4. **事后审计**：监听 `main` push，检查提交是否关联已合并 PR；发现直接提交
   立即让 workflow 失败并报警，暂停部署。审计只能发现，不能撤销已经发生的 push。

因为仓库 Owner 始终可以在 GitHub 网页直接操作，这套免费方案的安全边界必须写清：
它能可靠阻止日常误操作、留下违规证据并阻断部署，但不能在 GitHub 服务端对 Owner
形成不可绕过的权限墙。若未来需要这种绝对门禁，只能升级套餐、转为公开仓库或迁移到
支持私有仓库保护规则的代码托管服务。

### 5.3 分支与 PR 策略

- `main` PR 来源只允许 `test`；唯一例外是 `fix/*` 紧急修复。
- `test` 不要求 PR；工作分支通过本地 squash commit 集成后直接 push。
- `main` PR 来源规则由 `policy` check 验证；失败时不得手工合并。
- `main` 上出现无关联 PR 的提交时，生产部署脚本必须拒绝该 SHA。

### 5.4 免费可用的仓库设置

- Default branch：`main`
- **关闭**自动删除 head branches：发布 PR 的 head 就是长期分支 `test`，开启后每次合并都会把
  `test` 删掉（2026-09-17 实际发生过）。`feat/*`、`fix/*` 在 squash 进 `test` 后手工删除
- 关闭直接 merge commit
- 关闭 rebase merge
- 开启 squash merge
- 开启 vulnerability alerts、Dependabot alerts 和 secret scanning（免费能力允许时）
- GitHub Actions 只使用免费额度；接近额度上限时优先保证 PR 全量测试，减少重复矩阵
- 不配置 Required reviewer 或付费 Environment protection

GitHub 官方依据：

- [About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
- [Managing protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/managing-a-branch-protection-rule)
- [GitHub Actions billing](https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-actions/about-billing-for-github-actions)

## 6. CI 门禁

`.github/workflows/ci.yml` 在每次 push `test`、以及所有指向 `main` 的 PR 上运行：

| Check | 内容 | 失败后 |
|---|---|---|
| `policy` | 分支名、PR 来源/目标、PR 标题、变更文件策略 | 红灯，禁止人工合并与部署 |
| `test` | Python 3.12、`uv sync --locked`、全量 `pytest` | 红灯，禁止人工合并与部署 |
| `secret-scan` | 无依赖脚本扫描当前受 Git 管理文件中的高风险密钥模式 | 红灯，禁止人工合并与部署 |
| `package` | 构建 wheel、导入包、检查 lockfile 未漂移 | 红灯，禁止人工合并与部署 |

补充规则：

- CI 权限默认 `contents: read`；仅确有需要的 job 提升权限。
- 第三方 Action 固定到完整 commit SHA，不使用浮动 `@main`。
- fork PR 不获得任何部署 secret。
- 测试不得访问真实券商账户、真实订单 Stream 或生产写路径。
- 新增超过 20 MB 的单文件必须在 PR 中解释；超过 GitHub 限制的文件不得进 Git。
- 数据产物优先保存摘要、manifest、hash；不可复现的大文件使用对象存储或 Git LFS。

## 7. 环境与端口规划

### 7.1 固定拓扑

| 环境 | Git 分支 | Linux 目录 | 应用监听 | 访问方式 |
|---|---|---|---|---|
| 生产 | `main` | `/home/ubuntu/qsys` | `127.0.0.1:8420` | `https://wanlinruo.cn:8443/` |
| 测试 | `test` | `/home/ubuntu/qsys-test` | `127.0.0.1:8421` | 仅 Mac SSH 隧道 `127.0.0.1:18421` |

现有 443 已由另一套站点使用，不能改写；qsys 新建独立的 Nginx TLS listener 8443。
8420/8421 永远只绑定 loopback，不直接暴露公网。测试环境不配置公网 Nginx location。

### 7.2 Mac 隧道

```bash
# 测试环境
ssh -N -L 18421:127.0.0.1:8421 \
  -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144

# 必要时绕过公网入口检查生产后端
ssh -N -L 18420:127.0.0.1:8420 \
  -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144
```

浏览器访问 `http://127.0.0.1:18421` 或 `http://127.0.0.1:18420`。

### 7.3 公网生产入口

Nginx 在 8443 复用 `wanlinruo.cn` 证书，反向代理到 `127.0.0.1:8420`。
上线前必须同时完成：

- 云安全组仅开放 TCP 8443；
- TLS 1.2/1.3；
- Nginx 认证（至少强密码 Basic Auth；完成应用 TOTP 后再替换）；
- 登录限流、访问日志、真实 IP 透传；
- 不向公网开放 8420/8421；
- 证书到期监控与续期演练。

当前 qsys Web 含策略、回测和模拟账户写接口，**没有认证时禁止开放 8443**。

**实况（2026-09-17）**：8443 已开通，Nginx Basic Auth、TLS 1.2/1.3、每 IP 限流、
独立访问日志、云安全组放行均已完成，8420/8421 公网不可达；配置与验证记录见
`docs/deploy/linux-core.md`「公网入口」。**尚未完成**：应用层 TOTP 登录与按用户的
登录失败限流、证书到期监控与续期演练。

## 8. 生产与测试隔离

| 项目 | 生产 | 测试 |
|---|---|---|
| 配置 | `config/deploy.local.toml`（prod） | 独立 test 配置 |
| workspace | `/data/qsys/var-prod` | `/data/qsys-test/var` |
| SQLite | 独立 | 独立 |
| lock/kill 文件 | 独立 | 独立 |
| Redis | 生产 DB/stream | 独立 DB 且 stream 加 `test:` 前缀 |
| warehouse | `/data/qsys/warehouse` | 只读快照 `/data/qsys-test/warehouse` |
| 券商订单 | 可由生产安全配置启用 | 永久 disabled，禁止真实账户 |

测试不得通过软链接写生产 warehouse。需要真实数据时，从生产生成带日期和 hash 的只读
快照；测试完成后按保留策略清理。

同一节点内存有限，测试环境默认只运行 Web/回测工作台，不复制生产数据同步器、行情代理、
影子核心或实盘核心。重负载回测仍使用独立 worker，并设置内存/CPU 限制。

## 9. 部署流程

### 9.1 测试环境

> **实况（2026-10-07）：自动部署已建成。** `.github/workflows/deploy-test.yml` 只接收
> `test` push 对应的成功 CI `workflow_run`；PR CI、失败 CI、其他分支和浮动 ref 均不部署。

触发条件：工作分支 squash 到 `test` 并直接 push，随后 CI 全绿。

1. `test` push 的 CI 全绿后，独立特权 workflow 才通过 SSH 调用节点固定入口；
2. Linux 使用只读 deploy key 执行 `git fetch origin test`；
3. 校验待部署 SHA 与 GitHub job 的 SHA 完全一致；
4. 工作区必须干净，执行 `git merge --ff-only origin/test`；
5. GitHub CI 执行 `uv sync --locked`；Linux 因使用腾讯云 PyPI 镜像而执行
   `uv sync --frozen`，精确使用 CI 已验证且入库的 lock，不在节点重写 registry URL；
6. 运行配置检查与 smoke tests；
7. 重启 `qsys-web-test.service`；
8. 检查 `127.0.0.1:8421/healthz`；
9. 通过 Mac 隧道进行人工验收；
10. 以同一 run ID 写节点 `record.json`，校验后回写 GitHub deployment 状态。

Actions 只持有测试专用 SSH key。该 key 在节点 `authorized_keys` 中被固定到工作树外的
`qsys-deploy-ssh`，不能取得 shell 或做端口/agent 转发；dispatcher 只允许查询状态、部署
白名单组件和读取指定 run 的脱敏记录。特权 workflow 从 `main` checkout 可信 planner，
不执行触发它的 `test` 版本控制代码。节点内部再执行
`scripts/deploy_test_linux.sh <完整 commit SHA>`；脚本只接受 `origin/test` 当前 40 位 SHA，
拒绝错误分支和脏工作区。任何配置、smoke 或 health 失败均恢复部署前 SHA、依赖与服务，
并把结果写入 `/data/qsys-test/var/deployments/<run-id>/record.json`。

`/data/qsys-test/warehouse-current` 是只读数据快照，测试服务只有
`/data/qsys-test/var` 的写权限；测试配置固定 `safety.mode=disabled`、独立 Redis DB/namespace
与独立状态路径。

### 9.2 生产环境

> **实况（2026-10-05）：第 1 步的 `workflow_dispatch` 工作流从未建成**，生产发布
> 目前是在 Mac 上手工按第 2–9 步执行（SSH 到节点、`git merge --ff-only` 到 `main`
> 的精确 SHA、`scripts/preflight_linux_deploy.sh`（内部所有 `uv sync/run` 均为
> `--frozen`）、按变更清单只重启受影响服务、
> 查 8420/`healthz` 与公网 8443）。#110 的发布记录见
> `docs/deploy/linux-core.md`「发布记录」。
>
> 另：第 5 步原写「重启 `qsys-web-prod.service`」，节点上不存在该单元——生产 Web
> 是 `qsys-monitor.service`。2026-10-05 在节点实测 15 个 qsys 单元后更正。

触发条件：维护者已完成 `test -> main` PR 自审、CI 全绿、测试环境验收完成且 PR 已合并。

1. 生产不因 `main` push 自动发布；维护者在核对合并结果后手工触发
   `workflow_dispatch`，输入待部署 commit SHA 和确认短语；
2. Linux 拉取 `main` 的精确 SHA，只允许 fast-forward；
3. 保存当前 SHA 作为 rollback target；
4. 安装锁定依赖并执行 preflight；
5. 重启 `qsys-monitor.service`（生产 Web）；
6. 本机检查 `8420/healthz`；
7. 外部检查 `https://wanlinruo.cn:8443/healthz`；
8. 失败则恢复上一个已验证 SHA，并记录 deployment failure；
9. 成功后记录 SHA、操作者、时间、健康检查和回滚点。

只有 Web 代码变化时只重启 Web。数据同步器、paper、shadow、live core 必须根据变更清单
显式选择，禁止每次部署无差别重启全部服务。

### 9.3 部署并发与失败策略

- 每个环境设置独立 GitHub Actions `concurrency`，新部署不得与旧部署并发；
- 生产部署不自动取消正在执行的旧部署；
- 工作区脏、目标 SHA 不符、CI 不绿、preflight 失败时立即停止；
- 部署失败不自动修改数据；涉及 schema/数据迁移时先备份，再单独审批执行；
- 回滚代码不能反向猜测或回滚数据，数据回滚必须有独立方案。

## 10. GitHub Actions 与节点权限

建议 GitHub Actions repository secrets：

- `LINUX_HOST`
- `LINUX_USER`
- `LINUX_SSH_PRIVATE_KEY`：专用部署登录 key，不复用个人日常 key
- `LINUX_HOST_KEY`：固定服务器 host key，禁止运行时 `ssh-keyscan` 后盲信

Linux 上的 GitHub deploy key保持只读。GitHub Actions 登录 Linux 的 key 与 Linux 拉取
GitHub 的 key必须是两把不同的密钥。部署用户只能管理 qsys 目录和指定 systemd unit，
不得获得无约束 root shell；sudoers 只放行固定部署命令。

## 11. 回滚与审计

每次部署至少记录：

- environment；
- branch、commit SHA、PR 编号；
- 发起人、自审完成时间、手工生产发布确认时间；
- 部署前后 SHA；
- 依赖锁 hash；
- smoke/health 结果；
- systemd 重启结果；
- rollback SHA；
- 数据迁移或配置变更摘要。

保留最近 10 个成功发布记录和至少 30 天部署日志。回滚只能回到曾经成功部署且健康检查
通过的 SHA。线上人工修改不构成回滚点。

## 12. 首次落地顺序

1. 从当前 `main` 创建 `test` 并推送。
2. 增加 `.githooks/pre-push`、安装脚本，并在 Mac 实测直接 push `main` 被拒绝、
   push `test` 被允许。
3. 增加 CI、PR template、branch-policy 和直接提交审计 workflow。
4. 配置免费仓库设置：仅 Squash、自动删除 head branch、安全告警。
5. 建立 `/home/ubuntu/qsys-test`、独立配置和 `qsys-web-test.service`，监听 8421。
6. 用 Mac 隧道完成一次 `feat/*` squash 到 `test`、直接 push 和部署验收。
7. 建立手工生产 deployment workflow、确认短语和回滚记录。
8. 为 8443 配置 Nginx TLS、认证与云安全组；未完成认证不得公网开放。
9. 用无业务改动的 PR 完成 `test -> main -> production` 全链路演练。
10. 验收通过后，旧的手工 bundle 发布降为只读应急手册。

## 13. 生效验收

以下条件全部满足，免费单人模式才算生效：

- Mac 直接 push `main` 被 `pre-push` hook 拒绝，push `test` 正常；
- Linux deploy key实测无写权限；
- GitHub 网页直接提交被审计 workflow 识别，并阻断对应 SHA 的部署；
- 不合规的 `main` PR 来源被 `policy` 标红；`test` 集成 commit 标题不合规时 CI 标红；
- 测试失败、secret scan 失败或 lockfile 漂移时不得人工合并，部署脚本也拒绝；
- `main` PR template 留下维护者逐文件自审记录；
- `test` 合并后自动更新 8421，生产不变化；
- `main` 合并后不会自动更新 8420，必须手工输入 SHA 和确认短语触发；
- 生产可通过 `https://wanlinruo.cn:8443/` 安全访问；
- 测试端口只能通过 SSH 隧道访问；
- 两套环境的状态、Redis、warehouse 写路径完全隔离；
- 任一部署失败可恢复到上一个已验证 SHA；
- GitHub deployment 和节点日志能互相对应同一 commit SHA。

这套验收不声称 GitHub Free 私有仓库具备服务端不可绕过的分支保护；它要求的是日常
路径被本地和部署端强制、任何绕过都有审计记录且不能自动进入线上。
