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
    order_status: str = "evt:order_status"
    account: str = "state:account"
    heartbeat: str = "state:heartbeat"
    quote_requests: str = "cmd:quote_requests"
    quote_snapshots: str = "evt:quote_snapshots"
    market_snapshot: str = "state:market_overview"
    market_schedule: str = "state:market_schedule"


@dataclass(frozen=True, slots=True)
class FeeConfig:
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001


@dataclass(frozen=True, slots=True)
class SafetyConfig:
    mode: str = "disabled"
    orders_enabled: bool = False
    protected_account_ids: tuple[str, ...] = ()
    local_kill_path: str = "var/AGENT_KILL"


@dataclass(frozen=True, slots=True)
class AlertConfig:
    webhook_url: str = field(default="", repr=False)
    timeout_seconds: float = 5.0


@dataclass(frozen=True, slots=True)
class RiskConfig:
    enabled_capital: float = 10_000.0
    max_account_asset: float = 100_000.0
    max_position_weight: float = 0.05
    max_gross_exposure: float = 0.10
    max_order_value: float = 10_000.0
    max_daily_turnover: float = 1.0
    max_daily_loss: float = 0.02
    max_price_deviation: float = 0.02


@dataclass(frozen=True, slots=True)
class DeployConfig:
    redis: RedisConfig = field(default_factory=RedisConfig)
    streams: StreamConfig = field(default_factory=StreamConfig)
    fees: FeeConfig = field(default_factory=FeeConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
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
    raw = tomllib.loads(target.read_text(encoding="utf-8"))
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
    raw = tomllib.loads(target.read_text(encoding="utf-8"))
    agent = raw.get("agent", {})
    return DeployConfig(
        redis=RedisConfig(**raw.get("redis", {})),
        streams=StreamConfig(**raw.get("streams", {})),
        fees=FeeConfig(**raw.get("fees", {})),
        safety=SafetyConfig(
            **{
                **raw.get("safety", {}),
                "protected_account_ids": tuple(
                    raw.get("safety", {}).get("protected_account_ids", ())
                ),
            }
        ),
        alerts=AlertConfig(**raw.get("alerts", {})),
        risk=RiskConfig(**raw.get("risk", {})),
        consumer_group=agent.get("consumer_group", "core"),
        consumer_name=agent.get("consumer_name", "core-1"),
    )
