# service-checks 生产迁移（#418）

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
