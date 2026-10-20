# reboot-drill 定时模式契约（#444）

2026-10-20 10:00，生产共享 `qsys-reboot-drill.service` 缺少 CLI 必需的显式模式，
在参数解析阶段退出 2。实际 timer 每天 10:00 触发（Persistent=true），脚本再按直取交易日历
判断月度执行日。缺参失败不表示已经执行 Windows 重启，也不能用 reset-failed 当作修复。

共享 unit 与独立组件规格统一显式 `--dry-run`。CLI 继续拒绝无模式、冲突模式或不完整的
真实执行确认；不得将参数默认值改为 execute。此修复不迁移 reboot-drill 独立组件。

- 真实交易日：按交易日历拒绝重启，退出 0，保留 journal 日志；不会探测 Windows 或伪造演练记录。
- 非月度执行休市日：按日历跳过，退出 0，同样仅有实际退出日志。
- 月度执行日：采集真实 Windows/Redis 基线，生成 skipped ServiceRun，明确未执行重启，
  不运行 shutdown、恢复等待或演练成功通知。
- 真正执行仍需维护者针对当次日期/目标主机的明确授权、显式 --execute 和对应确认参数；
  --force 不能绕过交易日禁令。日常定时入口不携带任何执行确认参数。

回归测试提取共享 unit 和独立规格的实际 ExecStart 参数并调用同一个 CLI，覆盖以上三个分支。
网络、危险命令、等待与通知均设为未预期调用即失败；基线分支仅用明示测试夹具。

发布使用正常 test/CI/测试部署/PR/生产 dry-run/正式流程，并精确核对从当前生产 SHA 到目标
SHA 的实际组件范围。workflow、GitHub Deployment、节点实际 validate_record 三方成功后，
核验实际安装 ExecStart 与源文件一致，再运行真实 unit 的只读分支；保留失败 journal、原 unit
备份和回滚记录。交易日 skipped 仅证明交易日保护分支，不冒充月度基线或真实重启恢复验收。
生产版本、发布与现场结果见 #444 最新记录。
