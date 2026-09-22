import pytest

from qsys.backtest.fees import AShareFeeModel, ZeroFeeModel
from qsys.core.trading import Side


def test_zero_fee_model_charges_nothing():
    assert ZeroFeeModel().fee_for(Side.BUY, 100_000.0) == 0.0
    assert ZeroFeeModel().fee_for(Side.SELL, 100_000.0) == 0.0


def test_buy_fee_is_commission_plus_transfer():
    assert AShareFeeModel().fee_for(Side.BUY, 100_000.0) == 31.00


def test_sell_fee_adds_stamp_duty():
    assert AShareFeeModel().fee_for(Side.SELL, 100_000.0) == 81.00


def test_minimum_commission_applies():
    assert AShareFeeModel().fee_for(Side.BUY, 1000.0) == 5.01


def test_minimum_commission_not_applied_above_threshold():
    fee = AShareFeeModel().fee_for(Side.BUY, 20_000.0)
    assert fee == pytest.approx(6.20, abs=0.005)


def test_stamp_duty_is_sell_only():
    m = AShareFeeModel()
    assert m.fee_for(Side.SELL, 100_000.0) - m.fee_for(Side.BUY, 100_000.0) == 50.00


def test_rates_are_configurable():
    m = AShareFeeModel(
        commission_rate=0.0001, min_commission=0.0, stamp_duty_rate=0.0, transfer_fee_rate=0.0
    )
    assert m.fee_for(Side.SELL, 100_000.0) == 10.00


def test_fee_is_rounded_to_cents():
    m = AShareFeeModel(commission_rate=0.000333, min_commission=0.0, transfer_fee_rate=0.0,
                       stamp_duty_rate=0.0)
    assert m.fee_for(Side.BUY, 1000.0) == 0.33


def test_zero_gross_still_charges_minimum_commission():
    assert AShareFeeModel().fee_for(Side.BUY, 0.0) == 5.00


def test_negative_gross_rejected():
    with pytest.raises(ValueError, match="gross must be >= 0"):
        AShareFeeModel().fee_for(Side.BUY, -1.0)


def test_qsys_cost_tier_uses_the_account_commission_without_the_five_yuan_waiver():
    """专用账户：万 0.75、不免五（2026-09-23）。小单被最低 5 元抬高。"""
    from qsys.backtest.runner import cost_model
    from qsys.core.trading import Side

    fees, _slippage, label = cost_model(False)
    # 1 万元买入：0.75 元佣金按 5 元收 + 过户费 0.10
    assert fees.fee_for(Side.BUY, 10_000.0) == 5.10
    # 20 万元卖出：佣金 15 + 过户费 2 + 印花税 100
    assert fees.fee_for(Side.SELL, 200_000.0) == 117.0
    assert "万0.75最低5元" in label
