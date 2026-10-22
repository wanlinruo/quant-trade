# reboot-drill 独立只读部署（#495 / #330）

生产单元保持显式 `--dry-run`，真实交易日/非月末休市日门禁保持；实际重启仍需每次维护者
明确授权，不属于本次部署或验收。旧单元以Ubuntu/共享venv/完整配置运行；此前manifest所列
`/etc/qsys/reboot-drill/windows-reboot`和known_hosts尚不存在，不能据此声称限权已完成。

## Windows只读边界

`src/qsys/ops/windows_reboot_baseline.py`为仅stdlib的固定入口，从已合并main精确SHA安装。
独立key只能调用 `qsys-reboot-baseline-v1`，拒绝空命令、shell、SFTP、额外参数及换行；
所有读取均为固定PowerShell，不接受用户脚本。响应protocol=1，严格校验真实开机时长、
活动会话、开机以来RDP次数及QMT外连端点。没有活动会话/连接允许null；读失败、未知字段、
非法计数/NaN、超大响应显式拒绝，不以0/空串伪装成功。

Linux显式 `--win-transport readonly-baseline` 时只允许dry-run和明确known_hosts；
遗留人工execute入口原审批门禁保持，但新key无法调用任何重启命令。私钥专属于新运行账号，
不复制维护者个人全权key；Windows授权行仅追加from/restrict/forced-command，保留原字节、
原ACL及真实备份，不改SSHD服务/密码登录、RDP、Autologon、QMT恢复及任务配置。

开发期直接只读探针已在真实Windows执行固定PROBE，约4.9秒返回完整真实schema且存在QMT连接。
这不是限权key验收，也不是重启验收；最终SHA安装后仍须实际专属key验证正常/拒绝路径。

## Linux与消费者

私有主组+observers，最小配置在 `/etc/qsys/components/reboot-drill/deploy.toml`，完整配置隐藏；
只写自身 `var/service-runs/reboot-drill` 与 `var/alerts/reboot-drill`，0770与Ubuntu维护ACL有效。
旧flat运行/告警历史保持，不复制为重复记录。Web/日报已支持这两个namespace，发布前用其实际
账号/当前venv读取新记录与新旧记录去重，不能仅凭main reader测试称生产已兼容。

MemoryHigh256MiB/Max512MiB/Swap256MiB、CPUWeight50；所有测试通知关闭。真实休市日dry-run
分支采Windows与Redis真实基线且明确不重启；模拟日只决定日期门禁，不改真实基线时间/值。
交易日自然skipped只能证明日期门禁，不能冒充完整基线或实际重启恢复验收。

稳定test CI→测试部署→代行逐文件自审PR→合并对齐/最终CI→最终制品/私有身份/限权入口验证→
dry-run/正式workflow、GitHub Deployment及实际validate_record三方success→真实基线验收。
部署前后Windows启动时间/关键进程/任务XML、Linux关键peer、共享checkout/配置与旧历史保持。
旧副本/授权备份保留；只前进修复，不做生产legacy回切、故障注入或真正Windows重启。
Redis5无ACL的残余权限仍由#488承接，14/14单元部署也不代表#330所有门禁已完成。
