# Windows 执行节点部署记录

- **主机**：`118.25.42.149`（腾讯云）
- **登录**：`ssh -i ~/.ssh/qmt_bridge_ed25519 Administrator@118.25.42.149`（密钥已装，无需密码）
- **首次接入本项目**：2026-09-09

## 机器实况

```
Windows Server 2016 Datacenter 10.0.14393
AMD EPYC 7K83 · 1 物理核 / 2 逻辑核
内存 3.9 GB（可用约 1.0 GB）
C: 59.5 GB，剩 23.8 GB
Python 3.11.9 @ C:\Program Files\Python311
```

**这台机器不是空的**：

| 进程 | 说明 |
|---|---|
| `XtMiniQmt`（700 MB） | miniQMT 客户端，实盘通道 |
| `python -m bridge.server.wsgi` | 你的 qmt-bridge |
| `python -m bridge.server.trader` | 你的 qmt-bridge |

**三个 bridge 目录一律不碰**：`C:\qmt-bridge`、`C:\qmt-bridge-private`、
`C:\qmt-bridge-simulation`。它们跑着自己的影子模式作交叉验证。

**内存是这台机器的瓶颈**（可用仅 1.0 GB）。任何在此运行的任务都要让路：
采集脚本默认带 `--sleep`。

## 无人值守重启（2026-10-04 验证）

重启后整条链自己回来，无需任何人介入。**从开机到影子盘代理心跳恢复约 5.5 分钟。**

```
10:38:40  开机
10:38:44  qsys-market-gateway   （SYSTEM，开机触发）
10:39:09  qsys-qmt              （登录触发，最高权限）→ XtItClient
10:39:34  XtMiniQmt 起来，自动登录券商
10:44:01  qsys-agent-shadow     （看门狗拉起）
```

验证口径：`qwinsta` 显示会话是 `console`，安全日志里只有 `LogonType=2`
（控制台自动登录）、**没有任何 `LogonType=10`（RDP）**——确实没人碰过。

### 四层，缺一不可

