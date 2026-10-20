# 实盘停机状态展示（#433）

## 只读真值

Web 不复用交易 KillSwitch 的错误转换逻辑，不调用 engage/release，也不写 Redis、文件或订单。
只有明确的 live 配置才启用实盘读取；shadow/disabled 或配置不可读时显示未知。
Linux 路径由 `--live-kill-path` 或显式环境变量 `QSYS_LIVE_KILL_PATH` 传入，缺省未知，不能从状态库位置猜路径或读取 SHADOW_KILL。
生产已核对 Live Core cwd `/home/ubuntu/qsys`、默认 KILL 为 `/home/ubuntu/qsys/var/KILL`；
实际 qsys-web 可遍历/读取父目录，不能写入。独立 Web 使用现有最小配置，无需 Windows SSH
管理密钥、账户白名单或完整生产配置。

- Linux：正规文件存在即已停机（即使空文件或原因不可读）；类型异常、权限不足、父目录丢失
  为未知。缺文件只有在父目录真实可访问时才判解除。
- Redis：只读 `control:kill`，存在（包括空字符串）即已停机；读取失败未知。
- Windows：只读既有 heartbeat 前缀下的 agent，要求 source=agent、trading_mode=live、
  local_kill_engaged 已上报。最大有效年龄 10 秒，未来超过 1 秒也为未知；不用心跳存活代替停机字段。

任一当前可信来源停机，整体已停机；三处均有效解除才显示解除；其他情形未知。
原因与记录时间只解析停机标记原值，未上报保持未知。Windows 的记录时间来自该次心跳，
不是推测的停机触发时间。状态更新时间是本次实际读取时间。Web 读取错误不展示异常里的秘密。
解除只说明停机闸状态，不能被解释为其他交易条件满足、必然成交或允许扩大资金。

## 页面与失效

公共导航始终提供简洁实盘状态，两级导航紧邻。实盘内容区用紧凑摘要展示状态，正常时使用白底；
三处来源明细默认折叠，可手动展开，自动刷新保持展开状态。红色禁止自动开仓，黄色未知；
既有进程心跳、减仓/风控和质量显示保持原语义。实盘页直接更新摘要，其他页显示紧凑变化提醒，不重复堆叠大卡。变化提醒仅在站内，不发额外邮件/消息。
`/api/live/kill-status` 无写入口，响应 no-store；浏览器约 3 秒轮询、不整页导航。

有效期不超过 10 秒，并受 agent 心跳剩余寿命、实际读取耗时与 HTTP 请求耗时约束。
浏览器用单调时钟并辅以本地经过时间（覆盖休眠）检查，到期、非成功/重定向、无效数据、请求失败立即撤销旧绿。
后台标签恢复及浏览器历史页面恢复也先撤销旧状态，再取新值。初次装载不制造“状态变化”提醒。

## 测试与发布

专项覆盖任一停机优先、缺失/过期/未来/非实盘心跳、权限/缺父目录、空或不可读标记、
自定义路径/前缀、慢请求、配置模式隔离、API no-store、原始原因转义及发布规格的显式路径。
真实 Chrome 覆盖解除/停机切换、网络失败、过期、恢复与非实盘页导航；以下截图均为本地合成夹具：

- [桌面解除](../deploy/screenshots/live-kill-clear-desktop.png)
- [桌面停机](../deploy/screenshots/live-kill-stopped-desktop.png)
- [移动未知](../deploy/screenshots/live-kill-unknown-mobile.png)

独立 Web spec 只追加 `Environment=QSYS_LIVE_KILL_PATH=...`，保留原 ExecStart；旧 Web
忽略该变量，因此自动回切旧 CLI 时不会因不认识新参数而启动失败。其余组件规格不得变化。可信 main planner、目标
spec 与节点 root whitelist 都须核对；合并后仅从精确可信 GitHub SHA 对齐 whitelist，保留旧
控制器/配置和哈希，不降低 artifact/spec 校验。正常 CI/测试部署/代行自审/独立 Web 发布；
workflow、Deployment、节点实际 validate_record 三方成功后，按实际账号/制品确认路径、
三处真值、页面时延、peer 身份与交易配置。异常分支使用合成夹具，不为截图在生产落闸。
#444 的共享 unit 发布门禁仍单独跟踪；本项代码合入不代表它已正式发布或验收。
