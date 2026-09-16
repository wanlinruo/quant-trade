# qsys 工程化开发与发布规范

状态：拟定，合并到 `main` 并完成 GitHub Ruleset 与双环境部署后强制生效。

适用范围：`wanlinruo/quant-trade` 仓库、Mac 开发机、GitHub、Linux 生产节点。

## 1. 不可违反的原则

1. `main` 是唯一生产分支，`main` 的每个提交都必须可部署。
2. `test` 是唯一集成测试分支，不能承载只存在于服务器上的修改。
3. 禁止直接 push、force-push 或删除 `main`、`test`；所有变更必须通过 PR。
4. “只支持 PR”是指禁止直接推送；PR 通过 Review 和 CI 后仍需执行合并。
5. Mac 是唯一开发环境；GitHub 是唯一代码中转；Linux 只从 GitHub 拉取。
6. Linux 上禁止手改受 Git 管理的文件。配置、密钥、warehouse 和运行状态不入库。
7. 生产与测试必须使用独立端口、配置、状态库、锁文件和 Redis namespace。
8. 测试环境不得连接真实委托流，不得复用生产账户，不得写生产 warehouse。
9. 所有部署只能快进到已经通过 CI 的精确 commit SHA；禁止部署浮动工作区。
10. 规则对管理员同样生效，不允许以“紧急”为由绕过；紧急修复走 `fix/*` PR。

## 2. 分支模型

| 分支 | 来源 | PR 目标 | 用途 | 生命周期 |
|---|---|---|---|---|
| `main` | `test` 或紧急 `fix/*` | — | 生产真值，自动部署生产 | 永久 |
| `test` | `feat/*`、`fix/*`、同步后的 `main` | `main` | 集成测试，自动部署测试环境 | 永久 |
| `feat/<slug>` | 最新 `test` | `test` | 新需求、功能开发 | PR 合并后删除 |
| `fix/<slug>` | 通常最新 `test` | `test` | 普通缺陷修复 | PR 合并后删除 |
| `fix/<slug>` | `main` | `main` | 生产紧急修复 | 合并后必须同步回 `test` |
| `docs/<slug>` | 最新 `test` | `test` | 纯文档 | PR 合并后删除 |
| `chore/<slug>` | 最新 `test` | `test` | 工具、依赖、运维维护 | PR 合并后删除 |
| `refactor/<slug>` | 最新 `test` | `test` | 不改变外部行为的重构 | PR 合并后删除 |

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

然后创建 `feat/<slug> -> test` 的 PR。PR 合并并在测试环境验收后，再创建
`test -> main` 的发布 PR。禁止把 `feat/*` 直接合并到 `main`。

### 3.2 普通缺陷

从 `test` 创建 `fix/<slug>`，PR 回 `test`，随下一次 `test -> main` 发布。

### 3.3 生产紧急修复

```bash
git switch main
git pull --ff-only origin main
git switch -c fix/<slug>
```

紧急修复 PR 可以直接指向 `main`，但不得跳过测试、Review 或生产部署审批。
合并后立即创建 `main -> test` 同步 PR；在该同步 PR 合并前，暂停新的发布 PR，
防止生产修复在下一次发布中被覆盖。

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

## 4. Pull Request 强制规范

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
2. 至少 1 名非 PR 作者 Reviewer 批准；
3. 所有 Review conversation 已解决；
4. 新 commit 会使旧批准失效，必须重新 Review；
5. PR head 必须基于目标分支最新版本；
6. 禁止未解释的测试跳过、降级或阈值放宽；
7. 数据或交易路径变更必须给出明确回滚点；
8. `test -> main` PR 必须附测试环境验收记录。

仓库只启用 **Squash merge**，关闭 merge commit 和 rebase merge。PR 标题就是最终
提交信息，合并后自动删除源分支，保持线性历史。

## 5. GitHub 强制设置

> 私有仓库的受保护分支/Ruleset 需要支持该能力的 GitHub 套餐。若当前账户不支持，
> 必须升级套餐或调整仓库可见性；不能用“大家自觉”替代服务端强制。

### 5.1 `main` Ruleset：`production-main`

- Enforcement status：`Active`
- Target：`main`
- Restrict deletions：开启
- Block force pushes：开启
- Require linear history：开启
- Require a pull request before merging：开启
- Required approvals：`1`
- Dismiss stale approvals：开启
- Require approval of the most recent reviewable push：开启
- Require conversation resolution：开启
- Require status checks：开启，且分支必须 up to date
- Required checks：`policy`、`test`、`secret-scan`、`package`
- Do not allow bypassing：开启，包含管理员
- Allowed merge method：仅 Squash

`main` 的 PR 来源策略：只允许 `test`；唯一例外是名称符合 `fix/*` 的紧急修复。
该限制由 `policy` CI 检查强制执行。

### 5.2 `test` Ruleset：`integration-test`

除“PR 来源策略”外，与 `main` 使用同等级保护：禁止直接 push/force-push/delete，
要求 1 个批准、conversation 全解决、严格状态检查和线性历史。

允许的 PR 来源：`feat/*`、`fix/*`、`docs/*`、`chore/*`、`refactor/*` 和 `main`。

### 5.3 Reviewer 硬前提