**① Windows 自动登录**。用 Sysinternals Autologon（`C:\qsys\tools\`）设置，
密码存 LSA 密钥，**注册表里不留明文**。核对：`AutoAdminLogon=1` 且
`Winlogon\DefaultPassword` 不存在。

重设用 `C:\qsys\tools\enable-autologon.ps1`——它当场问密码，不进命令行、
不进历史。

**② Windows 不会自己重启**。`NoAutoUpdate=1`，`wuauserv`/`UsoSvc` 都是 Manual
且停着。剩下的重启来源只有人为和宿主机故障。

**③ QMT 勾上「自动登录」**。登录框上「记住密码」旁边那个——**只勾「记住密码」
不够**，那只是帮你填好、仍然等人点「登录」。两个都要勾。

另外「系统设置 → 交易设置 → 自动重启设置」里勾上「启用开机自动启动」「启用自动
初始化」，并加一条 **08:45** 的自动重新初始化时间：自动初始化只在启动时跑一次，
机器连开几天的话，交易前那条连接可能已经是上周建立的。08:45 给开盘前一个干净
的起点，离 09:25 竞价还有 40 分钟余量。

**④ 开机启动必须走 `XtItClient.exe`，不能直接起 `XtMiniQmt.exe`。**

这一条最隐蔽，2026-10-04 查了一整夜：

| | 可执行文件 |
|---|---|
| 桌面图标（人手点的） | `XtItClient.exe` |
| 原来的启动文件夹快捷方式 | `XtMiniQmt.exe linkMini` |

是两个不同的程序。`XtItClient` 是正规启动器，会把 `BrokerProxy` 等依赖拉起来；
直接起 `XtMiniQmt` 就缺组件，日志里刷
`[proxy_login_status] status = 1, message = 找不到指定的类。`，每 3 秒一次，
**一整夜 11,447 次**，而 `Get-NetTCPConnection` 显示它一个外连都没有。

所以现象是：**人手点图标必成功、开机自启必失败**。

还有第二层：把启动文件夹的快捷方式改指 `XtItClient.exe` 仍然起不来——
`UAC EnableLUA=1`，启动文件夹用的是过滤后的受限令牌，而启动器要管理员权限。
最终用计划任务 `qsys-qmt` 解决：登录触发、延时 20 秒、`HighestAvailable`、
`InteractiveToken`。启动文件夹那条已停用（改名 `.disabled-20261004`，原件
`.bak-20261004` 留着可回退）。

### 每月演练一次

这四层**任何一层被人改掉都不会有任何提示**，直到某次真的意外重启才发现——
2026-10-04 查清它花了一整夜。所以排成月度任务 `qsys-reboot-drill`：真重启一次，
让它坏在我们看着的时候。

**执行日：当月最后一个休市日，上午 10:00。** 定时器每天触发，由脚本查交易日历
判断今天是不是那一天——执行日不是固定号数，systemd 的日历表达式写不出来。

为什么是上午：这天多半是周日，**第二天就开市**。上午跑完，万一失败还剩一整天
在下一个交易日之前修好，而且失败会立刻推 CRITICAL 到运维群。

判据六条，缺一不算通过：机器确实重启了、Windows 自动登录（会话必须是
`console`）、**全程无 RDP 登录**（有人进去点一下当然能好，那不是无人值守）、
QMT 连上券商、代理心跳、行情心跳。

闸门：**交易日直接拒绝，这一条压过 `--force`**。`--force` 是给人临时补做用的，
不是用来在交易时段重启生产节点的。

```bash
uv run python scripts/reboot_drill.py --dry-run   # 看今天跑不跑、采个基线
uv run python scripts/reboot_drill.py --force     # 人工补做（交易日仍拒绝）
```

### 排错时先看什么

别从 QMT 日志开始——它那句「找不到指定的类」会把人带去查 COM 和网络，
全是弯路。先看这三样：

```powershell
Get-Process XtMiniQmt | Select-Object Id, SessionId, StartTime
Get-NetTCPConnection -OwningProcess <pid>   # 登录正常时应有 58.34.120.85:56000
schtasks /query /tn qsys-qmt /fo LIST /v
```

**本机能连通券商端点、而 QMT 没有任何外连**，就说明问题在 QMT 内部，不是网络。

还有一条教训：SSH 进来是 session 0，QMT 在 session 1，**枚举不到它的窗口**。
看不见界面时不要靠日志编故事——让维护者截一张图，比猜一小时快。那张登录框
截图（「自动登录」没勾）和 `XtItClient` vs `XtMiniQmt` 这两步，才是真正解决
问题的，前面三个结论全错了。

## qsys 的落脚点

新建 `C:\qsys`，与 bridge 完全隔离：

```powershell
python -m venv --system-site-packages C:\qsys\.venv
C:\qsys\.venv\Scripts\python.exe -m pip install `
    -i https://mirrors.cloud.tencent.com/pypi/simple `
    --trusted-host mirrors.cloud.tencent.com pandas pyarrow
