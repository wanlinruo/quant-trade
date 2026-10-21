# limit-up-shadow 独立生产迁移（#463 / #330）

承接 [#463](https://github.com/wanlinruo/quant-trade/issues/463)。维护者 2026-10-21 指示：
优先继续 #463，尽快推进 #330；交易时段做开发与验证，非交易维护窗口发布生产。
本次生产准备、控制面 bootstrap、历史复制与组件/Web 联合发布均安排在窗口内，
不做生产故障注入，不改资金、交易开关或共享 checkout，不重启 Live Core。

## 现场基线（2026-10-21 13:09，只读）

- 共享 checkout `98fff8b`，干净；Live Core PID1748883，启动10-19 18:21:47。
- `qsys-limit-up-shadow` 账号、独立 current、最小配置和专属运行记录目录均不存在。
- legacy service 为 ubuntu/ubuntu、inactive/dead，timer active，下一次19:35。
- 旧库 `/data/qsys/var-prod/limit-up-shadow.sqlite3` 为77,824字节、DELETE journal；
  真正表名为 `limit_up_shadow_runs/entries/outcomes`，不按“空新库”迁移。
- 10-20 自然作业19:35:55–19:36:16成功，journal实测内存峰值2.8GiB、CPU34.996秒。
  因此不套用1–2GiB运维组件上限。候选MemoryHigh3G/MemoryMax4G、CPU50%、单线程；
  评分连接实际使用2048MiB DuckDB上限、私有临时目录与512MiB spill上限。

## 改动与边界

1. #479后组件主组为qsys-limit-up-shadow，仅补充qsys-observers；Web/日报通过
   observers只读ACL读取小型状态库。私有组只给该服务读取最小配置。
   `/etc/qsys/components/limit-up-shadow/deploy.toml` root:私有组0640，仅schema_version与
   Tushare日历token。不得复制Redis、账号、订单、邮件或Windows凭据。
2. 首次seed来源固定为旧库绝对路径，仅此组件允许这个固定绝对来源；其他组件seed仍须仓库相对路径。
   gateway从可信规格显式传入来源，不依赖临时worktree的现场状态。
3. installer用SQLite backup一致性复制，校验完整性、schema和所有历史行的确定性摘要，
   保留null及源值；新库DELETE journal、owner为组件账号、group为qsys-limit-up-shadow、0640。
   使用不覆盖的原子发布；存在非等价目标、软链接或异常库立即失败关闭。
   首次迁移已有目标须与旧库等价；普通升级保留新库，绝不再次从冻结旧库覆盖。
   升级时新库缺失必须报错，不允许从冻结旧库重建而丢掉迁移后的历史。
4. Web规格显式读取新路径 `/data/qsys/var-prod/limit-up-shadow/limit-up-shadow.sqlite3`。
   现有reader不改，仍mode=ro/query_only。当前生产reader在切换前必须验证新历史可读且一致。
5. 新状态目录与新记录子树保留ubuntu与qsys-observers的access/default读取ACL；
   ubuntu须显式获得目录遍历和新文件读取权限，保证安装器能够
   校验root安装的0640状态库；不得因此开放私有配置。验证祖先遍历、
   新日期目录读取，以及当前Web/日报实际账号的新旧记录发现和identity无重复。
   #470现场确认GNU `install`会移除文件继承的named ACL：安装器必须在新文件原子发布前
   显式设置ubuntu/observers只读access ACL；已有目标重试也先幂等补齐ACL再回读，不能只依赖目录default ACL。

## 交易时段：开发与证据

- 从最新test开fix分支；专项覆盖WAL已提交历史、原库不变、目标不覆盖、首次不一致拒绝、
  普通升级保留新历史、已有Web reader兼容、运行中拒绝切换、实际DuckDB预算。
- 完整自审后统一运行pytest、secret scan、可信main与候选planner分类，再squash到test。
- 原始CI成功→测试部署成功→发布PR→PR CI与最终逐文件代行自审。
  测试环境不会运行生产榜单；整体部署不能替代真实账号、配置与研究作业验收。
- 交易时段不建立生产账号/配置、不复制状态、不bootstrap、不激活组件或重启Web。

## 维护窗口：按依赖串行发布

本仓库窗口为15:40后，还必须避开实际归档/同步/全量质检等高水位作业，
不以15:00收盘为立即发布许可；不设置普通需求的紧急时段例外。

原#463方案中“bootstrap前不得合入规格”与“只能从已合并main安装可信入口”无法同时成立。
本次采用窗口内的明确串行顺序：完成test证据后再合并→对齐→取得精确main CI→立即bootstrap→
两个组件分别发布；此间禁止并发发布Web/limit-up-shadow，其他组件规格保持不变。
规格不符仍失败关闭，不降低白名单校验，也不从未合并分支安装控制面。

1. 保存peer身份、unit/timer、旧库/配置hash和读者证据到
   `/data/qsys/var-deployments/issue463-<日期>/`；核对精确候选、资源和现行作业/锁。
2. 在首次迁移前暂停榜单timer，记录原enabled/active，确认service不运行。
   新建无登录账号和私有组；写最小配置，建立新目录及最小ACL。旧库、旧配置不改owner或内容。
3. 从已合并、原始CI/测试部署/PR CI及精确main CI成功的SHA建立只读源码worktree，
   用既有安装脚本备份后安装入口v30与root-owned白名单。规格delta仅Web和limit-up-shadow。
   核对入口/dispatcher/config/白名单hash及status能力，保留旧控制面文件但不用于生产legacy回切。
4. 在空闲资源窗口，以候选真实制品/实际账号、只读仓库和独立输出验证上一已收盘日的评分，
   比较已有榜单的证券、顺序、概率和真值身份。不能把immutable snapshot skipped当作完整评分。
   此验证不写生产影子库/warehouse，不占用17:00同步或19:35自然任务窗口。
5. limit-up-shadow：同SHA/release_id dry-run→正式workflow；installer一致性seed、切current/drop-in。
   等workflow最终成功、GitHub Deployment success、节点实际validate_record成功，才验收。
   生产记录须说明首次SQLite seed，不能统一写“无状态迁移”。
6. 用当前生产Web的reader/实际账号验证新库，再独立发布Web指向新路径。
   对Web同样完成dry-run及三方成功核对；核验页面历史和新旧运行记录、read-only权限。
7. 恢复榜单timer原状态，记录下一次19:35；Live Core、Paper、Shadow、worker身份不变，
   Web只有它自己的正式发布允许改变身份。旧库冻结保留，不删除新历史或回退数据。

如果不能在自然任务前完整完成联合发布及读者验收，停止推进生产切换，保留已取得证据，
依当前有效发布状态记录timer与下一窗口；不以赶进度为由跳过门禁。

## 自然运行与后续批次

19:35后核对真实执行身份/制品、退出状态、研究/质量结果、新库历史键连续、当日新增事实、
旧库hash不变、记录可读且无重复。发布完成与自然业务验收分开记录，前者不自动关闭后者。
按“只前进不回退”裁决，不安排生产legacy回切/故障注入；组件自带失败补偿保持。

#330后续按依赖推进：catalog-verify专属账号/最小配置补齐→tushare-sync→daily-archive→
premarket-limits→paper/shadow-core→reboot-drill只读控制面迁移。写warehouse的组件先完成
精确写目录、锁与读者兼容方案；premarket-limits关系当日买单fail-closed，排在数据任务之后。
Live Core最后单独审阅，Research Runner/月度研究/platform/Windows兼容分别验收。
14组件激活数、账号/配置隔离、真实业务验收和其余运行单元分别计数，不以14/14代替#330整体完成。

## #479 Unix身份收紧

qsys-workbench同时授予Workbench目录/DB写权限，不能用于榜单账号。主组及输出组改为
qsys-limit-up-shadow，仅补充qsys-observers；新身份真实探针已确认模型/参考/最小配置可读、
自身输出可写，而Workbench、完整配置及业务表不可写。正式变更账号及unit后需复验，
系统沙箱与原始Unix身份分别记录；现有评分/旧数据内容不变，复用有效业务证据。
