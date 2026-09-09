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
**必须从现在开始每日增量归档**。2025-09-09 之前的已经找不回来了。

### 四、吞吐

| 方式 | 耗时 | QMT 缓存占用 |
|---|---|---|
| **按股-日下载**（默认） | 1.08 s/只 | **零增长** |
| 按标的整年下载 | 0.69 s/只 | **4.5 MB/只 → 3515 只约 16 GB** |

整年下载单看速度更快，**但会撑爆磁盘**：QMT 把下到的一切留在
`C:\self\...\userdata_mini`，实测 1056 只就占了 4.8 GB，而全盘只剩 15.9 GB。
这台机器跑着实盘通道，磁盘写满会一起出事。

我们只需要 13,622 个股-日，整年是 3515 × 242 = 850,000 个股-日——**要的只占
1.6%**。故 `win_download_minutes.py` 默认按股-日下载，`--whole-window`
可切回整年（磁盘宽裕时才用）。

**教训**：选下载策略时只比了时间没比磁盘。在这台机器上磁盘才是约束。

## 采集流程

```powershell
# 1) Linux 侧导出清单，传到 Windows
#    uv run python scripts/export_minute_manifest.py \
#        --start 2025-09-09 --end 2026-09-08 --out var/minute_manifest.csv

# 2) Windows 下载并导出 parquet（可中断可续传，done.csv 记账）
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

### 四、体量

单只单日约 5,000 笔 / 下载 1.1~1.2 s。全市场 × 242 天约 5.3 TB，
**全量不可行**；只采候选股-日的竞价段（每笔仅需时刻/bid1/ask1/量），
量可忽略。

## 待办

- [ ] **每日增量归档**——分钟线是滚动一年，不每天拉就永久丢失
- [ ] **每日归档竞价 tick**——只有 20 个交易日的窗口，比分钟线急得多
- [ ] 关闭 SSH 密码认证（密钥已可用）
- [ ] 轮换部署期间在对话中出现过的账号密码
- [ ] 内存仅 1.0 GB 可用，若后续要在此跑更多任务需扩容
