# #330 后续组件生产迁移批次

维护者 2026-10-18 明确要求“继续推进330及其相关组件部署到生产环境，尽快处理”。
本批按依赖顺序推进 Web/worker 与只读观测组件，随后处理数据写入任务；不包含真实故障注入、
Windows 重启演练、删除数据/回滚副本或扩大实盘资金。Live Core 最后处理。

## 首批必要修复

- Web 对 LiveStore 使用 mode=ro/query_only，不在启动时创建或迁移业务状态库；服务健康读取器
  不初始化数据库。读 WAL 的真实测试证明读取最新提交、拒绝写入，缺库时不伪造空库。
- Web、daily-digest、health-observer 使用 `/etc/qsys/components/<component>/deploy.toml`
  最小配置。Web 仅 Redis/streams/safety/Tushare/策略承袭；日报增加发信配置；observer 增加运维通知。
  不复制账户白名单、Windows 管理凭据或完整生产配置。必要的 Redis 访问沿用现有 RESP2 链路，
  不声称已具备 Redis 服务端 ACL 隔离；代码只读观测，订单消费者不迁移。
- Web 的查询预算从组件专有环境变量读取（缺字段失败关闭），日报告在有界上下文查询。
  内存/线程/temp/spill 均显式限定；组件 cgroup 另限制 CPU/内存。
- Windows 为 observer 新增固定查询 qsys-watchdog 的 forced-command key，禁止转发/PTY；
  保留原 keys、密码、RDP、Autologon 和 QMT 配置。Web 使用采样数据库，不开放 Windows 管理 key。

## 现场与执行

生产共享 checkout 基准 `447bdeefb5f50ebd8eae43974296ba0cbbeb169a`；不进行共享 checkout 升级。
按 exact main SHA 的成功 CI artifact，逐组件 stage/校验/预演/dry-run/正式 workflow；
workflow、GitHub Deployment、节点 validate_record 三方成功后才做官方验收。
生产审计根 `/data/qsys/var-deployments/issue330-batch-20261018/`。

1. 保存 unit/drop-in、有效命令、owner/ACL、配置 fingerprint、SQLite 一致备份和关键 peer 身份。
2. 共享 Workbench 与 runs 为 Web/worker 专属组可写，显式保留 ubuntu 的 access/default ACL，
   使旧 Paper 与 Live 的策略读取不中断。只读输入按注册数据集和枚举记录/研究路径授权；
   不开放备份内容。不覆盖金融数据或历史记录。
3. Web 与 worker 联合准备：旧 Web 有内嵌队列，新 Web 不再内嵌队列；新 worker 必须等旧 Web
   退出后才启动，防止双消费者。若旧队列仍有活动作业则先等待，不中断作业。
4. 先以真实 immutable venv、专用账号、8422 本机临时端口和隔离 Workbench 验证 Web；
   不在试点连接真实 Redis 或写业务库。健康/Live/历史/数据页与只读拒绝分别验证。
5. 正式 Web 发布只切 qsys-monitor，保持 Live/Paper/observer 身份；确认页面后部署/启用独立
   worker。首次无旧 current 时，legacy drop-in 为回退依据，保留其副本，不伪称 previous-release。
6. daily-digest 在隔离输出与 --print 下对交易日/休市日验证，不发送额外邮件；正式切换恢复原 timer。
   observer 保留健康历史与当前消费者可见性，试跑禁用通知，正式切换只改变目标进程身份。
7. 每个组件失败先依据正式 record 恢复，保留新记录和制品；逐层核验 owner/ACL，不以退出 0
   代替消费者可读和交易日数据质量。最终分别记录代码、激活、运行、legacy 回切与故障注入状态。

## 未完成的验收不能提前计入

service-checks 已恢复 timer，10-19 20:00 首次交易日独立定时运行仍待；storage-report 与
service-checks 的 previous-release/故障注入未验收。其余组件只有在真实现场证据完成后才更新矩阵。
本批结果继续写入 #330、子 Issue 与本计划；生产实盘参数和风险上限保持原值。