```

`--system-site-packages` 让 venv 看得见系统 Python 里的 `xtquant`，
而 pandas/pyarrow 只装进 venv——**不污染 bridge 的运行环境**（bridge 用的
正是系统 Python）。

## xtdata 分钟线实测（2026-09-09）

### 一、时间戳按「收盘时刻」标注 —— 与仓库约定一致，无需换算

判据是互斥的，一测便知：

| 探测 | 结果 |
|---|---|
| `11:30` 存在 | **是** |
| `13:00` 存在 | **否** |
| `13:01` 存在 | **是** |

若按开盘时刻标注，必然相反（11:30–11:31 无交易、12:59–13:00 无交易）。

### 二、当日 241 根的结构

| bar | 特征 | 是什么 |
|---|---|---|
| `09:30` | OHLC 四价相等 | **开盘集合竞价**，其 open 即日线 open |
| `09:31 ~ 11:30` | 120 根 | 上午连续竞价 |
| `13:01 ~ 15:00` | 120 根 | 下午连续竞价 |
| `14:58`/`14:59` | 量为 0、四价相等 | 收盘竞价时段，无连续成交 |
| `15:00` | 四价相等 | **收盘集合竞价**，其 close 即日线 close |

**竞价量单独成一根，没有折进首根。** 这正是策略判高开/低开要的东西。
`MinuteFeed.SESSION_OPEN` 因此必须是 09:30 而不是 09:31。

### 三、历史深度只有约一年，且是滚动窗口

| 区间 | 分钟线 |
|---|---|
| 2023-01 整月 | **0 根** |
| 2024-01 整月 | **0 根** |
| 2025-01 整月 | **0 根** |
| 2025-09-09 起 | 有 |
| 从 2020 起问最大范围 | 最早 **2025-09-09**，共 **242 个交易日** |

对照：同一账户的**日线**能回溯到 2023-01-03（893 根），所以不是权限问题。

**后果一**：精确版策略只能回测约 1 年（242/894 = 27%），2023-01 至 2025-09
只能用日线近似版。

**后果二（有时效性）**：每过一天就永久丢失一天。要积累超过一年的分钟线，
**必须从现在开始每日全市场增量归档**。2025-09-09 之前的已经找不回来了。
2026-10-06 裁决后，下载清单由候选口径改为当日在市的主板、创业板、科创板与北交所
全部股票；候选与模拟持仓仍作补充集合，保证股票池之外的持仓资产不漏采。

### 四、吞吐

| 方式 | 耗时 | QMT 缓存占用 |
|---|---|---|
| **按股-日下载**（默认） | 1.08 s/只 | **零增长** |
| 按标的整年下载 | 0.69 s/只 | **4.5 MB/只 → 3515 只约 16 GB** |

整年下载单看速度更快，**但会撑爆磁盘**：QMT 把下到的一切留在
`C:\self\...\userdata_mini`，实测 1056 只就占了 4.8 GB，而全盘只剩 15.9 GB。
这台机器跑着实盘通道，磁盘写满会一起出事。

历史回填仍默认按股-日下载。每日全市场归档使用 `--whole-window`，但窗口只覆盖本次
回看范围（通常 1～8 个交易日），每只证券只发一次下载请求；它不是下载整年。这样既能
在漏跑后补齐短窗口，又不会把一年数据一次性灌入 Windows 缓存。续传账本按「标的 + 日期」
记账，同一目录换日期清单不会整只跳过。

**教训**：选下载策略时只比了时间没比磁盘。在这台机器上磁盘才是约束。

## 采集流程

```powershell
# 1) Linux 侧导出清单，传到 Windows
#    uv run python scripts/export_minute_manifest.py \
#        --start 2025-09-09 --end 2026-09-08 --out var/minute_manifest.csv

# 2) Windows 下载并导出 parquet（可中断可续传；Parquet 落盘后才按证券+日期记 done.csv）
C:\qsys\.venv\Scripts\python.exe -u C:\qsys\win_download_minutes.py `
    --manifest C:\qsys\minute_manifest.csv --out C:\qsys\outgoing `
    --window-start 20250909 --window-end 20260908 --sleep 0.2

