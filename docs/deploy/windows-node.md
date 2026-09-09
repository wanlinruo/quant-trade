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

| 方式 | 耗时 | 全量估算 |
|---|---|---|
| 按股-日下载（冷） | 0.60 s/组 | 13,622 组 ≈ 2.3 小时 |
| **按标的整年下载（冷）** | **0.69 s/只** | **3,515 只 ≈ 0.7 小时** |

整年比单日还快——数据在服务端是现成的。故**下载按标的整年、导出只导候选日**：
整年 3,515 只共约 2 亿行，落 parquet 要几个 GB；候选日只有 330 万行、约 80 MB。

`download_history_data(code, '1m', start, end)` 中 `start == end` 亦可用，
但按标的整年更划算。

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

`Start-Process ... -WindowStyle Hidden` **不可靠**——SSH 会话结束时子进程会被
带走。长任务请让 SSH 会话保持前台，脚本本身可续传，断了重跑只补缺口。

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

## 待办

- [ ] **每日增量归档**——分钟线是滚动一年，不每天拉就永久丢失
- [ ] 关闭 SSH 密码认证（密钥已可用）
- [ ] 轮换部署期间在对话中出现过的账号密码
- [ ] 内存仅 1.0 GB 可用，若后续要在此跑更多任务需扩容
