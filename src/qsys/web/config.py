"""监控台配置。

默认只监听 127.0.0.1 —— 通过 Tailscale / WireGuard 私有网络访问。
一个能看到持仓的页面不该出现在公网上。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WebConfig:
    host: str = "127.0.0.1"  # 绝不默认 0.0.0.0
    port: int = 8000
    title: str = "量化监控台"
    equity_days: int = 120
    watch_sources: tuple[str, ...] = ("agent", "core")
    heartbeat_max_age: float = 90.0
    account_key: str = "state:account"
    heartbeat_prefix: str = "state:heartbeat"