# 3) 回传到 Linux 并导入
#    import_minute_bars(warehouse, Path("var/minutes-incoming"),
#                       archive_dir=Path("var/minutes-done"))
```

`Start-Process ... -WindowStyle Hidden` **不可靠且危险**：它看起来没启动
（日志为空、进程列表里找不到），实际却在后台活着。本次踩到的后果是两个
下载进程并发写同一个输出目录，其中那个"看不见的"占了 2.4 GB 内存，把机器
可用内存压到 113 MB。

长任务请让 SSH 会话保持前台（`ServerAliveInterval` 保活），脚本本身可续传，
断了重跑只补缺口。**排查时用 `Get-WmiObject Win32_Process` 看完整命令行**，
`Get-Process` 只给进程名，看不出是谁在跑什么。

## 跨源验证结论

QMT 分钟线 vs mootdx 日线，两个完全独立的数据源：

| 项 | QMT 分钟聚合 | mootdx 日线 |
|---|---|---|
| open | 41.01 | 41.01 |
| close | 40.90 | 40.90 |
| volume | 493,102 | 493,101 |

（600036.SH，2026-09-08）

小批试跑 20 只 × 66 个交易日 = 1,320 个股-日，逐日跑
`check_minute_bars`，**0 个交易日有问题**。成交量之和、最高价、首根 open
三项全部对得上。

## Tick 数据（集合竞价）

**为什么要**：策略要把买点从 09:30 开盘前移到竞价时段，用竞价过程中的
虚拟开盘价与匹配量下限价单，规避开盘瞬间的滑点。分钟线做不到——09:25
之前根本没有分钟线，09:30 那根只有一个价格点。

探针：`scripts/win_probe_ticks.py`。

### 一、返回形状

`get_market_data(period="tick")` 给的是 `{code: numpy 结构化数组}`，
**不是 DataFrame**（分钟线是 DataFrame，这里换了形状，照抄会崩）：

```
time lastPrice open high low lastClose amount volume pvolume
stockStatus openInt lastSettlementPrice
askPrice bidPrice askVol bidVol      <- 各 5 档，每笔是 list
settlementPrice transactionNum
```

`time` 是**毫秒 epoch（UTC）**，+8 小时才是北京时间。

### 二、竞价段覆盖：完整

| 标的 | 竞价段 tick | 间隔 | 09:15~09:20 | 09:20~09:25 |
|---|---|---|---|---|
| 600036.SH（沪·大盘） | 202 | 恒定 3s | 100 | 102 |
| 300302.SZ（创业板·小盘） | 45 | 中位 9s / 最大 81s | 17 | 28 |

沪市按 3 秒固定广播；深市只在盘口变化时推，笔数少但**不漏关键变化**。

竞价段的盘口就是撮合状态——买一 = 卖一 = **虚拟开盘价**，量 = **匹配量**：

```
时刻        bid1    ask1   bidVol1  askVol1  lastPrice   volume
09:15:00    0.00    0.00        0        0       0.00        0
09:17:30   41.07   41.07      601      601       0.00        0
09:20:03   41.07   41.07      626      626       0.00        0
09:25:00   41.01   41.01     2195     2195       0.00        0
09:25:03   41.01   41.02       72      200      41.01     2195   <- 撮合完成
```

09:25:00 那笔的价 41.01 就是当日 open，量 2195 手就是开盘成交量。
**09:20 之后委托不可撤**，这一段的虚拟开盘价才是可交易的信号——
600036.SH 有 102 笔，300302.SZ 有 28 笔，够用。

这同时**解决了移植版最后一条口径分歧**：原策略的 `get_first_auction_record`
取 09:15~09:26 的首个快照，我们此前只有 09:30 的开盘价，只能近似。

### 三、历史深度：只有约 20 个交易日 —— 这是硬约束

600036.SH，锚点 2026-09-08：

```
T-25d  2026-08-14   5153 笔
T-29d  2026-08-10   5102 笔
T-30d  2026-08-07      0 笔   <- 边界
T-90d  2026-06-10      0 笔
```

边界干净地落在 2026-08-10，约 **20 个交易日**。服务端就不发更早的，
`download_history_data` 也补不回来。

**后果**：买点前移**无法在过去一年的区间上回测**，只能从今天起每日累积、
向前验证。且这是比分钟线（滚动一年）紧得多的窗口——**漏一天就永久丢一天**，
20 天不采就全没了。

### 四、实测采集（2026-09-10 首次上线）

```
137 只候选 × 1 天  ->  17,224 笔  /  30 秒  /  0 失败
增量再跑 67 只     ->   3,940 笔  /  12 秒  /  0 失败
```

**跨源校验**：竞价段最后一条的虚拟开盘价 vs mootdx 日线 `open`，
2026-09-09 的 55 只**全部一致**。

**这台机器的 venv 没有 polars，只有 pyarrow**——`win_download_minutes.py`
一直如此，新脚本照办。首次上线就栽在这条（`ModuleNotFoundError: polars`）。

### 五、体量

单只单日约 5,000 笔 / 下载 1.1~1.2 s。全市场 × 242 天约 5.3 TB，
**全量不可行**；只采候选股-日的竞价段（每笔仅需时刻/bid1/ask1/量），
量可忽略。

## 待办

- [x] **每日全市场增量归档**——`daily_archive.py` 生成全市场股票日清单，晚间以 Tushare
  实际有成交股票作独立到货质检；分钟线是滚动一年，失败必须尽快补跑
- [x] **每日归档竞价 tick**——已接入 `daily_archive.py`，排在日线增量之前
- [ ] 在**保留维护者密码登录**的前提下完成 #122 SSH 加固；不得关闭密码登录，也不得
      在未同步更新并验证 Sysinternals Autologon 时轮换 Administrator 密码
- [ ] 轮换部署期间在对话中出现过的账号密码
- [ ] 内存仅 1.0 GB 可用，若后续要在此跑更多任务需扩容

## 进程看门狗（2026-09-18）

影子执行代理 `qsys-agent-shadow` 是**「登录时触发」**：进程崩了不会自己起来。
2026-09-18 05:06:47 代理以 0xC0000409 崩溃，无人登录，任务一直停在 Ready，Linux 侧
影子核心因此 5.5 小时取不到券商账户快照，当日 09:30 的竞价事件过期未跑。
（行情网关后来改成了「开机触发」+ SYSTEM，见下节的触发方式一览。）

`deploy/windows/qsys_watchdog.ps1` 每 5 分钟按命令行特征检查这两个进程，不在就
`schtasks /run` 触发对应任务；只启动、不杀进程，也不区分盘中盘后。它写
`C:\qsys\live_app\var\watchdog.log`，只在真的触发时写行。

安装（脚本拷到 `C:\qsys\live_app\` 之后）：

```powershell
schtasks /create /tn qsys-watchdog /tr "powershell -NoProfile -ExecutionPolicy Bypass -File C:\qsys\live_app\qsys_watchdog.ps1" /sc minute /mo 5 /ru SYSTEM /rl HIGHEST /f
```

**脚本必须是 UTF-8 with BOM + CRLF**：Windows PowerShell 5.1 在没有 BOM 时按 GBK
读文件，中文注释会被读乱并导致解析失败（实测 EXIT=1）。

计划任务 XML 不入库：`schtasks /xml` 只接受 UTF-16 编码的文件，仓库里放 UTF-16
既不可读也容易在编辑时损坏，所以改用上面这条命令行创建。

Linux 侧的兜底：每日服务质检新增 `shadow-core｜当日策略事件已执行` 与
`paper｜当日策略事件已执行`，直接读两边状态库，事件缺失或被记成「已过期，未补跑」
都会在服务管理页报出来。影子核心还会在连续失败 5 分钟后报一次告警，不再每秒刷屏。

## 重启后能自己起来的和起不来的（2026-09-27）

三个计划任务的触发方式与运行身份并不一致，决定了重启后各自的命运：

| 任务 | 运行身份 | 触发 | 重启后 |
|---|---|---|---|
| `qsys-market-gateway` | SYSTEM | 开机时 | ✅ 自动恢复 |
| `qsys-watchdog` | SYSTEM | 每 5 分钟 | ✅ 自动恢复 |
| `qsys-agent-shadow` | Administrator | 登录时 | ❌ 需有人登录桌面 |

miniQMT 更是必须有交互式桌面会话才能跑（`XtMiniQmt.exe "linkMini"`，实测在
`RDP-Tcp#N` 会话里），而执行代理连不上 miniQMT 就会直接退出：

