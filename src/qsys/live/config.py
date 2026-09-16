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
    quote_requests: str = "cmd:quote_requests"
    quote_snapshots: str = "evt:quote_snapshots"


@dataclass(frozen=True, slots=True)
class DeployConfig:
    redis: RedisConfig = field(default_factory=RedisConfig)
    streams: StreamConfig = field(default_factory=StreamConfig)
    consumer_group: str = "core"
    consumer_name: str = "core-1"


@dataclass(frozen=True, slots=True)
class TushareConfig:
    """Tushare Pro 凭据。

    **积分一年有效期、到期回收**——到期未续费则日线源直接中断。
    `purchased` 留作到期提醒的依据。
    """

    token: str = field(default="", repr=False)  # 不进 repr / 日志 / 异常
    points: int = 0
    purchased: str = ""


def load_tushare_config(path: str | Path | None = None) -> TushareConfig:
    target = Path(path) if path is not None else DEFAULT_PATH
    if not target.exists():
        raise FileNotFoundError(f"{target} 不存在。Tushare 凭据只写在这里（已 gitignore）。")
    raw = tomllib.loads(target.read_text())
    section = raw.get("tushare")
    if not section or not section.get("token"):
        # 显式报错而不是给个空 token——空 token 要到网络层才暴露，
        # 那时错误信息是「token 无效」，查起来绕远路。
        raise ValueError(f"{target} 里没有 [tushare] token")
    return TushareConfig(
        token=section["token"],
        points=int(section.get("points", 0)),
        purchased=str(section.get("purchased", "")),
    )


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
