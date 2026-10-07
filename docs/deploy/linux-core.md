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

> 当前标准生产发布入口见 [production-linux-release.md](production-linux-release.md)。
> 下方手工命令只保留为历史说明和只读应急参考，不再作为日常写入流程。

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

### 3. 同步代码（GitHub 为唯一中转）

日常发布链路固定为：Mac 开发机提交并推送 `main`，Linux 节点使用仓库专用的
**只读 deploy key** 从 GitHub 快进更新。节点不保存开发者个人私钥，也不能向
GitHub 推送。

```bash
# Mac：确认测试与工作区后推送
git status --short --branch
git push origin main

# Linux：只允许 fast-forward，节点有本地改动或历史分叉时立即失败
ssh -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144 \
  'cd /home/ubuntu/qsys && git fetch origin main && \
   git merge --ff-only origin/main'
```

节点仓库配置：

```bash
git remote set-url origin git@github.com:wanlinruo/quant-trade.git
git config --local core.sshCommand \
  'ssh -i /home/ubuntu/.ssh/id_ed25519_quant_trade -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new'
```

若 GitHub 临时不可达，才使用 `git bundle` 作为应急路径。也可以逐文件列出后用
`scp`，但**绝不整目录 rsync，更不能带 `--delete`**。
`/home/ubuntu/qsys/warehouse` 是指向 `/data/qsys/warehouse` 的软链接，`.venv`
是节点的 Linux 环境；整目录同步会覆盖或删除两者，这两种事故都发生过。

### 4. 装依赖并验证

```bash
cd ~/qsys && ./scripts/preflight_linux_deploy.sh
```

部署节点配置了腾讯镜像，而入库的 `uv.lock` 由 CI 使用官方 PyPI 生成。不能执行裸
`uv run`：它会按节点镜像重新锁定，把整份 lock 的 registry URL 改写并污染生产工作树。
统一 preflight 对 `uv sync` 和每次 `uv run` 都传 `--frozen`，并在前后拒绝脏工作树。

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
ReadWritePaths=/home/ubuntu/qsys /data/qsys

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now qsys-monitor
curl -s http://127.0.0.1:8420/healthz
```

服务健康时刻轴由独立采样器写入，网页只读本地 SQLite，正常路径不在请求中 SSH
Windows：

```bash
sudo cp deploy/systemd/qsys-health-observer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now qsys-health-observer
systemctl status qsys-health-observer --no-pager
```

采样器故障只会让时刻轴出现灰色缺测，不参与 `control:kill`，也不替代 LIVE 的固定
10 秒双向心跳检查。

## 访问监控台

应用**只监听回环** `127.0.0.1:8420`，公网只经 Nginx 8443 进入，并且必须带认证。

### 公网入口 `https://wanlinruo.cn:8443/`（2026-09-17 开通）

| 项 | 实况 |
|---|---|
| 站点文件 | `/etc/nginx/conf.d/qsys-8443.conf`（独立文件，不改 443 的 `site.conf`） |
| 证书 | 复用 `/etc/letsencrypt/live/wanlinruo.cn/`；仅 TLS 1.2 / 1.3 |
| 认证 | Nginx Basic Auth，用户 `qsys`，口令文件 `/etc/nginx/.htpasswd-qsys`（`root:www-data 640`） |
| 限流 | 每 IP 20 r/s、burst 60（`limit_req_zone qsys_8443`）——只限总频率，挡不住慢速猜口令 |
| 日志 | `/var/log/nginx/qsys-8443.{access,error}.log` |
| 响应头 | HSTS、`X-Frame-Options DENY`、`nosniff` |
| 云安全组 | TCP 8443 已放行（用户在腾讯云控制台操作） |
| 开通前备份 | `~/qsys-code-backups/nginx-before-8443-20260917.tar.gz` |

开通时外网实测：不带或错误口令 401；正确口令 `/healthz` 正常、首页 307 → `/market` 200，
跳转保持 https 与 8443；原 443 站点仍 302；8420 / 8421 公网不可达；TLS 1.1 握手被拒。

改口令（`htpasswd` 来自 `apache2-utils`，节点默认未装）：

```bash
PW=$(openssl rand -base64 36 | tr -dc A-Za-z0-9 | head -c 24)
printf "qsys:%s\n" "$(openssl passwd -apr1 "$PW")" | sudo tee /etc/nginx/.htpasswd-qsys >/dev/null
sudo nginx -t && sudo systemctl reload nginx
```

