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

## 待办

- [ ] 关闭 SSH 密码认证（`PasswordAuthentication no`）——公网 22 端口每天被扫数千次
- [ ] 轮换部署期间在对话中出现过的账号密码
- [ ] 内存扩容或为全 A 回测设计分批方案
- [ ] 手机访问方案（自建 WireGuard，需开 UDP 端口）
