"""部署配置。

真实地址只允许出现在 gitignored 的 deploy.local.toml——
公网 IP 固化进代码库是常见的隐患来源：仓库共享或公开时会一起泄露，
且地址变更时改动分散。
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
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
    quote_latest: str = "state:quote_realtime_latest"
    market_snapshot: str = "state:market_overview"
    market_schedule: str = "state:market_schedule"
    # 盘前发布的当日 stk_limit，键后缀 YYYYMMDD（data.pipeline.premarket）
    price_limits: str = "state:price_limits"


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
    # 策略消息（影子/实盘的买卖、竞价决策、出错、收盘）
    webhook_url: str = field(default="", repr=False)
    # 运维消息（服务与数据故障）。单独一个机器人：运维要立刻看，
    # 混在策略消息里会被每日决策淹掉。不填则退回 webhook_url。
    ops_webhook_url: str = field(default="", repr=False)
    timeout_seconds: float = 5.0

    @property
    def ops_url(self) -> str:
        return self.ops_webhook_url or self.webhook_url


@dataclass(frozen=True, slots=True)
class EmailConfig:
    """每日汇报的发件账号。口令只写节点本地配置，不入库、不进仓库。"""

    host: str = ""
    port: int = 465
    username: str = ""
    password: str = field(default="", repr=False)
    sender: str = ""          # 不填则用 username
    recipients: tuple[str, ...] = ()
    use_ssl: bool = True      # 465 用 SSL，587 用 STARTTLS
    timeout_seconds: float = 20.0

    @property
    def configured(self) -> bool:
        return bool(self.host and self.username and self.recipients)

    @property
    def from_address(self) -> str:
        return self.sender or self.username


@dataclass(frozen=True, slots=True)
class RiskConfig:
    enabled_capital: float = 10_000.0
    # 账户总资产上限：0 表示不限制（默认）。真正卡住敞口的是 enabled_capital——
    # 策略能看到的现金被压到 enabled_capital - 已部署（runtime._context），
    # 账户里有多少钱都只能动用这么多。而账户本身会因为转入资金、创业板资产门槛、
    # 日后盈利自然长大，用它当闸门只会误伤（2026-09-28 维护者为创业板权限转入
    # 5 万后，门禁立刻因超 10 万而失败）。
    max_account_asset: float = 0.0
    max_position_weight: float = 0.05
    max_gross_exposure: float = 0.10
    max_order_value: float = 10_000.0
    max_daily_turnover: float = 1.0
    max_daily_loss: float = 0.02
    max_price_deviation: float = 0.02


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    """影子核心/实盘核心跑哪套策略。

    `follows_paper_account` 指向一个模拟账户：源码与参数都从它存档的版本读，
    而不是在这里重填一遍——"回测 → 模拟账户 → 影子盘"一条链承袭下来，
    三者口径不会各自漂移。留空则用 `file` 与 `params`（老行为）。
    """

    file: str = "strategies/structural_v1_2.py"
    params: Mapping[str, object] = field(default_factory=dict)
    follows_paper_account: str = ""
    workbench: str = "var/workbench/workbench.sqlite3"


def _email(raw: dict) -> EmailConfig:
    """收件人写成 TOML 数组或逗号分隔的字符串都认。"""
    data = dict(raw)
    people = data.get("recipients", ())
    if isinstance(people, str):
        people = [x.strip() for x in people.split(",") if x.strip()]
    data["recipients"] = tuple(people)
    return EmailConfig(**data)


@dataclass(frozen=True, slots=True)
class DeployConfig:
    redis: RedisConfig = field(default_factory=RedisConfig)
    streams: StreamConfig = field(default_factory=StreamConfig)
    fees: FeeConfig = field(default_factory=FeeConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
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
        email=_email(raw.get("email", {})),
        risk=RiskConfig(**raw.get("risk", {})),
        strategy=StrategyConfig(**{
            **raw.get("strategy", {}),
            "params": dict(raw.get("strategy", {}).get("params", {})),
        }),
        consumer_group=agent.get("consumer_group", "core"),
        consumer_name=agent.get("consumer_name", "core-1"),
    )
