# 节点存储保留策略

生产 warehouse 在单独挂载的数据盘 `/dev/vdb`（`/data/qsys`，99 GiB），不是系统盘
`/dev/vda2`。2026-10-13 盘点时数据盘已用 69 GiB、剩余 25 GiB（74%）。

规则登记在 `src/qsys/ops/storage.py` 的 `RULES`，盘点命令：

```bash
uv run python scripts/storage_report.py            # 默认 /data/qsys 与 ~/scratch
uv run python scripts/storage_report.py --json out.json
uv run python scripts/storage_report.py --history-root var/storage-history
```

`qsys-storage-report.timer` 每天（含休市日）18:20 生成一份
`var/storage-history/YYYY/MM/DD/storage-*.json`。快照包含磁盘总量、剩余空间、阈值、
保留策略清单和数据盘分类占用；分类统计在 `/data/qsys` 范围按 inode 去重，共享给
warehouse、测试快照和隔离构建目录的硬链接只计一次，并优先归入生产仓库。
`~/scratch` 仍参与保留策略检查，但不混入数据盘分类合计。工具始终只读业务数据。

组件制品入口为 `qsys-storage-report`；旧的 `scripts/storage_report.py` 仅是兼容包装器。
独立发布后从 `/opt/qsys/components/storage-report/current` 执行，账号仅能写
`var/storage-history`，对 `/data/qsys` 与 scratch 保持只读。代码合入不代表生产已切换。

## 保留规则

| 位置 | 类别 | 策略 |
|---|---|---|
| `warehouse/backups/*`、`warehouse/*.pre-*`、`backups/*` | 操作回滚副本 | 最近修改 30 天后可申请删除 |
| `baidupan` | 付费源下载区 | 永久；只留下载清单与日志，原始数据文件入库并核验无损后删除 |
| `minute-rebuild/warehouse/paid_minute_bars_staging`、`paid_minute_quarantine`、`paid_minute_quality` | 付费分钟原始等价物 | 永久；2026 原始 CSV 已于 2026-09-22 核验后删除，逐日源 SHA256 在 manifest |
| `minute-rebuild` 其他 | 重建暂存区 | 30 天 |
| `qmt-minute-2026` | 隔离构建仓库 | 30 天，已发布进生产 |
| `repair-cache` | 修复缓存 | 30 天 |
| `test-env/warehouse-snapshots/*`、`test-env/snapshots/*` | 测试快照 | 保留最新 2 份；`test-env/warehouse-current` 指向的快照始终保留 |
| `test-env/var`、`var-deployments`、`var-prod` | 运行状态与记录 | 永久 |
| `catalog-verify-state` | 冻结源验证基线 | 永久，不随临时工作区清理 |
| `test-env/research-jobs` | 隔离研究请求、状态、日志与产物 | 永久保留，待独立清理协议 |
| `test-env/warehouse-snapshot-store` | 隔离研究不可变快照 | 永久保留，不能套用旧测试快照的最新 2 份规则 |
| `~/scratch/*` | 临时工作区 | 最近修改 14 天后可申请删除 |

`/data/qsys` 顶层出现任何未登记的条目都会被报出，新目录必须先在 `RULES` 登记策略。
warehouse 内的数据集由指标字典与质量规则管理，不在本策略内。规则按登记顺序认领，
具体规则写在通用规则之前。

上述三个精确路径由 [#465](https://github.com/wanlinruo/quant-trade/issues/465) 补登记。
研究作业绑定固定 snapshot_id，不能只保护 current 而删除历史作业引用的快照；
制定引用感知的清理协议并取得删除授权前，保留整个作业与快照目录。未登记的新目录仍照常告警。

`moneyflow_daily` 等规范事实的历史分区不参与 30 天回滚副本清理；
`industry_membership` 按需全表原子刷新并保留当前完整 PIT 表；隔离态
`auction_open_daily` 在 #234 通过前必须保持 0 文件。三者继承 #113 裁决的系统级
快照外部责任，不在本保留工具内另建资产级备份或恢复副本。

## 占用与可释放

盘点给出两个数：**占用**是目录内文件去重后的磁盘块；**可释放**只计所有硬链接都在
目录内的 inode，即删除后真正腾出的空间。测试快照与 `*.pre-*` 副本大量硬链接到
warehouse：2026-09-21 实测，占用 25 GiB 的测试快照可释放约 0.1 GiB，
`paid_minute_bars.pre-d20-d578a85` 占用 22 GiB、可释放 0。真正独占空间的是付费原始包
（17 GiB）与重建暂存区（2.6 GiB）；前者已于 2026-09-22 删除（审计记录 12.4）。

## 容量告警

每日服务质检（`service-checks`，交易日 20:00）包含两项：

- **磁盘剩余容量**：数据盘低于 20 GiB 告警，低于 10 GiB 判失败（与 canonical 回填
  空间守卫一致）；
- **存储保留策略**：有到期项或未登记目录时告警，列出名称与可释放空间。

剩余空间低于 15 GiB 时必须启动 100 GiB → 200 GiB 扩盘评估；这是扩容决策阈值，
不会触发自动清理或自动扩盘。20 GiB 的提前告警仍然保留。

容量趋势至少连续积累 30 天后再用于外推耗尽时间；不足 30 天时只能显示观测区间，
不能把短期部署备份或一次性回填机械年化。

## 删除与审批

工具只读，**不自动删除**。到期项按以下流程处理：

1. 运行 `storage_report.py --json` 生成当期清单；
2. 维护者逐项批准，批准范围写进 `/data/qsys/var-deployments/<时间>-storage-prune/approval.txt`
   （路径、可释放空间、批准人、日期）；
3. 删除前再确认该项仍是 `eligible`，删除后重新盘点，报告存入同一目录。

回滚副本删除后无法再回到对应变更之前的状态；涉及生产数据修复的副本，至少在修复
被一次完整的每日验证与服务质检确认后再批准。
