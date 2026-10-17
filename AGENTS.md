# qsys 代理工作约定

本文件是 Claude Code 与 Codex 共用的项目规则唯一来源（`CLAUDE.md` 通过 `@AGENTS.md` 引用它）。
项目规则只改这里；工具专属的内容才写进各自文件。

## 0. 沟通

- 所有面向维护者的回复、进度说明、PR 审批提示一律用**中文**；技术名词、代码、命令保持原样。
- PR 描述与提交信息沿用仓库既有习惯（Conventional Commits，正文中文）。

## 1. 项目与接手入口

qsys 是面向 A 股的个人量化交易系统，**包含真实交易能力**：数据采集与治理、策略研究、
事件驱动回测、模拟/影子/实盘运行、监控告警、Linux/Windows 发布。Python ≥ 3.11，`uv` 管理依赖。

接手任何工作前按顺序读：

1. `HANDOFF.md`：当前生产状态与**不可擅自跨越的授权边界**（快照会过期，写操作前重新核验）。
2. `docs/development-governance.md`：分支模型、提交与发布规范。
3. 涉及数据：`docs/reference/data-source-migration-record.md`（每张表的真值源、换源过程）
   和 `docs/reference/deferred-corrections.md`（待办 D-x 清单）。
4. 其他专题：`docs/reference/README.md` 是参考资料索引，先查再动手，别重新摸索。

信息冲突时的优先级：维护者最新明确指示 → GitHub Issue 最新记录 → 当前 workflow / 节点入口与
manifest → `HANDOFF.md` → 其他历史文档。

## 2. 开发流程

- `main` 是唯一生产分支，禁止直接 push / force-push；`test` 是集成分支。
- 新需求从最新 `test` 开 `feat/<slug>`（缺陷用 `fix/<slug>`，文档用 `docs/<slug>`），
  完成后在本地 `git merge --squash` 到 `test` 再 push；只有 `test -> main` 需要 PR。
- 测试：`uv run pytest`。新增生产入口脚本必须加进 `tests/scripts/test_scripts_import.py` 的清单。
- 提交一次只做一类事。禁止提交密钥、真实账户、`deploy.local.toml`、运行数据库、warehouse、临时下载。
- 细节以 `docs/development-governance.md` 为准。

## 3. 数据铁律（无例外）

1. **禁止任何反推、猜测、填充。** 字段只允许两类：**直取**（数据源接口原值）和**纯派生**
   （直取列的确定性算术）。拿不到就记 `null`，交给质检报出来。默认值、近似替代、
   兼容性单位转换（如股/100 转手）同样禁止。维护者会用同花顺随机抽样验真。
2. 不确定的事实（接口列名、计费口径、成交量单位）先写探针脚本实测，结论落盘进 git，
   再写依赖它的代码；未确认前显式抛错，不给默认值。会造数据的接口参数默认值要显式写死并加测试。
3. **入库门禁只看质量，不看来源**：查正确性（跨源 / 日线对账）、有效性（网格、价格、单位）、
   完整性（覆盖、缺口）。来源只用于既定优先级选源，不要为了"证明来源"加阻断规则或迁移数据。
   补缺只能用真实直取数据。
4. **数据正确性优先于阈值稳定性**：修正数据导致派生指标偏移时照常修，再按新数据重新标定阈值。
   重新标定是独立工作，不要夹带进数据层任务；等数据全部修完再标定一次。
   冻结阈值在 `src/qsys/strategy/structural/params.py`（S6_* 常量），聚宽策略文件里另有一份副本。

### 3.1 ST、涨跌停、退市整理期

- **ST 状态只认 Tushare `stock_st`**（仓库表 `stock_st_daily.is_st`），绝不从名称、价格或
  涨跌幅档位推算。2026-07-06 起主板 ST 涨跌幅已改为 10%，价格档位再也判不出 ST。
- 涨跌停价只认 `stk_limit` 直取；没有直取行不按板块档位自算。`daily_flags.limit_pct` 是从
  直取涨停价解码的档位，**不要拿它判 ST**。
- 退市整理期只认 `namechange.change_reason='退市整理期'`，不从名称找"退"字。
- 候选池纳入 ST 首板，ST 炸板不进（有意偏离聚宽原文）。

### 3.2 数据源现状

- 日线真值源：Tushare Pro API 直连（`tushare_daily`），`bars_daily` 已冻结
  （读取入口 `legacy_daily_bars()`）。mootdx K 线接口上游已失效，不要再调。
- **`paid_daily` 不能删**：2026-09-11 及之前的逐日名称真值只有它，删了会静默丢掉 867 个首板。
- canonical `bar_minute_1m` 的"首根成交量为 0"问题（D-23）归因未完成，**禁止整体当填充删除**，
  了结前不得作为回测分钟源切换目标。

## 4. 生产操作安全

- **不可逆操作前必须停下等维护者确认**：删除 / 覆盖生产数据、对外发布、删除回滚副本、
  触发 `dry_run=false`、生产故障注入演练。"继续""按建议处理"不算对这类操作的确认。
  其余流程（开分支、测试、squash、建 PR、代做自审）可以一路推进；代做自审必须在 PR 正文
  如实写明是代行，并且要真查。
- 往节点同步代码**禁止整目录 rsync**（会毁掉 `warehouse` 软链接和 `.venv`）：逐个列文件，
  或带 `--exclude '.venv/' --exclude 'warehouse/' --exclude '.git/' --exclude 'var/'`。
  动节点前先 `ls -la` 看清目标是文件、目录还是软链接。
- 生产节点跑 DuckDB：必须 `SET memory_limit`、`SET temp_directory`（指向可控目录）、
  `SET max_temp_directory_size`；用流式聚合，禁止把整年分钟数据物化成临时表；
  加 `nice` / `ionice -c3` / `timeout`；工作目录不要设在 `/home/ubuntu/qsys`。
- 有时间窗口的生产操作用节点侧 `systemd-run --on-calendar` 调度，不要在 Mac 上等待后再 SSH
  （Mac 会休眠）。执行前后在 `/data/qsys/var-deployments/` 留记录。
- `/data/qsys` 下 warehouse 之外的目录（`minute-rebuild`、`qmt-minute-2026`、`test-env`）
  不在资产盘点里，清理前逐项读内容核对，不按目录名判断。
  `/data/qsys/minute-rebuild` 里的付费源 2026 分钟暂存区可能是**唯一副本**，不要删。
  统计释放空间要按 inode 去重。
- 不得关闭 SSH 密码登录、删除个人 SSH key，或改动 Windows Administrator 密码、RDP、
  Autologon、QMT 自动恢复配置。
- 不得把密码、私钥、token、完整配置写入 Git、Issue、Actions 日志或文档。

## 5. 跨工具 / 跨账号交接

本项目会在 Claude Code 与 Codex（以及不同账号）之间切换，会话历史互不相通：

- 新发现的**长期项目规则**写进本文件，不要只存在某个工具的私有记忆里。
- 进行中的工作在停下或切换工具前，把进展、未决问题、下一步写进对应文档
  （`docs/reference/deferred-corrections.md`、相关 Issue 或计划文档），接手方先读这些再动手。
