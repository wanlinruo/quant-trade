# Linux 核心节点部署记录

- **主机**：`110.42.205.144`（腾讯云 · `VM-0-16-ubuntu`）
- **首次部署**：2026-09-08
- **登录**：`ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144`

## 机器实况

```
Ubuntu 24.04.4 LTS · x86_64 · 2 核 / 1.9 GB 内存 / 40 GB 磁盘
时区 Asia/Shanghai · sudo NOPASSWD
```

**这台机器不是空的**，还跑着复盘子系统：

| 端口 | 进程 | 暴露 |
|---|---|---|
| 80 / 443 | nginx | 公网 |
| 3018 | docker-proxy | 公网 |
| 8000 / 8001 | waitress-serve | 仅回环 |
| 9222 | chrome（DevTools） | 仅回环 |
| **8420** | **qsys 监控台** | **仅回环** |

## 两条硬约束

**① 内存 1.9 GB 是真正的瓶颈**（可用约 870 MB，swap 已用 1.7 GB）。
日线小样本没问题；**全 A 十年回测大概率 OOM**。扩容或分批处理是前置条件。

**② 境外网络不可用**：
- GitHub releases ≈ **20 KB/s**（uv 二进制 18 MB 要 16 分钟）
- `pkgs.tailscale.com` ≈ **508 B/s**，`login.tailscale.com` ≈ **51 B/s** → **Tailscale 不可行**
- 腾讯内网镜像 ≈ **122 KB/s**，`uv sync` **7 秒**完成

一切依赖必须走国内镜像。

## 部署步骤

### 1. 镜像配置（必须最先做）

```bash
mkdir -p ~/.pip ~/.config/uv
cat > ~/.pip/pip.conf <<'EOF'
[global]
index-url = https://mirrors.cloud.tencent.com/pypi/simple
trusted-host = mirrors.cloud.tencent.com
timeout = 60
EOF
cat > ~/.config/uv/uv.toml <<'EOF'
[[index]]
name = "tencent"
url = "https://mirrors.cloud.tencent.com/pypi/simple"
default = true
EOF
```

### 2. 安装 uv

Ubuntu 24.04 的 PEP 668 会拒绝 `pip install --user`。
**不要用 `--break-system-packages`**——那是拿系统 Python 冒险。用独立 venv：

```bash
python3 -m venv ~/.uvenv
~/.uvenv/bin/pip install uv
mkdir -p ~/.local/bin
ln -sf ~/.uvenv/bin/uv  ~/.local/bin/uv
ln -sf ~/.uvenv/bin/uvx ~/.local/bin/uvx
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

### 3. 同步代码（从开发机执行）

```bash
rsync -az --delete \
  --exclude '.venv/' --exclude '__pycache__/' --exclude '.pytest_cache/' \
  --exclude 'warehouse/' --exclude 'var/' --exclude '*.pyc' \
  -e "ssh -i ~/.ssh/qmt_bridge_ed25519" \
  ./ ubuntu@110.42.205.144:/home/ubuntu/qsys/
```

### 4. 装依赖并验证

```bash
cd ~/qsys && uv sync && uv run pytest -q
```

### 5. 初始化数据仓库

```bash
cd ~/qsys && uv run python scripts/bootstrap_warehouse.py
```

数据源在这台机器上延迟 9–31 ms（开发机上是几百毫秒），bootstrap 约 12 秒。

### 6. 监控台服务

`/etc/systemd/system/qsys-monitor.service`：

```ini
[Unit]
Description=qsys 只读监控台
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/qsys
# 直接用 venv 解释器。用 `uv run` 会因 ProtectHome=read-only 挡住 uv 缓存而启动失败。
ExecStart=/home/ubuntu/qsys/.venv/bin/python scripts/run_monitor.py \
          --host 127.0.0.1 --port 8420 --warehouse /home/ubuntu/qsys/warehouse
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/ubuntu/qsys

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now qsys-monitor
curl -s http://127.0.0.1:8420/healthz
```

## 访问监控台

**只监听回环**——这个页面能看到全部持仓，绝不上公网。

从开发机开 SSH 隧道：

```bash
ssh -N -L 8420:127.0.0.1:8420 -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144
```

然后浏览器打开 `http://127.0.0.1:8420`。

**手机访问**：Tailscale 在这台机器上不可行（51 B/s）。备选是自建 WireGuard——
需要在腾讯云安全组放行一个 UDP 端口。尚未实施。

## 验证结论（2026-09-08）

| 项 | 结果 |
|---|---|
| 全量测试 | 647 passed，与开发机一致 |
| 数据仓库 | 117 交易日 / 351 日线 / 63 除权事件，`quality: clean` |
| **回测可复现** | `run_id=5bd68d99330b2f26`、净值 `1,054,143.08`、夏普 `0.844` —— **与开发机位级相同** |
| 监控台 | systemd active + enabled，`/healthz` 返回 ok |

回测在两台不同机器上得到完全相同的结果，验证了 spec §8.4 的可复现要求。

## 全市场采集实测（2026-09-09）

`scripts/ingest_universe.py --start 2023-01-01 --end 2026-09-09 --batch-size 500 --skip-valuation`

| 项 | 实测 |
|---|---|
| 耗时 | **6 分 22 秒**（采集 ~19 只/秒，网络受限；CPU 占 64%） |
| 结果 | **4,504,869 行日线 / 5217 只 / 894 个交易日**，0 失败 |
| 派生 | daily_flags 4,504,869 行；market_regime 894 行 |
| 磁盘 | bars_daily 82 MB + daily_flags 33 MB = **115 MB** |
| **峰值内存** | **1.04 GB**，而当时可用仅 ~677 MB —— **已进入 swap** |