改 Nginx 任何配置前先 `sudo nginx -t`；443 上挂着企业微信回调，不能动 `site.conf`。

#### 证书续期（2026-09-27 改回全自动）

证书 `/etc/letsencrypt/live/wanlinruo.cn/` 同时服务 443 与 8443。2026-07-28 签发时
备案未通过、80 端口不可用，只能走 **DNS-01 人工续期**：钩子 `acme-dns-wait.sh` 要求
先 `touch /etc/letsencrypt/MANUAL_RENEW_OK` 再由人去 DNSPod 加 TXT 记录，否则直接失败。
于是 `certbot.timer` 每天两次自动续期必然失败——2026-09-26 进入续期窗口后，
`certbot.service` 就一直是 failed。

80 端口现已可用（Let's Encrypt staging 从海外实测 HTTP-01 验证成功），按配置里早就
预留的方案改回 **HTTP-01 webroot**：

```ini
# /etc/letsencrypt/renewal/wanlinruo.cn.conf
[renewalparams]
authenticator = webroot
webroot_path = /var/www/html,

[[webroot_map]]
wanlinruo.cn = /var/www/html
```

`site.conf` 里 80 段的 `location /.well-known/acme-challenge/ { root /var/www/html; }`
本就是为此预留的，不用改 Nginx。

**同时补了缺失的重载钩子** `/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh`：
原先一个钩子都没有，就算续期成功，Nginx 也会继续用内存里的旧证书直到下次重启——
这半个缺陷单看续期日志是发现不了的。钩子只在 `nginx -t` 通过时 reload，且吞掉
`nginx -t` 正常时写到 stderr 的两行（否则 certbot 会报「钩子有错误输出」）。

2026-09-27 实测：演练通过 → 真实续期成功 → 443 与 8443 当场换上新证书
（有效期至 2026-12-25）。旧配置与钩子备份在
`/data/qsys/var-deployments/20260927-certbot-http01/`，要回退就把两个 `.bak` 拷回去。

`acme-dns-wait.sh` 暂时留着没删，等这套跑过一个完整续期周期再清理。

**质检兜住它**：这次续期失败是巡检时偶然翻 `systemctl --failed` 才发现的，服务管理页
一点动静都没有——空了整整一天的监控盲区。已加质检项 `service-checks｜站点证书有效期`：
握手探 `127.0.0.1:8443`（带 `wanlinruo.cn` 作 SNI），读**实际提供出来的**那张证书，
剩余 < 21 天告警、< 10 天判失败。验提供出来的而不是磁盘上的文件，是因为「续期成功
但没重载」这半个缺陷只有前者看得见；握手照常校验证书链与域名，链断了也一起报。

### SSH 隧道（不经公网）

从开发机开 SSH 隧道：

```bash
ssh -N -L 8420:127.0.0.1:8420 -i ~/.ssh/qmt_bridge_ed25519 ubuntu@110.42.205.144
```

然后浏览器打开 `http://127.0.0.1:8420`。

### 同步后的指标验证

`qsys-tushare-sync` 在工作日 17:00 与 19:00 两次同步中，只有事实表与派生表
全部刷新成功后，才自动执行当日增量指标验证。结果追加到
`warehouse/metric_checks`，通过监控台 `/data/catalog` 查看；违规样本写入
`artifacts/metric_checks`，可从指标详情页下载。

验证规则失败或验证器自身崩溃**不改变同步任务的退出码**。数据已经成功落盘，
若因此让 systemd 把同步记成失败，`Persistent=true` 会重补已经完成的数据，
反而掩盖真正的验证问题。因此不要靠 `systemctl status` 判断指标是否通过；
应查看数据层页面，或直接查询 `metric_checks`。同步本身失败时不运行验证，避免
残缺数据产生的大量预期告警淹没同步故障。

