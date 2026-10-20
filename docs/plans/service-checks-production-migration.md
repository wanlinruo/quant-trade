# service-checks 生产迁移（#418）

2026-10-20收尾：生产激活、当前消费者/权限、真实交易日自然运行证据已补齐。
正常legacy/previous回切尚未执行，由#453承接；故障注入由#454承接，逐次明确审批。
#418迁移阶段结案不代表本文所有回切维度完成，历史步骤和证据保留。

实时授权、候选、hash、workflow/Deployment/节点记录与验收状态以 #418 最新记录为准；
本轮维护者已明确确认生产部署，仅涉及 service-checks，不进行故障注入。

## 运行边界

- 使用精确 main 的 CI 组件制品、独立 release/venv/current 和 qsys-service-checks 无登录账号。
- `/etc/qsys/components/service-checks/deploy.toml` 仅保留当前生产配置的 safety.mode、Tushare token、
  有效 ops webhook URL；不复制 Redis、账户、订单、邮件或 Windows 凭据。root:专用组 0640。
  不回显配置值，保留源配置 hash 并核验原文件不变；今后源 mode/token/ops URL 变更时须同步
  此专属配置并重新核验，不能把一次快照当自动同步。
- DuckDB 复用 query_budget：256 MiB、单线程、私有临时目录、spill 上限 512 MiB；
  unit MemoryMax=1G、CPUQuota=50%、Nice=10、IO idle、Polars 单线程。
- 仅写专属 service-runs/service-checks 与 alerts/service-checks；Warehouse、共享运行记录和
  Live/Shadow/Workbench SQLite 均只读。不得为了 SQLite WAL/SHM 读取而开放业务数据库写权限。
- 输出根预置 ubuntu 的 access/default 读取 ACL，逐层验证 Web/daily-digest 的旧/新记录兼容。
  不移动或改写旧共享记录；新日期目录也必须让读者可遍历。

## 旧生产读取器兼容

生产 Web/daily-digest 的共享 checkout 仍为 447bdeef，只读取旧日期布局。组件结束时通过
OnSuccess/OnFailure 触发专属 `qsys-service-run-index.service`，以当前不可变 venv 中的
`python -I -m qsys.ops.service_checks_index` 将新记录快照发布到旧布局。

发布器拒绝符号链接、错误 owner/identity/date 与超大记录；只创建未存在的 service-checks
文件，已有不同内容立即报错，原记录不覆盖。源目录只读，目标目录仅共享运行记录，
没有网络/凭据/仓库写权限；新建日期目录保留 ubuntu 写者。新读取器按 identity 去重。
该伴随 unit 和组件同次审计安装，回切时保留全部新旧记录；不能以文件可读替代实际旧读取器验收。

## 执行顺序

1. 只读复核 unit/timer、生产 SHA/健康、节点入口与 root 白名单、输入可访问范围、关键 peer 身份；
   在 `/data/qsys/var-deployments/` 保存 before、配置/旧记录 hash、ACL 与控制文件副本。
2. 从 GitHub 取得精确 CI artifact 与匹配源码，校验 digest、manifest、成员、wheel、spec、CI provenance；
   隔离 systemd/venv 下试跑，使用独立输出与告警禁用的最小配置。真实日历必须来自 Tushare；
   不用星期几推断，不伪造缺失数据或把休市 skipped 当全量质检成功。
3. 如新增账号/ACL，仅开放枚举清单中的必要权限；受保护备份内容保持不可读。专用配置和新状态目录
   不影响原共享配置/旧 JSON。root 白名单更新前证明只有 service-checks 规格改变。
4. 暂停 timer、确认 service inactive，先正式 workflow dry_run=true，再 dry_run=false。
   等待 workflow/Deployment/node record 均成功，才触发官方验收作业。
5. 验证 current/有效命令/身份/沙箱、新增报告且消费者可读、旧数据/记录 hash 不变、peer 身份不变。
   正常 legacy 回切须保留新 release 和新报告，用复制校验保存跨盘副本；随后再通过正式 workflow
   重新激活候选并复验。结束恢复 timer 原 enabled/active，oneshot 保持正常结束的 inactive。
6. 用节点侧调度保护维护窗口，不靠 Mac 等待；退出或故障时明确记录 timer 和回退状态。

## 证据边界

休市分支、历史交易日只读对账、正式交易日调度分别记录。数据质检失败与部署/权限失败分别归因；
不放宽规则、掩盖历史错误或以返回 0 替代落盘检查。若本轮只能完成休市分支，生产已激活与
下一交易日全量验收待完成必须同时报告。首个 release 的 legacy 回切不冒充 previous-successful
版本回滚；故障注入需另行明确授权。

## 2026-10-18 晚间异常排查与调度恢复

- 20:37 现场发现 service-checks timer 仍为 enabled/inactive，18:59:43 暂停后未恢复。
  19:30 保护脚本只在 User=ubuntu 且 ExecStart 为 legacy 时恢复；组件已切换，
  因此记录 safe_legacy=false、timer_restored=false。这是迁移未收尾，不能标作正常休市。
- 正式 workflow `39121571064`、GitHub Deployment `7153601392` 和节点
  `github/checks-prod-20261018-first-service-checks/record.json` 均为 success，绑定
  `ac229960712f7f3aff0755ae6a462a46ec980551`；current、effective ExecStart、专用账号一致。
  官方作业 19:05 成功退出，但只执行休市 skipped；交易日完整定时验收仍待 10-19。
- 维护者要求继续处理异常后，核对上述三方证据、原 timer enabled 状态、组件已正常退出，
  恢复原 timer 调度。恢复结果、下一触发时间与 Live/Paper/Web/隧道身份前后对照保存到
  同目录 `timer-recovery-20261018.json`；未发布其他代码或重启交易组件。
- 4 个 failed 均为本轮 `/run/systemd/transient/` 准备/暂存/基线探针的一次性失败，
  只确认这些历史失败状态，保留原始 journal、制品和审计记录；不删除回退副本。
- 最近交易日 10-16 的指标验证首轮 7 阻断，20:38、21:06 两轮结果均为 0 阻断、28 警告。
  服务看板仍引用 20:00 的旧 service-checks 结果；不能改写历史来消除旧告警。
  存储保留策略还有到期副本和未登记目录，未经逐项核验/删除确认不清理。
- 同次发现两个独立的 Web 展示缺陷：coverage 只扫年度布局，漏统计日分区；
  收益对比将 5 万到 10 万的资金基准变化算成收益。修复分别按日分区优先规则统计、
  使用 ts/dt 实际时间列，以及缺少资金流水时收益率返回 null 并明确提示；不推断资金流，
  不改生产金融数据。模拟盘原有收益计算保持不变。

后续：完成修复分支的 CI/测试部署与发布 PR；生产 Web 发布需要针对候选版本的明确确认。
service-checks legacy 回切、previous-release 回滚与故障注入不因 timer 恢复而视作完成。

页面修复的[本地夹具截图](../../artifacts/monitoring-anomalies-20261018/overview-fixture.png)
使用合成的 5 万到 10 万资产记录，仅说明展示行为，不作为生产验收证据。