```
xtquant_adapter.py:45  RuntimeError("miniQMT 连接失败")
```

于是重启后会出现这样一个**看似故障、实则只是没人登录**的现象：看门狗每 5 分钟拉起
一次代理，每次都失败，任务「上次结果: 1」，`agent.log` 里一串同样的连接失败。判断
方法是先看 miniQMT 在不在，而不是去查代理自身。维护者登录并起好 miniQMT 后，
`schtasks /run /tn \qsys-agent-shadow` 一次即恢复（2026-09-27 实测：07:17:55 启动成功，
心跳 0.2 秒，券商快照恢复刷新）。

已做的加固：给 miniQMT 建了登录自启快捷方式，登录桌面后不必再手工点开。

```
C:\Users\Administrator\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\miniQMT.lnk
→ XtMiniQmt.exe "linkMini"
```

**尚未解决**：该机没有配置自动登录，重启后仍需人工远程登录一次，无人值守恢复不了。
这是上线前必须补的一项（见 `docs/reference/live-readiness.md`）。要用 Sysinternals
Autologon 由维护者自行设置——它把密码加密存 LSA，不要改用注册表 `DefaultPassword`
那种明文写法。设好后应真做一次重启演练确认全链路自动恢复。

### 任务计划 WMI 接口损坏与修复

2026-09-19～26 这台机器上 `Get-ScheduledTask` 一直报 0x80070057、CIM 枚举返回 0 行，
根因是 `TaskCache\Tree` 里有 23 条孤儿任务登记（全是 Windows 自带任务）。清理孤儿、
`sfc /scannow`、`mofcomp` 重编 SchedProv.mof 都没能让接口恢复（Schedule 服务不允许
单独重启），**最终是重启 Windows 才修好**，之后可正常列出 135 个任务。

期间的影响面与应对：监控探测改用 `schtasks /query /fo CSV /v` 解析（PR #69），看门狗
脚本本身用 `Win32_Process` + `schtasks`，两者都不依赖该接口，所以功能未受影响，只是
服务管理页的在线时刻轴把看门狗显示成灰色。备份与全过程记录在
`/data/qsys/var-deployments/20260927-windows-taskcache-repair/`。