若 service 使用 `ProtectSystem=strict`，`ReadWritePaths` 必须同时包含
`/home/ubuntu/qsys` 与 `/data/qsys`：前者保存验证样本，后者是 warehouse
软链接的实体位置。只写前者会在首次写验证结果时失败。

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
竞价 tick -> 算出缺哪些股-日 -> 驱动 Windows 下载
-> 回传 parquet -> 导入 -> 跨源校验
```

股本链路已于 D-16 结案：QMT 股本下载正式退出每日归档，旧 `share_capital`
表冻结保留为历史证据；规范 `share_capital_history` 由 Tushare 同步从
`daily_basic` 的每日股本快照压缩变化点生成。`effective_date` 表示新值首次可见的
交易日，`daily_basic` 没有公告日，因此 `announcement_date` 保持 null。

**本脚本只做 QMT 链路。** 日线、规范股本、指数与派生表（`adj_factors` /
`daily_flags` / `market_regime` / `valuation_snapshot` / `share_capital_history`）
归 `qsys-tushare-sync.timer`（每天 17:00 与 19:00），不在这里。

2026-09-15 删掉了这里的 mootdx 日线分支。mootdx 的 K 线接口 2026-09-10 起
对所有服务器返回空，探针每天如实报出「源失效」、脚本据此 `return 1`——
于是这个服务从换源那天起天天亮红灯（09-12/09-14/09-15 连续 `exit-code`），
而它的活其实干得好好的：竞价 tick、股本、分钟线、跨源校验每步都 `step.ok`、
`issues=0`。**红灯天天亮就不再有信息量**，`issues` 真的不为 0 的那天报的
还是同一个 exit-code，分辨不出来。现在退出码只反映 `issues`。

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

## 盘前涨跌停价（systemd timer）

`deploy/systemd/qsys-premarket-limits.{service,timer}`：交易日 08:58 触发
`scripts/tushare_premarket_limits.py`，取当日 Tushare `stk_limit`（官方约 09:00 更新），
每分钟重试到 09:25。完整才写 `price_limits_daily` 并发布 Redis `state:price_limits:YYYYMMDD`；
到点仍取不到以非零退出（`systemctl status` 可见）。模拟盘/影子盘/实盘的盘中涨停价、
行情页涨停统计都依赖它；缺价标的不自算（D-19）。

```bash
sudo cp deploy/systemd/qsys-premarket-limits.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now qsys-premarket-limits.timer
```

## 模拟交易运行器

Web 工作台只创建和控制账户，策略调度由独立进程负责：

```bash
uv run python scripts/run_paper.py \
  --warehouse warehouse \
  --workspace var/workbench \
  --config config/deploy.local.toml
