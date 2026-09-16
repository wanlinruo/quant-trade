from qsys.live.safety import SafetyPolicy, TradingMode


def test_live_requires_all_three_write_guards(tmp_path):
    policy = SafetyPolicy(
        TradingMode.LIVE, account_id="A1", protected_account_ids=("A1",),
        orders_enabled=True, local_kill_path=tmp_path / "K",
    )
    assert policy.check().allowed is True


def test_shadow_can_never_place_real_orders(tmp_path):
    policy = SafetyPolicy(
        TradingMode.SHADOW, account_id="A1", protected_account_ids=("A1",),
        orders_enabled=True, local_kill_path=tmp_path / "K",
    )
    assert policy.check().allowed is False


def test_local_kill_overrides_live_mode(tmp_path):
    policy = SafetyPolicy(
        TradingMode.LIVE, account_id="A1", protected_account_ids=("A1",),
        orders_enabled=True, local_kill_path=tmp_path / "K",
    )
    policy.engage_local_kill("network failure")
    assert policy.check().allowed is False
    assert policy.check(reducing=True).allowed is True
    policy.release_local_kill()
    assert policy.check().allowed is True


def test_shadow_cannot_trade_even_when_order_is_reducing(tmp_path):
    policy = SafetyPolicy(
        TradingMode.SHADOW, account_id="A1", protected_account_ids=("A1",),
        orders_enabled=True, local_kill_path=tmp_path / "K",
    )
    assert policy.check(reducing=True).allowed is False
