# 生产 Linux 发布

执行后续发布前，先阅读仓库根目录 `HANDOFF.md` 和 GitHub #154 最新评论。
本文描述稳定流程；具体 SHA、run ID 和待授权操作以交接快照及实时检查为准。

生产发布只通过 GitHub Actions 的 `Deploy production Linux` 手工触发。合并或推送
`main` 不会自动修改生产节点。

## 发布输入

- `target_sha`：`main` 历史中的完整 40 位 SHA。
- `components`：默认 `auto`；显式填写时必须与 manifest 计算出的 Linux 组件完全一致。
- `dry_run`：默认开启，只验证门禁并输出计划。
- `confirmation`：固定填写 `DEPLOY_PRODUCTION`。
- `release_id`：Linux/Windows 共用的发布关联 ID；节点记录自动增加节点后缀。
- 三个人工确认项：业务风险、迁移方案和外部验收负责人均已核对。
- `failure_injection`：日常必须为 `none`；仅按演练清单使用。

正式发布前必须先成功执行同一目标的 dry-run。GitHub workflow 和节点固定入口会分别
校验目标来源；节点还会按 Git tree 查找测试环境的成功发布记录，避免 `test -> main`
squash 后 SHA 改变导致证据断链。

## 权限边界

GitHub production environment 使用独立 Ed25519 密钥。公钥在 `ubuntu` 的
`authorized_keys` 中带 `restrict` 和固定命令，只能调用生产部署入口，不能获得 shell；
testing key 也不能调用生产入口。

这套机制不会关闭或替换维护者的密码登录、个人 SSH key 登录，也不调整 Windows 的
密码、RDP 或 Autologon。部署密钥轮换只替换带 `qsys-github-production` 标记的单独一行。

## 执行与回滚

节点只重启 manifest 判定为受影响且发布前已经处于 active 的服务；不会启动原本
inactive 的服务。发布后校验受影响服务和本机 `8420/healthz`。代码、依赖安装或健康
检查失败时，自动恢复发布前 SHA、同步旧依赖并重启同一组服务。数据和 schema 迁移
不在自动反向回滚范围内，因此包含迁移的发布必须另行制定操作方案。

每次正式发布写 GitHub Deployment、Actions Summary 和节点 `record.json`。dry-run
只产生 Actions 日志与 Summary，不冒充一次生产 Deployment。

完整机器/人工清单、故障注入和跨节点补偿顺序见
[production-release-checklist.md](production-release-checklist.md)。跨节点发布必须 Linux
先行、Windows 后行；不得自行交换顺序。

## 应急访问

日常不再通过交互式 SSH 执行 `git reset`、依赖同步或服务重启。维护者的密码/密钥
SSH 保留用于只读排查，例如：

```bash
git -C /home/ubuntu/qsys status --short --branch
systemctl status qsys-monitor.service --no-pager
curl --fail http://127.0.0.1:8420/healthz
```

若自动入口本身不可用，先保留现场和发布记录，再由维护者明确批准应急写操作。该方式明确
属于应急路径，恢复后必须补写审计证据，不能替代标准 workflow。

## 2026-10-19 归档与健康看板修复（#434 / #435 / #436）

本轮发现每日归档 unit 在 Python 启动前因两个 `ReadWritePaths` 不存在报
`226/NAMESPACE`。固定入口 v29 为 legacy daily-archive 精确准备工作区与记录目录，
保留既有状态，不递归更改 owner；拒绝路径中的软链接。所有本轮安装的 service 均在重启
前及后验阶段检查有效的必需 `ReadWritePaths`，包括当前 inactive 的 oneshot。
`ExecStartPre` 晚于 mount namespace 建立，不能代替这项准备。

发布顺序与验收：

1. 正常完成 test CI、测试部署、发布 PR 与逐文件自审。先核验可信 main planner 分类。
2. 正式操作须另获维护者针对本轮的明确确认。核对 main 精确 SHA、生产干净、任务锁和
   timer；在 `/data/qsys/var-deployments/issue434-436-<日期>/` 保存前后状态及旧 unit/控制器。
3. 从已合并的精确版本安装固定入口 v29；确认 `systemd-runtime-paths-v1` 能力后，先 dry-run，
   再按批准的范围正式发布 platform/shared checkout。独立 Web、health-observer、
   catalog-verify 使用同一 SHA 各自构建的不可变制品，通过组件 workflow 分别发布。
4. 每个 workflow 最终成功、Deployment 和经实际 validator 校验的节点 record 三者一致后，
   才进入现场验收。核对归档两个目录的 owner/遍历/写权限及旧 Web/日报的记录可读性。
   本轮 manifest 还会选择 linux-resident：共享发布按现有规则重启发布前 active 的
   Live Core、Paper、Web 和 observer；须在收盘维护窗口执行并核验委托、持仓与心跳，
   不把这次共享发布描述为“完全不触及实盘进程”。
5. 今日补采会写 warehouse；它与代码发布分别审批。先锁定目标交易日、缺失键、
   可能被替换的年度/日分区及备份 hash，再按实际 unit 参数执行归档，不启动重复实例，
   不与 tushare-sync 抢锁。跨源质检与新旧记录读取通过才算数据恢复。
6. 代码回滚恢复旧 unit/版本指针；保留新增状态和失败记录。数据恢复不随代码自动反向执行。

Web 看板读取 observer 新鲜的 `systemd_execution` 退出状态；启动失败按真实退出日期
展示并链接服务详情，不编造 ServiceRun。应先更新 observer 再验收 Web，旧 observer
没有新增字段时只显示原有状态。核验 `226`、正常 inactive、旧日失败、成功恢复与缺测，
保持 Web 只读权限。早盘实盘 failed 策略事件不因本轮修复改写。

表级健康刷新接入 catalog-verify，按一个真实交易日重算现有 **12 张规范表**及 7 条
必需关系。它只写 `dataset_checks`/`relation_checks`，不发布业务表、不切换 canonical
读取、不延长 TTL；其他表继续缺证据。分钟线按日重建比较输入，并在有 DuckDB 预算的
流式 full join 中汇总缺键/字段差异，不为每条分钟构造 Python 字典。CLI 分阶段使用
512 MiB DuckDB 内存、1 GiB spill、单线程与私有临时目录，service 另有 3 GiB cgroup、
CPU/IO 优先级限制并从状态目录运行。首次现场资源实测、真实红黄灰原因及消费者读取
未通过前，#436 不得按“所有表健康”关闭。

页面验证使用本地合成故障样例，不代表已部署生产：
[桌面看板](screenshots/service-start-failure-desktop.png)、
[390px 移动端详情](screenshots/service-start-failure-mobile.png)。

2026-10-19 真实输入隔离验证（只读生产、写隔离输出）：12 表重算 16.70 秒，
进程 RSS 峰值 951,616 KiB；分钟 1,341,565 行新旧逐键一致，6 个非标准根数标的日
保留黄色，分钟发布超过既定 TTL 如实红色。首次字段全量验证在 3 GiB 下 OOM，
5 GiB 下 180 秒超时；定位为 14 个生态指标重复物化全历史因子。修复为保留真实前序
观测的 SQL 窗口后按区间输出，并仅在同一检查批次复用小结果，新批次重新计算。
修复后同一真实 10-16 输入在 3 GiB 下 40.18 秒完成，RSS 峰值 1,017,848 KiB，
636 条结果、阻断 0、既有警告 28。原始失败和成功证据均保留在
`/data/qsys/var-deployments/issue434-436-20261019/`；这证明隔离验证可运行，
不替代新制品正式部署、今天数据补采及首次正式 timer 验收。