## 模拟交易实时行情网关

模拟盘使用独立的只读 QMT 进程，不需要资金账号，也不会加载下单适配器。
MiniQMT 和 Redis 可用后运行：

```powershell
uv run python scripts/run_market_gateway.py --config config/deploy.local.toml
```

进程会消费 `cmd:quote_requests` 并写入 `evt:quote_snapshots`，同时默认
在交易日 `09:00–11:30`、`13:00–15:30` 每 15 秒聚合一次指数与全市场温度，原子更新
`state:market_overview`。可用 `--overview-interval` 调整间隔，心跳键为
`state:heartbeat:market`。不要把它替换成 `run_agent.py`；后者属于真实券商链路。

### 部署实况与更新步骤（2026-09-17）

- 代码目录 `C:\qsys\paper_app`：**手工拷贝的源码快照，没有 git**，`PYTHONPATH=src`；
  配置 `config\deploy.local.toml` 不入库。
- 由计划任务 `\qsys-market-gateway` 启动 `run_market_gateway.cmd`，日志追加到 `market_gateway.log`。
- 网关每个交易日在 Linux 盘前任务发布 `state:price_limits:YYYYMMDD` 后，分批（500 只/批）
  用 `get_instrument_detail` 与 Tushare stk_limit 逐只核对，结果写
  `state:price_limits_check:YYYYMMDD`，行情页「涨跌停价核对」展示（D-19）。

### 执行代理与行情网关是两份源码（2026-10-06 澄清）

| 任务 | 目录 | 自带 src | 这次是否要停 |
|---|---|---|---|
| `qsys-agent-shadow` | `C:\qsys\live_app` | 有 | 要 |
| `qsys-market-gateway` | `C:\qsys\paper_app` | 有 | **不要** |

所以**只更新执行代理时不必停行情网关，不会有行情断流**。`live_app` 还有
`DEPLOYED_SHA` 记录当前部署的提交，更新时把旧值另存 `DEPLOYED_SHA.prev-<旧 sha>`。

**换文件前必须先停看门狗**：`qsys-watchdog` 每 5 分钟检查一次，进程不在就触发计划
任务，会在换到一半时把代理拉起来，跑起一份残缺的 `src`。顺序是
`schtasks /change /tn qsys-watchdog /disable` → 换 → `/enable`。这一条 2026-10-06
之前没写在文档里。

**换前换后都做逐文件哈希核对**（按 LF 归一后比对 `git ls-tree` 的内容哈希）：
换前确认机器上的 `src`/`scripts` 与 `DEPLOYED_SHA` 逐文件一致，证明没有脱离 git 的
本地改动；换后确认与目标提交逐文件一致，证明拷贝完整。2026-10-06 实测换前 266 个
py 文件对 `f7b6690` 零差异、换后 272 个对 `ddf7bc4` 零差异。

写 `DEPLOYED_SHA` 时**用 `git rev-parse` 取全长 SHA**，不要手敲——2026-10-06 就写错过
一次尾部，代码本体是对的，但那个标记文件会误导下一个接手的人。

更新网关代码时按这个顺序，不要只替换个别文件：

1. **先核对本地修改**：把 `src/`、`scripts/` 打包取回，按文件与 git 历史比对（忽略 CRLF）。
   任何文件对不上某个提交就停下，先把那份本地修改弄清楚。
2. **暂存验证**：`git archive <SHA> src scripts/run_market_gateway.py` 解压到
   `C:\qsys\daily\gateway_stage_<sha>`，用 venv 的 python 以暂存目录为 `sys.path` 导入
   `qsys.paper.quotes` 与网关脚本，确认本次改动的接口确实存在。
3. **替换**：`schtasks /end /tn qsys-market-gateway`，确认没有残留的 `run_market_gateway`
   python 进程；`src` 重命名为 `src.bak-<时间戳>`，网关脚本另存 `.bak-<时间戳>`；拷入暂存版本。
4. **重启与验证**：`schtasks /run /tn qsys-market-gateway`；在 Linux 侧确认
   `state:heartbeat:market` 刷新、`state:market_overview` 新发布、行情请求往返正常。

盘中重启会有数秒无行情；模拟盘可容忍 3 分钟内的断流（`run_paper.QUOTE_OUTAGE_LIMIT`）。
