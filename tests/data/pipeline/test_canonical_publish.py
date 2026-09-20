from datetime import date

import polars as pl
import pytest

from qsys.data.pipeline import canonical_publish as publish
from qsys.data.pipeline.canonical_minute import MinuteBuildResult
from qsys.data.pipeline.canonical_reference import Reconciliation


DAY = date(2026, 9, 18)
MATCH = Reconciliation(1, 1, (), (), ())


def test_premarket_publishes_and_requires_reconciliation(monkeypatch, warehouse):
    monkeypatch.setattr(publish, "build_price_limit_daily", lambda wh, lo, hi: 5)
    monkeypatch.setattr(
        publish, "state_reconciliation",
        lambda wh, lo, hi: {"price_limit_daily": MATCH},
    )
    result = publish.publish_premarket(warehouse, DAY)
    assert result.counts == {"price_limit_daily": 5}
    assert result.reconciled == ("price_limit_daily",)


def test_premarket_fails_closed_on_difference(monkeypatch, warehouse):
    mismatch = Reconciliation(1, 1, (), (), (("000001.SZ", DAY),))
    monkeypatch.setattr(publish, "build_price_limit_daily", lambda wh, lo, hi: 1)
    monkeypatch.setattr(
        publish, "state_reconciliation",
        lambda wh, lo, hi: {"price_limit_daily": mismatch},
    )
    with pytest.raises(RuntimeError, match="对账失败"):
        publish.publish_premarket(warehouse, DAY)


def test_postclose_orders_reference_daily_then_state(monkeypatch, warehouse):
    events = []
    monkeypatch.setattr(publish, "require_reference_sources", lambda wh: events.append("pre-ref"))
    monkeypatch.setattr(
        publish, "require_calendar_coverage",
        lambda raw, lo, hi, market="SSE": events.append("pre-cal"),
    )
    monkeypatch.setattr(
        publish, "require_canonical_daily_sources",
        lambda wh, lo, hi: events.append("pre-daily"),
    )
    monkeypatch.setattr(
        publish, "require_canonical_state_sources",
        lambda wh, lo, hi: events.append("pre-state"),
    )
    builders = (
        "build_security_master", "build_security_name_history",
        "build_bar_daily", "build_valuation_daily",
        "build_share_capital_history", "build_adjustment_factor_daily",
        "build_price_limit_daily", "build_security_status_daily",
        "build_market_flags_daily", "build_market_regime_daily",
    )
    for name in builders:
        monkeypatch.setattr(
            publish, name,
            lambda *args, _name=name, **kwargs: events.append(_name) or 1,
        )
    monkeypatch.setattr(
        publish, "publish_trading_calendar",
        lambda *args, **kwargs: events.append("publish_trading_calendar") or 1,
    )
    monkeypatch.setattr(
        publish, "reference_reconciliation",
        lambda wh: {"security_master": MATCH, "security_name_history": MATCH},
    )
    monkeypatch.setattr(publish, "calendar_reconciliation", lambda *a, **k: MATCH)
    monkeypatch.setattr(
        publish, "daily_reconciliation",
        lambda *a, **k: {name: MATCH for name in (
            "bar_daily", "valuation_daily", "share_capital_history",
            "adjustment_factor_daily",
        )},
    )
    monkeypatch.setattr(
        publish, "state_reconciliation",
        lambda *a, **k: {name: MATCH for name in (
            "price_limit_daily", "security_status_daily", "market_flags_daily",
            "market_regime_daily",
        )},
    )
    monkeypatch.setattr(
        publish.WarehouseQuery, "trading_calendar",
        lambda *a, **k: pl.DataFrame(),
    )
    result = publish.publish_postclose(warehouse, pl.DataFrame(), DAY)
    assert events.index("pre-state") > events.index("build_adjustment_factor_daily")
    assert events.index("build_price_limit_daily") > events.index("pre-state")
    assert len(result.reconciled) == 11


def test_minute_publish_reports_rows(monkeypatch, warehouse):
    monkeypatch.setattr(
        publish, "build_bar_minute_1m",
        lambda wh, lo, hi: MinuteBuildResult(1, 241, 1, 0, {"qmt": 241}),
    )
    result = publish.publish_minutes(warehouse, DAY, DAY)
    assert result.counts == {"bar_minute_1m": 241}
