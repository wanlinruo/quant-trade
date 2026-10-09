# 生产发布 checklist 与跨节点补偿

本清单是生产发布的标准入口。Linux 与 Windows workflow 使用同一个 `release_id`，节点记录
分别使用 `<release_id>-linux`、`<release_id>-windows`；GitHub Deployment description、
Actions Summary 和节点 record 都包含这个关联键。旧的交互式 SSH 操作只作为自动入口不可用时
的应急路径。

## 发布前确认

机器项由 workflow 和固定节点入口失败关闭：

- 目标是 `origin/main` 的完整 SHA，来源 PR 已合并，四个 CI check 成功；
- 相同 Git tree 存在成功 test deployment；
- 节点位于干净 `main`，固定入口能力版本满足 workflow；
- 配置只写脱敏 fingerprint，磁盘至少 5 GiB、可用内存至少 512 MiB；
- 当前版本存在成功 production record，可作为 rollback target；
- 交易日 08:45–15:40 禁止重启，确认短语不能绕过；
- Linux unit、Windows 任务、进程、心跳和 watchdog 在切换后验证。
- Windows 计划以每个应用实际 `DEPLOYED_SHA` 为基线分别计算后取并集，不得借用 Linux SHA；
  两个节点或两个 Windows 应用暂时版本不同时仍能生成准确计划。

触发人还必须显式勾选业务风险、数据/schema 迁移、影响范围与外部验收负责人。迁移只能填写
“无”或指向另行批准的方案；代码回滚不推断数据回滚。私有仓库当前没有 required reviewer，
这些选项是可审计的单人确认，不描述为双人审批。

每次先用相同 target 和 release ID 执行 dry-run，再执行正式发布。故障注入只接受
`drill-*` release ID 和 `DRILL_ROLLBACK`，普通发布无法启用。

## 固定跨节点顺序

跨节点版本固定按以下顺序执行，不引入隐藏的中央 orchestrator：

1. 执行 Linux workflow；失败时 Linux 自动恢复旧 SHA，停止，不启动 Windows。
2. Linux 成功并验证外部页面后，执行 Windows workflow。
3. Windows 切换失败时，Windows 控制器先恢复旧 release、agent/gateway 任务、进程、心跳和
   watchdog；其 `rolled_back` record 验证通过后，立即补偿 Linux。
4. Linux 补偿使用标准 Linux workflow，将 `target_sha` 设为第 1 步 record 的
   `before.linux`，release ID 使用 `<原 release_id>-compensate`。旧 SHA 必须仍是 main 祖先、
   有 test tree 证据且是成功 rollback target，否则失败关闭并升级为人工应急。
5. 补偿结束后确认 Linux `after.linux` 等于旧 SHA，Windows record 中所有 `after` 等于
   `before`，再检查内外 health、任务、进程、心跳和 watchdog。完成前发布状态是
   “补偿中”，不得宣告成功。

这个顺序覆盖唯一需要跨节点补偿的情况：Linux 成功而 Windows 失败。反向顺序会在 Linux
失败后留下已切换的 Windows，因此禁止。

## 演练与证据

- Linux：`failure_injection=linux-health`，在代码/依赖切换和 unit 重启后、健康检查前失败；
- Windows：`failure_injection=windows-switch`，第一个应用目录切换后失败；
- 两者均必须产生 `rolled_back` record，GitHub Deployment 标记 `inactive`，workflow 本身仅在
  验证恢复结果后成功；
- 跨节点演练按上节执行，并保存原发布、Windows 回滚和 Linux compensation 三份记录；
- #185 的缺证据、tree 不同、Deployment/record 非成功、controller/workflow 变化与 lease
  冲突均应回退全量验证或失败关闭，不能把快速通道成功当成演练成功。

`history` 受限命令与 `/data/qsys/var-deployments/success-index.json` 提供最近 10 个成功版本和
近 30 天成功记录视图。原始 `var-deployments` 按永久保留策略不自动清理，因此同时满足
“最近 10 个”和“至少 30 天”；任何清理都必须走独立存储审批。

## 应急路径

只有固定入口、GitHub Actions 或节点控制器自身损坏且无法按标准路径补偿时，才可启用
交互式 SSH/RDP。先保存 Actions URL、Deployment ID、节点 record、当前 SHA、任务与心跳现场，
再由维护者明确批准写操作。应急恢复后仍须补写 record/Issue 证据，并在标准入口执行一次
只读状态和 dry-run；手工 `git reset`、目录替换或任务启停不属于日常发布流程。