```

运行器从 Tushare `trade_cal` 校验未来交易日，从 QMT 只读行情流取价，心跳键为
`state:heartbeat:paper`。它不构造 `LiveBroker`，也不写真实订单或控制流。生产环境
由 `qsys-paper.service` 守护。

**行情不可用时的行为**（2026-09-17 起）：

- 行情刷新与盘中权益只在交易日 09:15 ~ 15:30 进行；盘外不请求 QMT、不写权益。
- 网关超时或回报错误（`QuoteUnavailable`）先记 warning 并从库里重建 runtime 重试；
  **连续 3 分钟**仍不可用才把账户标为 `error`。其他任何异常仍立即标 `error`。
- 账户 `error` 后 supervisor 不会自动拉起，需在页面或
  `POST /api/paper-accounts/{id}/resume` 恢复。生效日按 `trade_cal` 取第一个完整交易日：
  09:15 前恢复当天生效，之后恢复顺延到下一个交易日（跳过节假日）。取不到交易日历时
  创建与恢复返回 503。
- 不要在盘中让同一台 QMT 批量下载历史 tick：2026-09-17 00:49 的超时与那次批量补采
  时间重合（未证实因果）。

## 重启后能自己回来的（2026-09-27 实测）

先看清楚一件容易想错的事：**Redis 不在这台机器上，它跑在 Windows 节点**，
Linux 侧靠 `qsys-redis-tunnel.service` 的 SSH 隧道把 `127.0.0.1:6379` 转过去
（`-L 127.0.0.1:6379:127.0.0.1:6379`）。所以「Redis 能不能恢复」问的其实是 Windows。

| 组件 | 怎么起的 | 重启后 |
|---|---|---|
| `/data/qsys` 数据盘 | fstab，`defaults,nofail` | ✅ |
| `qsys-monitor`、`qsys-web-test` | systemd，enabled | ✅ |
| `qsys-shadow-core`、`qsys-paper` | systemd，enabled | ✅ |
| `qsys-health-observer` | systemd，enabled | ✅ |
| `qsys-redis-tunnel` | systemd，enabled + `Restart=always` | ✅ |
| 7 个 timer（归档/同步/质检/研究等） | systemd，timer enabled、service disabled | ✅（这是 timer 驱动 oneshot 的正确形态） |
| Redis 本体（在 Windows） | Windows 服务，启动类型 Automatic | ✅ 实测 05:15:07 随 05:14:58 的重启一起回来 |
| nginx / docker / chrony | enabled | ✅ |

**不会回来的**：用 `systemd-run` 起的 transient 单元（临时观察用的
`qsys-summary-*`）。它们只活在内存里，重启即消失。对策是把脚本写成文件
（`~/scratch/summary.sh`），重启后只需重新挂定时器，不必从头重写命令。

22:38 实测重启验证：22:38:52 启动完成，22:39:07 影子核心就绪，**约 15 秒全部自动恢复**，无失败单元，上表逐项对得上。唯一损失就是那两个
transient 单元。记录在 `/data/qsys/var-deployments/20260927-linux-reboot-drill/`。

所以整条链路上唯一的无人值守恢复缺口，仍然只有 Windows 那边的 miniQMT 与执行代理
（见 `docs/deploy/windows-node.md`）。**控制面（Redis、行情网关、看门狗）能自动恢复，
断的只是下单执行这一段。**

## 发布记录

治理 §11 要求每次部署留记录。下方 #110 发生在生产 `workflow_dispatch` 建成之前，
因此没有自动回写的 GitHub deployment 状态，记录手工写在这里。#154 之后的发布以
GitHub Deployment、Actions Summary 和节点 `record.json` 为准。

### #110 数据域目录（2026-10-05 19:31）

| 项 | 值 |
|---|---|
| environment | 生产 |
| branch / PR | `main` / [#110](https://github.com/wanlinruo/quant-trade/pull/110) `test -> main` |
| 部署前 SHA（回滚点） | `a28ae1f5452638afdba217d5554d0a19f43b4a1f` |
| 部署后 SHA | `1e225b130dec8bbeba5430f161b88eb41759d818` |
| CI | 四项全绿（`policy` / `test` / `secret-scan` / `package`），全量 2437 passed |
| 依赖锁 | `uv sync --frozen` → `Checked 67 packages`，无漂移 |
| preflight | `tests/scripts/test_scripts_import.py` 23 项全过 |
| 重启范围 | **仅** `qsys-monitor.service`（纯 Web + 目录改动）。同步器、paper、shadow-core、health-observer、redis-tunnel 未动 |
| 健康检查 | 本机 `127.0.0.1:8420/healthz` → 200 `{"status":"ok"}`；公网 `https://wanlinruo.cn:8443/healthz` → 401（Basic Auth 在前，预期） |
| 页面实测 | `/data/domains` 200，合计 47 张表、七域齐全；`/data/tables/bar_minute_1m` 的「根源接口」显示「百度网盘付费源（余哈哈数据仓库）·minute」 |
| 自审 | 由 Claude 代维护者勾选，PR 正文中如实标注代行依据与两项未满足项 |
| 维护者验收 | 2026-10-05 线上自行验证通过 |

发布内容除目录本体外，还随 `test -> main` 带走了三份此前积压在 `test` 的纯文档
（ETF 日内探查、D-19 结案、本支 spec/plan）。`test -> main` 是整体发布，无法只带
一部分；不影响运行时。

部署后按 §3.1.1 立即把 `main` 对齐回 `test`（内容一致前提下 `--force-with-lease`），
核验 `origin/main...origin/test` 为 `0 0`。

## 待办

- [ ] **Web 应用层登录（TOTP）替换 Nginx Basic Auth**，并按用户做登录失败限流
- [x] 证书到期监控与续期演练（8443 复用 `wanlinruo.cn` 证书）——2026-09-27 完成：
      续期改回 HTTP-01 全自动、补上 Nginx 重载钩子、加质检项「站点证书有效期」
- [ ] 在**保留维护者密码登录**的前提下完成 #122 SSH 加固：禁 root、强化密码与失败限制、
      审计异常登录；不得设置 `PasswordAuthentication no`
- [ ] 轮换部署期间在对话中出现过的账号密码
- [ ] 内存扩容或为全 A 回测设计分批方案
- [ ] 手机访问方案（自建 WireGuard，需开 UDP 端口）