至少要有第二个具有 read/review 权限的 GitHub 用户或团队。PR 作者不能把自己的批准
当成独立 Review。若暂时只有一个账号：Ruleset 先启用 PR、CI、conversation 和禁止
bypass，但发布状态必须标记为“Review 门禁未完全落地”，不得宣称规范已全部生效。

### 5.4 仓库设置

- Default branch：`main`
- 开启自动删除 head branches
- 关闭直接 merge commit
- 关闭 rebase merge
- 开启 squash merge
- 开启 vulnerability alerts、Dependabot alerts 和 secret scanning（套餐支持时）
- 创建 `test`、`production` 两个 GitHub Environment
- `production` 开启 Required reviewer 和 Prevent self-review
- Environment deployment branches：`test` 只允许 `test`，`production` 只允许 `main`

GitHub 官方依据：

- [About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
- [Managing rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets)
- [Deployment environments](https://docs.github.com/en/actions/concepts/workflows-and-actions/deployment-environments)

## 6. CI 门禁

`.github/workflows/ci.yml` 对所有指向 `main`/`test` 的 PR 运行：

| Check | 内容 | 失败后 |
|---|---|---|
| `policy` | 分支名、PR 来源/目标、PR 标题、变更文件策略 | 禁止合并 |
| `test` | Python 3.11/3.12、`uv sync --locked`、全量 `pytest` | 禁止合并 |
| `secret-scan` | gitleaks/同等级工具扫描新增历史 | 禁止合并 |
| `package` | 构建 wheel、导入包、检查 lockfile 未漂移 | 禁止合并 |

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
| 生产 | `main` | `/home/ubuntu/qsys-prod` | `127.0.0.1:8420` | `https://wanlinruo.cn:8443/` |
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

触发条件：PR 合并到 `test` 且 CI 全绿。

1. GitHub `test` Environment job 通过 SSH 调用节点部署脚本；
2. Linux 使用只读 deploy key 执行 `git fetch origin test`；
3. 校验待部署 SHA 与 GitHub job 的 SHA 完全一致；
4. 工作区必须干净，执行 `git merge --ff-only origin/test`；
5. `uv sync --locked`；
6. 运行配置检查与 smoke tests；
7. 重启 `qsys-web-test.service`；
8. 检查 `127.0.0.1:8421/healthz`；
9. 通过 Mac 隧道进行人工验收；
10. 回写 GitHub deployment 状态。

### 9.2 生产环境

触发条件：`test -> main` PR 已批准、CI 全绿、测试环境验收完成且 PR 已合并。

1. GitHub `production` Environment 等待独立 Reviewer 批准；
2. Linux 拉取 `main` 的精确 SHA，只允许 fast-forward；
3. 保存当前 SHA 作为 rollback target；
4. 安装锁定依赖并执行 preflight；
5. 重启 `qsys-web-prod.service`；
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

建议 GitHub Environment secrets：

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
- 发起人、Reviewer、批准时间；
- 部署前后 SHA；
- 依赖锁 hash；
- smoke/health 结果；
- systemd 重启结果；
- rollback SHA；
- 数据迁移或配置变更摘要。

保留最近 10 个成功发布记录和至少 30 天部署日志。回滚只能回到曾经成功部署且健康检查
通过的 SHA。线上人工修改不构成回滚点。

## 12. 首次落地顺序

1. 确认 GitHub 套餐支持私有仓库 Ruleset/保护分支。
2. 邀请至少一名独立 Reviewer。
3. 从当前 `main` 创建 `test` 并推送。
4. 增加 CI、PR template、CODEOWNERS、branch-policy 检查。
5. CI 首次成功后再把 status checks 设为 required，避免先锁死仓库。
6. 创建并启用 `test` Ruleset。
7. 创建并启用 `main` Ruleset；管理员不得 bypass。
8. 建立 `/home/ubuntu/qsys-test`、独立配置和 `qsys-web-test.service`，监听 8421。
9. 用 Mac 隧道完成一次 `feat/* -> test` 部署验收。
10. 建立生产 deployment job、审批和回滚记录。
11. 为 8443 配置 Nginx TLS、认证与云安全组；未完成认证不得公网开放。
12. 用无业务改动的 PR 完成 `test -> main -> production` 全链路演练。
13. 验收通过后，旧的手工 bundle 发布降为只读应急手册。

## 13. 生效验收

以下条件全部满足，规范才算真正“强制执行”：

- 直接 push `main` 和 `test` 均被 GitHub 拒绝；
- force-push/delete 被拒绝，管理员也不能绕过；
- 不合规分支名或 PR 来源被 `policy` 阻断；
- 测试失败、secret scan 失败或 lockfile 漂移时不能合并；
- PR 作者不能自己完成所需 Review；
- `test` 合并后自动更新 8421，生产不变化；
- `main` 合并后必须经过 production approval 才更新 8420；
- 生产可通过 `https://wanlinruo.cn:8443/` 安全访问；
- 测试端口只能通过 SSH 隧道访问；
- 两套环境的状态、Redis、warehouse 写路径完全隔离；
- 任一部署失败可恢复到上一个已验证 SHA；
- GitHub deployment 和节点日志能互相对应同一 commit SHA。

