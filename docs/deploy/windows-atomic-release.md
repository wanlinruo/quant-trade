# Windows 原子发布与回滚

本流程服务于 #155。GitHub 只连接 Linux production forced-command 入口；Windows SSH
私钥只保存在 Linux 节点，绝不进入 GitHub Secrets、Actions 日志或仓库。

## 发布单元

Windows 上的两个应用是独立源码快照，必须分别读取和验证各自的 `DEPLOYED_SHA`：

| app | 根目录 | 计划任务 | 发布后心跳 |
|---|---|---|---|
| `live_app` | `C:\qsys\live_app` | `qsys-agent-shadow` | `agent` |
| `paper_app` | `C:\qsys\paper_app` | `qsys-market-gateway` | `market` |

即使两者偶然处于同一 SHA，也不得把一方的基线推断成另一方的基线。

## 不变量

- Linux 只从完整、已验证的 Git SHA 读取 `src/`、`scripts/`，生成 ZIP 和逐文件
  SHA256；hash 比较统一将 CRLF/CR 归一为 LF。
- `prepare` 先用当前 `DEPLOYED_SHA` 对整棵 `src/`、`scripts/` 做漂移检查，再解压目标、
  校验 archive 路径与 hash，并用目标代码完成 import 和现有配置加载。任何失败均不碰任务。
- 相同 SHA 且文件无漂移时为 no-op，不停任务、不禁用 watchdog。
- `deploy` 先禁用 `qsys-watchdog`，再只停止受影响任务；确认 Python 进程退出后，以目录
  rename 切换完整 `src/`、`scripts/`，禁止逐文件覆盖。
- 两个 app 同时发布时必须全部 prepare 后才开始 switch。任一任务、文件、marker 或新心跳
  验证失败，按逆序恢复所有已切 app。
- watchdog 的重新启用在 `finally` 中执行；成功和失败都不得留下 disabled 状态。
- 发布健康要求心跳时间晚于本次 switch，不能用旧的、仍在 freshness 窗口内的心跳冒充新
  进程已就绪。
- 自动代码回滚不执行数据或 schema 反向操作，不修改密码、RDP、Autologon 或 QMT 设置。

## 固定入口

Windows 固定入口安装在应用目录之外：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  C:\path\to\install-windows-deployer.ps1 `
  -EntrypointSource C:\path\to\qsys-windows-deploy.py `
  -ConfigSource C:\path\to\windows-deployment.json
```

安装后人工核对 `C:\ProgramData\qsys\windows-deployment.json`。配置只含路径、任务名与
心跳 source，不含密码或私钥。Linux 协调器安装为：

```bash
scripts/install_windows_deployment_coordinator.sh
```

安装动作和真实发布都是生产写操作；执行前必须保存当前状态并核对精确文件。

## 应急人工回滚

自动入口不可用时，仅按某次 `operation.json` 里记录的 app、backup 和 SHA 操作：

1. 禁用 `qsys-watchdog`；
2. 结束受影响计划任务并确认对应 Python 进程退出；
3. 将失败版本的完整 `src`、`scripts` 移出应用目录；
4. 从该次 backup 完整恢复 `src`、`scripts`、`DEPLOYED_SHA` 和旧 manifest；
5. 启动受影响任务，确认进程与晚于启动时刻的新心跳；
6. 在 `finally` 等价步骤中重新启用 watchdog，并核对其计划任务状态与结果码。

不得从“最新 backup”猜测回滚点；只能使用该次记录明确关联的目录和 SHA。无法确认本地
文件基线时停止，不得覆盖现场。
