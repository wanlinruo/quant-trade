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
   和 `docs/reference/deferred-corrections.md`（D-1~D-24 历史事实与结论，**不是待办清单**）。
4. 其他专题：`docs/reference/README.md` 是参考资料索引，先查再动手，别重新摸索。

信息冲突时的优先级：维护者最新明确指示 → GitHub Issue 最新记录 → 当前 workflow / 节点入口与
manifest → `HANDOFF.md` → 其他历史文档。

## 2. 开发流程

- **需求、缺陷、待办一律走 GitHub Issue**（维护者 2026-10-20 要求）：新发现的问题直接开 Issue
  （带 `priority:*`、`area:*`、`status:*` 标签），进度与结论记在 Issue 评论里。
  `deferred-corrections.md` 只保留既有 D-x 的历史事实与裁决，**不再新增 D 编号、不在其中登记待办**；
  需要沉淀的数据事实可写进对应参考文档，但必须链接承接它的 Issue。
- `main` 是唯一生产分支，禁止直接 push / force-push；`test` 是集成分支。
- 新需求从最新 `test` 开 `feat/<slug>`（缺陷用 `fix/<slug>`，文档用 `docs/<slug>`），
  完成后在本地 `git merge --squash` 到 `test` 再 push；只有 `test -> main` 需要 PR。
- 测试：`uv run pytest`。新增生产入口脚本必须加进 `tests/scripts/test_scripts_import.py` 的清单。
- 提交一次只做一类事。禁止提交密钥、真实账户、`deploy.local.toml`、运行数据库、warehouse、临时下载。
- 细节以 `docs/development-governance.md` 为准。

### 2.1 发布前检查与避免重复验证（2026-10-18 复盘）

- **先检查可信控制面，再安排发布顺序**：测试部署使用 `main` 上的 planner，不能只验证
  `test` 的 manifest。新增受 Git 管理的路径时，同时检查目标分支和可信 `main` 的分类；
  文档只能精确加入非运行时路径，不用宽泛通配放过未知路径。若需要控制面引导修复，先按
  既有分支规范完成最小修复，再推进依赖它的发布，不把普通需求包装成紧急修复。
- **同步提交也必须符合 Conventional Commits**：需要 merge commit 时显式传入合规的
  `-m`，不用默认 `Merge ...` 标题。push 前运行
  `python scripts/check_repo_policy.py --head test --base main --title '<合规 PR 标题>' --check-commits`，
  检查整个 PR 提交范围。追加空提交不能修复历史中的不合规标题，也不作为失败重试手段。
- **提前识别 workflow 升级的 bootstrap**：改动 `ci.yml`、测试部署 workflow 或 evidence
  契约时，先核对 source/controller 的哈希与复用条件，预判自动对齐是否会拒绝旧证据。
  需要人工对齐时，只能在已合并 PR、原始 CI 与测试部署成功、main/test tree 完全相同、
  远端旧 SHA 精确核验后，按既有流程使用 exact `--force-with-lease`；随后取得新控制器下的
  验证证据并记录原因。此流程不授权 force-push main，也不允许降低 evidence 校验标准。
- **先完整自审与专项验证，再推送稳定版本**：入口/状态目录迁移必须联查调用方、维护脚本、
  急停、权限、健康检查与回滚路径，不能只检查新入口默认值；自定义生产参数也要覆盖。
  发现缺陷先修复并做有意义的专项测试，再集中运行全量检查，减少边修边 push 的取消与重跑。
- **按依赖顺序取得证据**：稳定 test SHA 的 CI → 测试部署成功 → 创建发布 PR并复用有效
  证据 → PR CI 全绿与最终自审 → 合并/对齐。只有最终 SHA 或相关输入变化、验证失败或仍有
  未决风险时才追加验证；不要并行触发相同 SHA 的 push CI 与手工 CI。合并后先检查自动对齐
  结果，确需最终 SHA 制品时再统一构建、下载和校验，避免反复下载中间版本。
- **先摘要、后定点日志**：Issue 先取列表与最新相关评论，Actions 先取 job 名称和结论，
  失败再读对应步骤；长 diff 分文件检查，制品 verifier 的完整输出落临时文件，只汇报摘要。
  避免一次输出过长被截断后重复读取。无独立工作可做时按作业耗时等待，不连续密集查询；
  耗时较长仍须及时汇报发现、阻塞和下一步。
- **拆分发布前核对新旧组合**（2026-10-19 复盘）：共享发布会先装上新的基础 unit，它的资源
  限制、工作目录、参数会和尚未升级的组件旧代码组合运行。如果中间隔着一次定时运行，要先确认
  这个"新 unit + 旧组件"的组合可以运行（曾因 `MemoryMax=3G` 套在旧 catalog-verify 上而 OOM），
  否则就把组件发布紧跟在共享发布之后。合并后 Align/Direct push audit 若报"未关联已合并 PR"，
  先用 `commits/<sha>/pulls` 确认关联已经存在，再重跑失败的 job，不要追加提交。
- **手动运行按日运行的任务要注意时间点**：午夜后到当天收盘前，交易日历会包含尚未收盘的当日，
  可能被当作采集目标（#440）。补采优先放在当晚收盘后到午夜前执行。
- **区分验收层级**：CI、测试部署、制品校验、人工页面验收、生产激活分别记录；测试环境
  未运行某组件时，不把整体部署成功表述为该组件已完成现场激活/回滚验收。代码合入与 Issue
  关闭不代表生产迁移完成，未执行项和剩余生产门禁必须如实交接。

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
- **独立组件只前进不回退**（维护者 2026-10-20 裁决，#453）：组件出现异常直接修复并正常发布，
  不把 legacy 回切当作生产回滚手段，也不为此新增 gateway 命令或控制面能力。回切只作一次性
  预生产验证（`Verify component rollback preproduction`）；`component-deploy` 自带的失败补偿保留。
- 组件发布必须等待 workflow 最终成功，并核对 GitHub Deployment 与节点 record，三者一致后
  才启动验收作业；节点切换成功不能替代 workflow 后验成功。生产记录生成测试须调用实际
  `validate_record()`，不能只检查 mock 捕获的字段。
- 状态目录换 owner 后逐层验证 Web/旧账号的遍历、读取和回切写权限；default ACL 不替代
  access ACL。跨文件系统保存回退文件不能直接 `rename`，须先留存副本再恢复控制入口。
- 只读巡检账号使用专属最小配置，不为方便而开放完整生产配置；真实休市日的 skipped
  只证明休市分支，不能冒充交易日全量质检验收。配置变更须同步各组件副本并重新核验。
- 迁移生产者前，用**当前生产版本**的消费者验证新记录可发现、可读且不重复；main 上的
  新读取器通过测试不代表尚未发布的生产 Web/daily-digest 已兼容。
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
  （优先写进相关 Issue；长篇方案放计划文档并在 Issue 里链接），接手方先读这些再动手。