外部印证：涨停家数最多的三天是 2024-09-30（817 家，涨占比 99.8%）、
2024-10-08（777 家）、2024-02-08（530 家），与真实行情吻合。
炸板数恒等于触板数减涨停数，逐日精确。唯一涨停为 0 的日子是区间首日
2023-01-03（无前收），符合设计。

**内存是已经触顶的约束**，不是将来的风险：日线采集靠 swap 撑过去了，
全市场回测与分钟线不会这么走运。

**估值接口在本机同样不可用**：`stock_zh_a_spot_em` 三次重试全部
ConnectionError（东财主动断连），与开发机的 ProxyError 是两种表现、
同一结果。估值步骤已降级为可失败，不阻断日线。

## 每日归档（systemd timer）

**为什么必须每天跑**：QMT 分钟线只保留约一年且是滚动窗口，每过一天就
永久丢一天，漏跑的日子事后补不回来。

`scripts/daily_archive.py` 在 Linux 上编排整条链路，通过 SSH 驱动 Windows：

```
日线增量 -> 派生表重算 -> 算出缺哪些股-日 -> 驱动 Windows 下载
-> 回传 parquet -> 导入 -> 跨源校验
```

**一个编排者、一份日志、一处看失败**——两边各自定时就是把「多系统拼接
脆弱」这个老毛病请回来。

### 前置：Linux 到 Windows 的免密登录

```bash
ssh-keygen -t ed25519 -N "" -C "qsys-linux-to-winnode" -f ~/.ssh/id_ed25519_winnode
# 公钥追加到 Windows 的 C:\ProgramData\ssh\administrators_authorized_keys
# （Administrators 组成员用的是这个文件，不是 ~/.ssh/authorized_keys）
ssh -i ~/.ssh/id_ed25519_winnode Administrator@<win> "echo OK"
```

### 单元与定时器

`/etc/systemd/system/qsys-daily-archive.service` + `.timer`，
`OnCalendar=Mon..Fri 15:40 Asia/Shanghai`，`Persistent=true`
（机器停过则开机后补跑）。

`ProtectHome=read-only` 仍允许读 `~/.ssh` 里的私钥去驱动 Windows——
已实跑验证。

```bash
sudo systemctl enable --now qsys-daily-archive.timer
systemctl list-timers qsys-daily-archive --no-pager
sudo journalctl -u qsys-daily-archive.service -n 50 --no-pager
```

### 实跑验证（2026-09-09）

| 步骤 | 结果 |
|---|---|
| 日线增量 | 5,217 只，0 失败 |
| 候选计算 | 308 组，**其中只缺 7 组**（增量生效，不重复下载） |
| 驱动 Windows 下载 | 5.9 秒 |
| 导入 | 15,665 行 / 0 错误 |
| 跨源校验 | 2 天，**0 问题** |
| 总耗时 | 5.7 分钟（CPU 4 分钟） |

退出码非 0 表示跨源校验发现问题，systemd 会记为失败。


## 每日归档的实测节奏

定时任务单独跑（2026-09-09 15:40，`--lookback-days 8`）：

```
全市场日线增量   约 6 分钟   5,217 只，500 只 / 35 秒
派生表重算       约 10 秒
分钟线补齐       视缺口
竞价 tick        约 24 秒    137 只 / 17,224 笔
全程             5.7 ~ 9.1 分钟
```

**这台机器只有 4 核，归档时尽量别跑回测。**

> **2026-09-12 订正**：上面这条曾被当成 2026-09-10 归档超时的原因，是错的。
> 那两天（09-10、09-11）归档在 7200 秒被杀、停在 1500/5219 只，真正的原因是
> **mootdx / 通达信免费行情的 K 线接口在上游失效**，不是 CPU 争抢。证据：
>
> - 两次失败的 `Consumed CPU time` 分别只有 **45.2 秒**和 **48.0 秒**——进程在
>   两小时里几乎没用 CPU，是在等网络，CPU 争抢不会是这个样子；
> - 09-12 复现：每次 `fetch_daily_bars` 恒定等 **3.8 秒**返回**空表**，
>   5219 只要 5.5 小时，必然超过 2 小时的 `TimeoutStartSec`；
> - 配置里 38 台服务器逐台实测：14 台 TCP 可连，`get_security_count`、
>   `get_security_list` 都正常返回，但 `get_security_bars`、`get_index_bars`、
>   `get_security_quotes` **全部返回 None**；
> - 开发机（本地宽带）与云主机两个网络、三台服务器上表现一致——不是本机
>   网络问题，也不是云主机 IP 被封，是上游把 K 线接口关了。
>
> 结论：**mootdx 不再是可用的日线来源**，换源是唯一出路（`paid_daily` 已覆盖
> 到 2026-09-11）。`TimeoutStartSec` 调大没有意义。

**竞价 tick 排在最前**：它的窗口只有约 20 个交易日，日线滚动一年、
分钟线滚动一年。万一后面的步骤挂了或超时被杀，先保住不可恢复的那份。
此时 `daily_flags` 还没有当天，故 `candidate_stock_days` 显式传入
akshare 的交易日历。

时间敏感的那一步要能单独重试：

```bash
uv run python scripts/daily_archive.py --only-auction
```

## 待办

- [ ] 关闭 SSH 密码认证（`PasswordAuthentication no`）——公网 22 端口每天被扫数千次
- [ ] 轮换部署期间在对话中出现过的账号密码
- [ ] 内存扩容或为全 A 回测设计分批方案
- [ ] 手机访问方案（自建 WireGuard，需开 UDP 端口）
