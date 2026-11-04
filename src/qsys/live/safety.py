"""实盘写保护：模式、账户白名单与本地停机必须同时放行。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import re


class TradingMode(StrEnum):
    DISABLED = "disabled"
    SHADOW = "shadow"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class SafetyVerdict:
    allowed: bool
    reason: str = ""


def check_order_market(symbol: str, side: str) -> SafetyVerdict:
    """当前策略禁止北交所新开仓；行情可见性不构成交易许可（#589）。

    本函数不访问券商，也不改变已有持仓/撤单。规范北交所身份及已知北交所
    号段均拒绝买入，避免改错后缀绕过；无法识别的身份不默认当主板。
    合法卖单继续由持仓、可卖量、停牌等既有约束验证，保留风险处置路径。
    """
    if side not in {"buy", "sell"} or not isinstance(symbol, str):
        return SafetyVerdict(False, "market_access: invalid order identity")
    match = re.fullmatch(r"([0-9]{6})\.(SH|SZ|BJ)", symbol)
    if match is None:
        return SafetyVerdict(False, "market_access: unknown security identity")
    code, market = match.groups()
    if side == "buy" and (market == "BJ" or code.startswith(("4", "8", "92"))):
        return SafetyVerdict(False, "market_access: BSE opening orders are disabled")
    return SafetyVerdict(True)


class SafetyPolicy:
    def __init__(
        self,
        mode: TradingMode,
        *,
        account_id: str,
        protected_account_ids: tuple[str, ...] = (),
        orders_enabled: bool = False,
        local_kill_path: str | Path | None = None,
    ) -> None:
        self.mode = mode
        self.account_id = account_id
        self.protected = frozenset(protected_account_ids)
        self.orders_enabled = orders_enabled
        self.local_kill_path = Path(local_kill_path) if local_kill_path else None

    def check(self, *, reducing: bool = False) -> SafetyVerdict:
        if self.mode is not TradingMode.LIVE:
            return SafetyVerdict(False, f"trading mode is {self.mode.value}")
        if not self.orders_enabled:
            return SafetyVerdict(False, "orders_enabled is false")
        if self.account_id not in self.protected:
            return SafetyVerdict(False, "account is not in protected_account_ids")
        if self.local_kill_path and self.local_kill_path.exists() and not reducing:
            return SafetyVerdict(False, "Windows local kill file is engaged")
        return SafetyVerdict(True)

    def engage_local_kill(self, reason: str) -> None:
        if self.local_kill_path is None:
            return
        self.local_kill_path.parent.mkdir(parents=True, exist_ok=True)
        self.local_kill_path.write_text(reason, encoding="utf-8")

    def release_local_kill(self) -> None:
        if self.local_kill_path and self.local_kill_path.exists():
            self.local_kill_path.unlink()
