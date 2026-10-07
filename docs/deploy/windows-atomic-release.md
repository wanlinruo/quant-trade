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

固定入口是首次引导和应急回退点。正常 GitHub 发布时，Linux 协调器还会从已经通过来源、
PR 和 CI 门禁的目标 SHA 提取该版本的 `qsys-windows-deploy.py`，复制到 Windows 的
`incoming` 目录，并让 status / prepare / deploy / record 全部调用这一精确版本。这样控制器
修复无需先手工覆盖固定入口，也不会出现“新归档由旧控制器验证”的自举环；控制器仍位于
应用目录之外，应用切换和回滚不会替换本次操作所使用的控制器。

这里有一个必须显式处理的首次自举边界：如果 Linux 上已安装的
`qsys-production-deploy-entrypoint` 早于“从目标 SHA 提取协调器”的实现，那么仓库里的新代码
不会自动生效。旧固定入口会继续调用旧协调器，旧协调器又会调用 Windows 固定旧入口，形成
自举悖论。首次启用或修复自举逻辑时，必须先逐文件审查目标入口、备份已安装文件、仅更新该
固定入口并核对 SHA256；不得把一次性引导误认为普通目标 SHA 已经能够自行完成。

安装动作和真实发布都是生产写操作；执行前必须保存当前状态并核对精确文件。

## 2026-10-07 编码故障复盘与强制排障顺序

### 现象与真正根因

Windows 现场直接发布已经成功，目标控制器记录的两个应用、进程、心跳和 watchdog 均正常；
但 GitHub dry-run 连续在 Linux 协调阶段报
`'utf-8' codec can't decode byte 0xd5 ...`。两者看似矛盾，实际走的不是同一版本链路：

- Windows 固定旧入口把包含“正在运行”的 JSON 按 CP936 输出，中文起始字节为
  `d5 fd d4 da ...`；
- 第一次现场发布使用的目标 SHA 控制器按 UTF-8 输出，同一文字起始字节为
  `e6 ad a3 e5 9c a8 ...`；
- Linux 已安装的 production 固定入口仍停留在旧版本，尚不具备从目标 SHA 自举协调器的能力；
- 因而 GitHub 实际调用的是旧协调器和 Windows 固定旧入口，而不是已经修复的目标控制器。

更新 Linux 固定入口后，同一目标 SHA 的 GitHub dry-run 立即成功。由此确认：主要故障是
**执行版本漂移和自举断点**，编码异常只是旧链路暴露出的表象。

随后真实 workflow 还有一个独立的响应契约问题：节点操作和 record 都已成功，但入口成功响应
缺少 `"dry_run": false`，被 workflow 的 fail-closed 校验拒绝。该问题与字符编码无关，必须
通过比较节点 record、入口响应和 Actions 失败步骤分别判断，不能把所有失败归为同一根因。

### 本次排查为什么耗时

最初根据异常文本直接假设目标 Windows 控制器解码错误，在没有先证明 GitHub 实际执行文件
版本的情况下修改了编码层。该修改通过单元测试，但无法修复旧入口仍被执行的事实，导致又跑了
一轮完整 CI、PR 和 dry-run。直到比较服务器固定入口 SHA256、检查 Windows `incoming` 文件，
并捕获 SSH 原始 stdout 字节后，才确认执行路径与预期不一致。

### 以后必须按此顺序排查

1. **先画实际调用链**：GitHub workflow → Linux forced command → Linux production 固定入口
   → 目标协调器或固定协调器 → 目标 Windows 控制器或固定控制器。不得只看仓库 HEAD 推断
   服务器执行版本。
2. **先核对版本再改代码**：记录已安装入口 SHA256，与目标 commit 中对应 blob 的 SHA256
   比较；同时确认目标控制器是否真的出现在 Windows `incoming` 目录。
3. **保留原始字节证据**：SSH stdout/stderr 先按 bytes 落到受控临时文件，只报告长度、是否可
   按 UTF-8 解码及必要的短十六进制片段。不要让本地 `text=True` 在证据采集前替你选择编码。
4. **对比固定入口和目标入口**：用相同只读 `status` 参数分别调用，确认问题属于旧 bootstrap、
   transport，还是目标控制器内部。直接现场成功不能自动证明 GitHub 链路也使用同一入口。
5. **先做最短现场验证**：目标协调器 direct dry-run 成功后，再决定是否需要一次性更新固定
   bootstrap；更新前保留带旧 SHA 的备份，只替换已审查的单文件，不覆盖配置或 SSH wrapper。
6. **再走标准门禁**：依次验证目标 SHA 的 CI、test deployment、GitHub dry-run；真实发布后
   同时核对 workflow、GitHub Deployment、节点 record 和现场 status。
7. **分开判断三类结果**：Windows 操作结果、Linux 节点 record、Actions 编排结果各自可能
   成功或失败。必须定位第一个不一致的契约字段，禁止仅凭最终红灯推断节点已回滚或服务异常。

### 禁止的捷径

- 不得看到 UnicodeDecodeError 就在多层同时增加 fallback 或 `errors="replace"`；这会掩盖到底
  执行了哪个版本，还可能破坏“已禁用”等安全状态的精确判断。
- 不得在尚未确认目标控制器被调用前反复触发完整 Actions；先做哈希、原始字节和 direct
  `status`/dry-run，通常几十秒即可区分版本漂移与真实编码缺陷。
- 不得为了修复 bootstrap 整包重装并覆盖生产配置；固定入口、配置、forced-command wrapper
  和 Windows key 必须分别处理。

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
