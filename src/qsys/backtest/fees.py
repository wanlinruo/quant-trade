"""费用模型。

费率是已知确定的，这块能做到与实盘一模一样。默认值为 A 股常见档位：
佣金万三（最低 5 元）、印花税万五（2023-08-28 起减半，仅卖出）、
过户费十万分之一（双向）。请按自己的实际费率调整。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from qsys.core.trading import Side, to_cents


# 实盘专用账户的券商佣金（维护者 2026-09-23 确认）：成交额万 0.75，不免五，
# 即 max(成交额 × 0.0075%, 5 元)。"qsys 实盘档位"与实盘配置按此计费；
# 在此之前的回测按万三计，其 config.json 的 cost_model 标签可区分。
ACCOUNT_COMMISSION_RATE = 0.000075
ACCOUNT_MIN_COMMISSION = 5.0


class FeeModel(ABC):
    @abstractmethod
    def fee_for(self, side: Side, gross: float) -> float:
        """给定方向与成交金额，返回该笔应收费用。"""


class ZeroFeeModel(FeeModel):
    def fee_for(self, side: Side, gross: float) -> float:
        return 0.0


@dataclass(frozen=True, slots=True)
class AShareFeeModel(FeeModel):
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_duty_rate: float = 0.0005  # 仅卖出
    transfer_fee_rate: float = 0.00001  # 双向

    def fee_for(self, side: Side, gross: float) -> float:
        if gross < 0:
            raise ValueError(f"gross must be >= 0: {gross}")
        commission = max(gross * self.commission_rate, self.min_commission)
        transfer = gross * self.transfer_fee_rate
        stamp = gross * self.stamp_duty_rate if side is Side.SELL else 0.0
        return to_cents(commission + transfer + stamp)
