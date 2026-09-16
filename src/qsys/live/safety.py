"""实盘写保护：模式、账户白名单与本地停机必须同时放行。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class TradingMode(StrEnum):
    DISABLED = "disabled"
    SHADOW = "shadow"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class SafetyVerdict:
    allowed: bool
    reason: str = ""


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
