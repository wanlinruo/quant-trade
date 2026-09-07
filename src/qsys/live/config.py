"""部署配置。

真实地址只允许出现在 gitignored 的 deploy.local.toml——
公网 IP 固化进代码库是常见的隐患来源：仓库共享或公开时会一起泄露，
且地址变更时改动分散。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path("config/deploy.local.toml")
EXAMPLE_PATH = Path("config/deploy.example.toml")


@dataclass(frozen=True, slots=True)
class RedisConfig:
    host: str = "127.0.0.1"
    port: int = 6379
    password: str = field(default="", repr=False)  # 不进 repr / 日志
    tls: bool = False
    db: int = 0


@dataclass(frozen=True, slots=True)
class StreamConfig:
    orders: str = "cmd:orders"
    control: str = "cmd:control"
    fills: str = "evt:fills"
    rejects: str = "evt:rejects"
    account: str = "state:account"
    heartbeat: str = "state:heartbeat"


@dataclass(frozen=True, slots=True)
class DeployConfig:
    redis: RedisConfig = field(default_factory=RedisConfig)
    streams: StreamConfig = field(default_factory=StreamConfig)
    consumer_group: str = "core"
    consumer_name: str = "core-1"


def load_deploy_config(path: str | Path | None = None) -> DeployConfig:
    target = Path(path) if path is not None else DEFAULT_PATH
    if not target.exists():
        raise FileNotFoundError(
            f"{target} 不存在。请复制 {EXAMPLE_PATH} 为 {DEFAULT_PATH} 并填入真实地址。"
        )
    raw = tomllib.loads(target.read_text())
    agent = raw.get("agent", {})
    return DeployConfig(
        redis=RedisConfig(**raw.get("redis", {})),
        streams=StreamConfig(**raw.get("streams", {})),
        consumer_group=agent.get("consumer_group", "core"),
        consumer_name=agent.get("consumer_name", "core-1"),
    )
